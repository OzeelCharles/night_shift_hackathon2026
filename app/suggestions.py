"""Suggestions de matières à partir de l'agenda et lecture des dates d'examen.

- Les intitulés de cours de l'emploi du temps (« CM Probabilités - Amphi B ») sont regroupés
  par matière, puis classés par similarité cosinus d'embeddings avec ce que l'utilisateur tape.
- Un événement dont l'intitulé ressemble à une épreuve (« Partiel Probabilités », « DS de
  maths », « Examen final - Droit ») fournit la date d'examen de la matière correspondante.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from . import embeddings, matching

EXAM_RE = re.compile(
    r"\b(examens?|exams?|partiels?|ds|devoirs? surveilles?|controles?( continu)?|cc|ccf|epreuves?|"
    r"finals?|midterms?|concours blanc|bac blanc|brevet blanc|rattrapages?|soutenance)\b")
EXAM_WORDS = re.compile(
    r"\b(examens?|exams?|partiels?|DS|devoirs? surveill[ée]s?|contr[ôo]les?( continu)?|CC|CCF|[ée]preuves?|"
    r"finals?|midterms?|concours blanc|bac blanc|brevet blanc|rattrapages?|soutenance)\b\s*(de |d'|du |des )?",
    re.IGNORECASE)


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def is_exam(title: str) -> bool:
    return bool(EXAM_RE.search(_plain(title)))


def course_name(title: str) -> str:
    """Nom de matière lisible : « Partiel de Probabilités - Amphi B » -> « Probabilités »."""
    return matching.clean_title(EXAM_WORDS.sub(" ", title or "").strip(" -–:"))


def detect_exam_date(name: str, events) -> tuple[date | None, dict | None]:
    """Première épreuve à venir dont l'intitulé correspond à la matière `name`."""
    today = date.today()
    best = None
    for e in events:
        if not is_exam(e["title"]):
            continue
        d = datetime.fromisoformat(e["start"]).date()
        if d < today:
            continue
        linked = name and e.get("subject_name") and matching.similarity(name, e["subject_name"]) >= matching.THRESHOLD
        if linked or matching.similarity(name, course_name(e["title"])) >= matching.THRESHOLD:
            if best is None or d < best[0]:
                best = (d, e)
    return (best[0], best[1]) if best else (None, None)


def agenda_courses(events, subjects) -> list[dict]:
    """Regroupe les intitulés de l'agenda en matières candidates (non encore créées)."""
    groups: dict[str, dict] = {}
    now = datetime.now()
    for e in events:
        if e.get("kind") == "autre" and e.get("source") == "manuel":
            continue  # occupation déclarée comme telle par l'utilisateur (sport, job…)
        name = course_name(e["title"]) if is_exam(e["title"]) else matching.clean_title(e["title"])
        key = matching.normalize(name)
        if not key:
            continue
        g = groups.setdefault(key, {"name": name, "count": 0, "next": None, "exam_date": None,
                                    "subject_id": None, "titles": set()})
        g["titles"].add(e["title"])
        start = datetime.fromisoformat(e["start"])
        if is_exam(e["title"]):
            if start.date() >= now.date() and (g["exam_date"] is None or start.date() < g["exam_date"]):
                g["exam_date"] = start.date()
        else:
            g["count"] += 1
            if start >= now and (g["next"] is None or start < g["next"]):
                g["next"] = start
        if e.get("subject_id"):
            g["subject_id"] = e["subject_id"]
    # Une épreuve seule (sans cours) reste une suggestion valable ; on fusionne les groupes
    # que la similarité considère comme la même matière (« Proba » et « Probabilités »).
    merged: list[dict] = []
    for g in sorted(groups.values(), key=lambda x: -x["count"]):
        twin = next((m for m in merged if matching.similarity(m["name"], g["name"]) >= matching.THRESHOLD), None)
        if twin is None:
            merged.append(g)
            continue
        twin["count"] += g["count"]
        twin["titles"] |= g["titles"]
        twin["subject_id"] = twin["subject_id"] or g["subject_id"]
        for k in ("next", "exam_date"):
            if g[k] and (twin[k] is None or g[k] < twin[k]):
                twin[k] = g[k]
    for g in merged:
        existing, _ = matching.best_match(g["name"], subjects)
        g["existing"] = existing
    return merged


def suggest(query: str, events, subjects, limit: int = 6) -> list[dict]:
    """Matières suggérées pour la saisie `query` (vide : les cours les plus fréquents)."""
    candidates = agenda_courses(events, subjects)
    if query.strip():
        ranked = embeddings.rank(query, [c["name"] for c in candidates])
        order = {name: score for name, score in ranked}
        candidates = [c for c in candidates if c["name"] in order]
        for c in candidates:
            c["score"] = order[c["name"]]
        candidates.sort(key=lambda c: -c["score"])
    else:
        candidates = [c for c in candidates if c["existing"] is None]
        for c in candidates:
            c["score"] = None
    out = []
    for c in candidates[:limit]:
        out.append({
            "name": c["name"], "score": c["score"], "count": c["count"],
            "next": c["next"].isoformat(timespec="minutes") if c["next"] else None,
            "exam_date": c["exam_date"].isoformat() if c["exam_date"] else None,
            "existing": {"id": c["existing"]["id"], "name": c["existing"]["name"]} if c["existing"] else None,
        })
    return out
