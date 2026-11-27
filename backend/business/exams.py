"""Règles de la date d'examen d'une matière (pures : pas de base de données, pas de web).

Priorité : date choisie par l'utilisateur > prochaine épreuve de l'emploi du temps
> dernière séance de cours (date par défaut).
"""
from __future__ import annotations

from datetime import date

from .text import date_fr

EXAM_SOON_DAYS = 21  # en deçà, des ressources de préparation d'examen sont ajoutées
SOURCES = {"manuel": "date choisie par toi", "agenda": "épreuve de ton emploi du temps",
           "derniere_seance": "par défaut, dernière séance de cours"}


def days_left(exam_date: str | None, today: date | None = None) -> int | None:
    if not exam_date:
        return None
    return (date.fromisoformat(exam_date) - (today or date.today())).days


def phase(exam_date: str | None) -> str:
    """« examen » quand l'épreuve approche (ressources d'annales ajoutées), sinon « cours »."""
    days = days_left(exam_date)
    return "examen" if days is not None and 0 <= days <= EXAM_SOON_DAYS else "cours"


def choose(manual: str | None, agenda_date: str | None, agenda_source: str | None,
           today_iso: str) -> tuple[str, str] | None:
    """Date retenue et son origine, ou None si rien ne permet de la déterminer."""
    if manual and manual > today_iso:
        return manual, "manuel"
    if agenda_date:
        return agenda_date, agenda_source
    return None


def change_message(name: str, new: str, source: str, old_date: str | None, old_source: str) -> str | None:
    d = date_fr(new)
    if source == "derniere_seance" and old_source == "derniere_seance" and old_date:
        verb = "repoussé" if new > old_date else "avancé"
        return f"Examen de « {name} » {verb} au {d} : c'est maintenant sa dernière séance de cours."
    if source == "derniere_seance":
        return (f"Aucune épreuve de « {name} » dans ton emploi du temps : l'examen est fixé "
                f"par défaut à sa dernière séance, le {d}.")
    if source == "agenda":
        return f"Examen de « {name} » lu dans ton emploi du temps : {d}."
    return None


def typed_date(form, current=None) -> tuple[str | None, str | None]:
    """Date d'examen saisie dans un formulaire -> (date choisie par l'utilisateur, date retenue).

    Une date laissée identique à la date actuelle ne devient pas une date « choisie » : ainsi
    recalculer un programme ne fige pas la date par défaut issue de l'emploi du temps.
    """
    typed = (form.get("exam_date") or "").strip()
    if current is not None and typed and typed == current["exam_date"]:
        return current["exam_manual"], typed
    return (typed or None), (typed or None)
