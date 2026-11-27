"""Cas d'usage de l'atelier de révision : niveaux, fiches, QCM, carnet de notes, exercices."""
from __future__ import annotations

import json
import random
import time
from urllib.parse import urlparse

import requests
from flask import current_app

from backend.business import extractor
from backend.business.resource_rules import LEVEL_QUERY
from backend.business.subjects import topics_of
from backend.dao import documents as documents_dao
from backend.dao import resources as resources_dao
from backend.dao import subjects as subjects_dao
from backend.services.common import owned
from backend.services.report import Report

from . import generator as gen
from . import sources, store

MIN_QUESTIONS = 3
WIKI_RETRY_SECONDS = 600
_wiki_failed: dict[int, float] = {}  # matière -> heure du dernier échec (évite d'attendre le réseau à chaque page)


def web_enabled() -> bool:
    return bool(current_app.config.get("ENABLE_WEB"))


def subject(user_id, subject_id):
    return owned(subjects_dao.get_owned(subject_id, user_id))


# ---------------------------------------------------------------- niveau de difficulté
def levels(subject_id) -> dict[str, str]:
    """{'': niveau de la matière, '<chapitre>': niveau propre au chapitre}. Vide si jamais demandé."""
    return store.difficulties(subject_id)


def difficulty_of(lv: dict, chapter: str = "") -> str:
    return lv.get(chapter) or lv.get("") or "moyen"


def set_levels(s, form) -> Report:
    main = form.get("difficulte")
    if main not in gen.DIFFICULTIES:
        return Report().error("Choisis un niveau de difficulté pour cette matière.")
    values = {"": main}
    for i, topic in enumerate(topics_of(s)):
        value = form.get(f"chap_{i}", "")
        if value in gen.DIFFICULTIES and value != main:
            values[topic] = value
    store.set_difficulties(s["id"], values)
    extra = len(values) - 1
    return Report().success(f"Niveau « {gen.DIFFICULTIES[main]} » enregistré pour {s['name']}"
                            + (f", {extra} chapitre(s) réglé(s) à part." if extra else "."))


# ---------------------------------------------------------------- texte du cours
def course_text(s) -> tuple[str, str]:
    """(texte, origine). Les supports déposés d'abord ; Wikipédia en secours s'il n'y a presque rien."""
    sid = s["id"]
    cached = store.corpus(sid)
    docs = {f"doc:{d['id']}": d for d in documents_dao.list_for_subject(sid)}
    for key in list(cached):
        if key.startswith("doc:") and key not in docs:
            store.drop_corpus(sid, key)
    parts = []
    for key, d in docs.items():
        if key not in cached:
            text, _ = extractor.extract(d["stored_path"])
            cached[key] = text or d["excerpt"]
            store.save_corpus(sid, key, cached[key])
        parts.append(cached[key])
    body = "\n".join(p for p in parts if p).strip()
    origin = "docs" if body else "none"
    if len(body) < 400:
        if "wiki" not in cached and web_enabled() and time.time() - _wiki_failed.get(sid, 0) > WIKI_RETRY_SECONDS:
            try:
                cached["wiki"] = sources.wikipedia_text(s["name"])
                store.save_corpus(sid, "wiki", cached["wiki"])
            except requests.RequestException:
                _wiki_failed[sid] = time.time()
        if cached.get("wiki"):
            body = (body + "\n" + cached["wiki"]).strip()
            origin = "docs+wiki" if origin == "docs" else "wiki"
    return body, origin


def chapters(s) -> tuple[dict[str, str], str]:
    text, origin = course_text(s)
    return gen.chapters_of(text, topics_of(s)), origin


# ---------------------------------------------------------------- fiches
def sheets(s, chapter: str = "") -> tuple[list[dict], dict, str]:
    chs, origin = chapters(s)
    lv = levels(s["id"])
    if chapter in chs:
        chs = {chapter: chs[chapter]}
    built = gen.build_sheets(chs, lambda ch: difficulty_of(lv, ch))
    for sheet in built:
        sheet["difficulty"] = difficulty_of(lv, sheet["chapter"])
    return built, lv, origin


# ---------------------------------------------------------------- QCM
def create_quiz(user, s, chapter: str, difficulty: str) -> tuple[int | None, Report]:
    if difficulty not in gen.DIFFICULTIES:
        return None, Report().error("Choisis un niveau de difficulté.")
    chs, _ = chapters(s)
    if chapter and chapter not in chs:
        chapter = ""
    questions = gen.build_quiz(chs, difficulty, seed=random.randrange(1 << 30), only=chapter or None)
    if len(questions) < MIN_QUESTIONS:
        return None, Report().error(
            "Pas assez de contenu pour fabriquer un QCM" + (f" sur « {chapter} »" if chapter else "")
            + " : dépose tes supports de cours (PDF, diapositives… avec du texte) sur la page de la matière.")
    report = Report()
    wanted = gen.SETTINGS[difficulty]["questions"]
    if len(questions) < wanted:
        report.info(f"Le cours permet {len(questions)} questions sur les {wanted} prévues à ce niveau.")
    return store.create_quiz(user["id"], s["id"], chapter, difficulty, questions), report


def quiz(user, quiz_id) -> dict:
    q = owned(store.get_quiz(quiz_id, user["id"]))
    q["result"] = gen.grade(q["questions"], q["answers"], q["difficulty"]) if q["answers"] is not None else None
    q["settings"] = gen.SETTINGS[q["difficulty"]]
    return q


def submit(user, quiz_id, form) -> tuple[int, Report]:
    q = owned(store.get_quiz(quiz_id, user["id"]))
    if q["answers"] is not None:
        return q["subject_id"], Report().info("Ce QCM a déjà été corrigé : lance-en un nouveau pour t'améliorer.")
    answers = []
    for i, question in enumerate(q["questions"]):
        try:
            a = int(form.get(f"q{i}", ""))
            answers.append(a if 0 <= a < len(question["options"]) else None)
        except ValueError:
            answers.append(None)
    result = gen.grade(q["questions"], answers, q["difficulty"])
    store.submit_quiz(quiz_id, answers, result["correct"], result["score"])
    return q["subject_id"], Report().success(
        f"Note : {result['score']:g}/20 ({result['correct']} bonne(s) réponse(s) sur {len(answers)}).")


# ---------------------------------------------------------------- carnet de notes
def _points(scores, width, height, pad_x=8, pad_y=8):
    n = len(scores)
    step = (width - 2 * pad_x) / max(n - 1, 1)
    return [(round(pad_x + i * step, 1), round(pad_y + (20 - sc) / 20 * (height - 2 * pad_y), 1))
            for i, sc in enumerate(scores)]


def register(s, lv: dict) -> dict:
    rows = [dict(r) for r in store.attempts(s["id"])]
    scores = [r["score"] for r in rows]
    by_chapter: dict[str, list[int]] = {}
    for r in store.chapter_results(s["id"]):
        for question, answer in zip(json.loads(r["questions"]), json.loads(r["answers"])):
            stats = by_chapter.setdefault(question["chapter"], [0, 0])
            stats[0] += answer == question["answer"]
            stats[1] += 1
    chapters_stats = sorted(({"chapter": ch, "pct": round(100 * ok / tot), "n": tot}
                             for ch, (ok, tot) in by_chapter.items()), key=lambda c: c["pct"])
    main = difficulty_of(lv)
    advice = gen.trend([r["score"] for r in rows if r["difficulty"] == main and not r["chapter"]] or
                       [r["score"] for r in rows if r["difficulty"] == main])
    target = gen.harder(main, 1 if advice == "up" else -1) if advice else None
    chart = None
    if rows:
        w, h, left, bottom = 640, 220, 34, 26
        pts = _points(scores, w - left, h - bottom, pad_x=14, pad_y=10)
        chart = {"w": w, "h": h, "left": left, "plot_h": h - bottom,
                 "points": [{"x": x + left, "y": y, "row": r, "n": i + 1} for i, ((x, y), r) in enumerate(zip(pts, rows))],
                 "grid": [{"v": v, "y": round(10 + (20 - v) / 20 * (h - bottom - 20), 1)} for v in (0, 5, 10, 15, 20)]}
        chart["line"] = " ".join(f"{p['x']},{p['y']}" for p in chart["points"])
    return {"rows": rows, "n": len(rows), "chart": chart, "chapters": chapters_stats,
            "avg": round(sum(scores) / len(scores), 1) if scores else None,
            "best": max(scores) if scores else None, "last": scores[-1] if scores else None,
            "delta": round(scores[-1] - scores[0], 1) if len(scores) >= 2 else None,
            "advice": advice, "advice_target": target}


def hub(user, subject_id) -> dict:
    s = subject(user["id"], subject_id)
    lv = levels(subject_id)
    chs, origin = chapters(s)
    ex = [dict(e) for e in store.exercises(subject_id)]
    return {"s": s, "topics": topics_of(s), "levels": lv, "asked": "" in lv, "difficulty": difficulty_of(lv),
            "suggested": gen.suggested_difficulty(s["comfort"]), "chapters": list(chs), "origin": origin,
            "register": register(s, lv), "exercises": ex, "web_enabled": web_enabled(),
            "n_docs": len(documents_dao.list_for_subject(subject_id))}


def overview(user) -> list[dict]:
    lv = store.difficulties_for_user(user["id"])
    scores = store.scores_by_subject(user["id"])
    out = []
    for s in subjects_dao.list_for_user(user["id"]):
        sc = scores.get(s["id"], [])
        spark = " ".join(f"{x},{y}" for x, y in _points(sc, 120, 32, 4, 4)) if len(sc) >= 2 else ""
        out.append({"s": s, "difficulty": lv.get(s["id"]), "n": len(sc), "last": sc[-1] if sc else None,
                    "best": max(sc) if sc else None, "spark": spark})
    return out


def panel_summary(subject_id) -> dict | None:
    """Résumé pour le panneau de la page matière. Ne doit jamais casser cette page."""
    try:
        lv = store.difficulties(subject_id)
        rows = store.attempts(subject_id)
        return {"difficulty": lv.get(""), "n": len(rows), "last": rows[-1]["score"] if rows else None,
                "n_ex": len(store.exercises(subject_id))}
    except Exception:
        current_app.logger.exception("Atelier de révision : résumé indisponible")
        return None


# ---------------------------------------------------------------- exercices
def exercises_grouped(subject_id, only: str) -> list[tuple[str, list]]:
    groups: dict[str, list] = {}
    for e in store.exercises(subject_id):
        if only in gen.DIFFICULTIES and e["difficulty"] != only:
            continue
        groups.setdefault(e["chapter"] or gen.GENERAL, []).append(e)
    return list(groups.items())


def _store_exercises(s, found, topics, chapter, source, url) -> int:
    added = 0
    for e in found:
        ch = gen.assign_chapter(e["title"] + " " + e["statement"], topics)
        if ch == gen.GENERAL and chapter:
            ch = chapter
        added += store.add_exercise(s["id"], ch, e["title"], e["statement"], e["solution"],
                                    gen.estimate_difficulty(e["statement"]), source, url,
                                    gen.fingerprint(e["statement"]))
    return added


def collect_exercises(user, s, chapter: str, difficulty: str) -> Report:
    """Exercices des supports déposés, puis de PDF d'exercices corrigés trouvés sur internet."""
    topics = topics_of(s)
    if chapter not in topics:
        chapter = ""
    if difficulty not in gen.DIFFICULTIES:
        difficulty = difficulty_of(levels(s["id"]), chapter)
    report = Report()
    course_text(s)  # remplit le cache du texte des supports
    cached = store.corpus(s["id"])
    from_docs = 0
    for d in documents_dao.list_for_subject(s["id"]):
        found = gen.extract_exercises(cached.get(f"doc:{d['id']}", ""))
        from_docs += _store_exercises(s, found, topics, chapter, d["filename"], "")
    report.info(f"{from_docs} nouvel(s) exercice(s) trouvé(s) dans tes supports.")

    if not web_enabled():
        return report.info("La recherche sur internet est désactivée sur ce serveur.")
    urls = [r["url"] for r in resources_dao.list_for_subject(s["id"])
            if ".pdf" in r["url"].lower() and r["kind"] in ("web", "exam")]
    try:
        urls += [r["url"] for r in sources.search_exercise_pdfs(s["name"], chapter, difficulty,
                                                                LEVEL_QUERY.get(user["level"], ""))]
    except requests.RequestException:
        report.warning("La recherche d'exercices en ligne n'a pas répondu ; réessaie plus tard.")
    from_web, tried, useful = 0, 0, 0
    for url in dict.fromkeys(urls):
        if tried >= 5 or useful >= 3:
            break
        tried += 1
        try:
            found = gen.extract_exercises(sources.pdf_text(url), limit=8)
        except requests.RequestException:
            continue
        if found:
            useful += 1
            from_web += _store_exercises(s, found, topics, chapter, urlparse(url).netloc.replace("www.", ""), url)
    return report.success(f"{from_web} nouvel(s) exercice(s) importé(s) depuis {useful} PDF en ligne.") if from_web \
        else report.info("Aucun nouvel exercice trouvé en ligne cette fois-ci.")


def delete_exercise(user, exercise_id) -> int:
    e = owned(store.get_exercise(exercise_id, user["id"]))
    store.delete_exercise(exercise_id)
    return e["subject_id"]
