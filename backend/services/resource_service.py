"""Ressources de révision : recherche sur internet (en arrière-plan), ressources gardées."""
from __future__ import annotations

import threading
from urllib.parse import urlparse

import requests
from flask import current_app

from backend.business import exams, extractor
from backend.business import resource_rules as rules
from backend.business.subjects import topics_of
from backend.dao import documents as documents_dao
from backend.dao import resources as dao
from backend.dao import subjects as subjects_dao
from backend.dao import users as users_dao
from backend.dao import web
from backend.dao.db import commit

from .common import owned
from .report import Report


def web_enabled() -> bool:
    return bool(current_app.config.get("ENABLE_WEB"))


# ---------------------------------------------------------------- recherche
def find_resources(subject: str, topics: list[str], extra_keywords: list[str] | None = None,
                   level: str = "autre", school: str = "", exam_days: int | None = None) -> tuple[list[dict], list[str]]:
    """Ressources classées par `kind` : exam (si l'examen approche), web, video, platform.
    Retourne (ressources, erreurs) ; ne lève jamais d'erreur réseau."""
    results: list[dict] = []
    errors: list[str] = []
    lvl = rules.LEVEL_QUERY.get(level, "")

    def q(*parts):
        return " ".join(p for p in parts if p)

    def documents(queries, kind, label):
        """Recherche web de documents : établissement, puis PDF, puis pages web."""
        found = []
        for query, from_school in queries:
            try:
                found += rules.label_documents(web.duckduckgo_search(query, limit=6), level, school, from_school, kind)
            except requests.RequestException as e:
                errors.append(f"{label} indisponible ({e.__class__.__name__}).")
                break
        return rules.documents_first(found, level)

    # 0. Examen proche : annales et sujets corrigés (ceux de l'établissement d'abord), vidéos de révision
    if exam_days is not None:
        exam_queries = [(q(subject, school, "examen corrigé filetype:pdf"), True)] if school else []
        exam_queries += [(q(subject, "annales examen corrigé", lvl, "filetype:pdf"), False),
                         (q(subject, "sujet d'examen avec corrigé", lvl), False)]
        results += documents(exam_queries, "exam", "Recherche d'annales")[:10]
        try:
            results += [dict(v, kind="exam") for v in web.youtube_search(q(subject, "révision examen", lvl), limit=4)]
        except (requests.RequestException, ValueError):
            pass  # les vidéos d'examen sont un bonus : on ne signale pas leur absence

    # 1. Documents PDF en priorité : polycopiés de l'établissement, cours et exercices corrigés du niveau
    web_queries = [(q(subject, school, "filetype:pdf"), True)] if school else []
    web_queries += [(q(subject, "cours", lvl, "filetype:pdf"), False),
                    (q(subject, "exercices corrigés", lvl, "filetype:pdf"), False),
                    (q(subject, "cours", lvl), False)]
    results += documents(web_queries, "web", "Recherche web")

    # 2. Wikipédia ensuite, en complément (définitions, vue d'ensemble)
    wiki_queries = [subject] + [q(subject, t) for t in topics[:1]]
    if extra_keywords:
        wiki_queries.append(q(subject, " ".join(extra_keywords[:2])))
    for query in wiki_queries:
        try:
            results += web.wikipedia_search(query, limit=2)
        except requests.RequestException as e:
            errors.append(f"Wikipédia indisponible ({e.__class__.__name__}).")
            break

    # 3. Vidéos : YouTube, sinon plan B via la recherche web
    videos: list[dict] = []
    try:
        for query in [q(subject, "cours", lvl)] + [q(subject, t, lvl) for t in topics[:2]]:
            videos += web.youtube_search(query, limit=6)
    except (requests.RequestException, ValueError):
        try:
            videos = web.videos_via_duckduckgo(q(subject, "cours", lvl))
        except requests.RequestException as e:
            errors.append(f"Vidéos indisponibles ({e.__class__.__name__}).")
    results += rules.suited_videos(videos, level)

    # 4. Plateformes adaptées au niveau
    results += rules.platform_links(subject, level)
    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            r.pop("pdf", None)
            unique.append({"meta": "", "thumbnail": "", "snippet": "", **r})
    return unique, errors


def outline(name: str) -> list[str]:
    """Chapitres proposés pour une matière sans support (plan Wikipédia), vide si indisponible."""
    if not web_enabled():
        return []
    try:
        return extractor.clean_topics(web.wikipedia_outline(name), limit=15)
    except Exception:
        return []


def signature(user, subject) -> str:
    """Ce dont dépendent les ressources proposées : profil et proximité de l'examen.
    Quand cette signature change, les ressources sont recherchées à nouveau."""
    return f"{user['level']}|{user['school']}|{exams.phase(subject['exam_date'])}"


def refresh(subject, user) -> list[str]:
    """Remplace les suggestions d'une matière (les ressources gardées par l'utilisateur restent)."""
    excerpts = documents_dao.excerpts(subject["id"])
    days = exams.days_left(subject["exam_date"])
    found, errors = find_resources(
        subject["name"], topics_of(subject), extractor.keywords(excerpts, 5) if excerpts else None,
        level=user["level"], school=user["school"],
        exam_days=days if exams.phase(subject["exam_date"]) == "examen" else None)
    saved = dao.saved_urls(subject["id"])
    dao.replace_suggestions(subject["id"], [r for r in found if r["url"] not in saved])
    subjects_dao.update(subject["id"], resources_status="error" if errors and len(found) <= 6 else "ok",
                        resources_sig=signature(user, subject))
    return errors


def _worker(app, user_id, subject_ids):
    with app.app_context():
        user = users_dao.get(user_id)
        for sid in subject_ids:
            subject = subjects_dao.get_owned(sid, user_id)
            if subject is None or user is None:
                continue
            try:
                refresh(subject, user)
            except Exception:
                subjects_dao.set_status([sid], "error")
            commit()  # chaque matière s'affiche dès qu'elle est prête


def refresh_async(user_id, subject_ids) -> bool:
    """Lance la recherche sans faire attendre l'utilisateur ; la page affiche « en cours »."""
    subject_ids = list(subject_ids)
    if not subject_ids or not web_enabled():
        return False
    subjects_dao.set_status(subject_ids, "pending")
    commit()  # le traitement en arrière-plan doit voir les données de la requête en cours
    threading.Thread(target=_worker, args=(current_app._get_current_object(), user_id, subject_ids),
                     daemon=True).start()
    return True


def refresh_if_stale(user, subject) -> bool:
    """Relance la recherche si le profil ou la proximité de l'examen ont changé depuis la dernière."""
    if web_enabled() and subject["resources_status"] != "pending" and subject["resources_sig"] != signature(user, subject):
        return refresh_async(user["id"], [subject["id"]])
    return False


# ---------------------------------------------------------------- ressources de l'utilisateur
def grouped(subject_id) -> dict:
    res = dao.list_for_subject(subject_id)
    suggestions = [r for r in res if not r["saved"]]
    return {"saved": [r for r in res if r["saved"]],
            **{name: [r for r in suggestions if r["kind"] == kind]
               for name, kind in (("videos", "video"), ("web", "web"), ("platforms", "platform"), ("exam_res", "exam"))}}


def keep(user_id, resource_id) -> tuple[int, Report]:
    r = owned(dao.get_owned(resource_id, user_id))
    dao.set_saved(resource_id, True)
    return r["subject_id"], Report().success(f"« {r['title'][:80]} » ajouté à tes ressources.")


def remove(user_id, resource_id) -> tuple[int, Report]:
    r = owned(dao.get_owned(resource_id, user_id))
    if r["kind"] == "perso":
        dao.delete(resource_id)
    else:
        dao.set_saved(resource_id, False)
    return r["subject_id"], Report().success("Ressource retirée de ta liste.")


def add_link(user_id, subject_id, title: str, url: str) -> Report:
    owned(subjects_dao.get_owned(subject_id, user_id))
    url, title = url.strip(), title.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return Report().error("Lien invalide : il doit commencer par http:// ou https://.")
    dao.add_personal(subject_id, (title or parsed.netloc)[:300], url, parsed.netloc.replace("www.", ""))
    return Report().success("Lien ajouté à tes ressources.")


# ---------------------------------------------------------------- réactions aux événements
def on_subjects_created(user_id, subject_ids, **_):
    refresh_async(user_id, subject_ids)


def on_profile_changed(user_id, level_label, school, **_):
    ids = [s["id"] for s in subjects_dao.list_for_user(user_id)]
    if refresh_async(user_id, ids):
        return [f"Les ressources de tes {len(ids)} matière(s) sont en cours d'adaptation à ton niveau "
                f"({level_label}) et à {school}."]
    return []
