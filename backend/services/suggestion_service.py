"""Suggestions en direct : matières tirées de l'agenda, rattachement d'un créneau, établissements."""
from functools import lru_cache

from backend.business import agenda_analysis, embeddings, matching, school_search
from backend.dao import schools as schools_dao

from . import subject_service


def subjects_from_agenda(user_id, query: str = "") -> list[dict]:
    """Matières suggérées d'après l'agenda, classées par similarité cosinus d'embeddings."""
    return agenda_analysis.suggest(query, subject_service.recent_events(user_id), subject_service.list_for(user_id))


def slot_link(user_id, title: str, kind: str) -> dict:
    """À quelle matière un créneau en cours de saisie sera-t-il rattaché ?"""
    if not title:
        return {"match": None, "new_name": None, "suggestions": []}
    subjects = subject_service.list_for(user_id)
    lookup = agenda_analysis.course_name(title) if kind == "examen" or agenda_analysis.is_exam(title) else title
    match, score = matching.best_match(lookup, subjects)
    return {
        "match": {"id": match["id"], "name": match["name"], "score": score} if match else None,
        "new_name": None if match else (matching.clean_title(lookup) or title),
        "suggestions": [{"name": n, "score": sc} for n, sc in embeddings.rank(title, [s["name"] for s in subjects])[:5]],
    }


@lru_cache(maxsize=1)
def _school_index():
    return school_search.build_index(schools_dao.all_rows())


def school_count() -> int:
    return len(_school_index())


def schools(query: str, level: str | None = None) -> list[dict]:
    """Établissements de France correspondant à la saisie (recherche locale, rien ne sort du serveur)."""
    return school_search.search(_school_index(), query, level)
