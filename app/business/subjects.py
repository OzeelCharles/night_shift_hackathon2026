"""Règles d'une matière : validation du formulaire, chapitres."""
from __future__ import annotations

import json
from datetime import date

from . import extractor
from .planner import DEFAULT_TOPICS
from .text import bounded_float, bounded_int

GOALS = {"exam": "Préparer un examen", "a_jour": "Être à jour dans le cours"}
COMFORT = {1: "Très difficile", 2: "Difficile", 3: "Moyen", 4: "À l'aise", 5: "Très à l'aise"}
MISSING_EXAM_DATE = "Indique la date de l'examen."


def topics_of(subject) -> list[str]:
    try:
        return json.loads(subject["topics"])
    except (TypeError, ValueError):
        return []


def dump_topics(topics: list[str]) -> str:
    return json.dumps(topics, ensure_ascii=False)


def merge_topics(current: list[str], new: list[str]) -> list[str]:
    """Ajoute les nouveaux chapitres (sans doublon) ; le plan générique est remplacé."""
    if current == DEFAULT_TOPICS:
        current = []
    lower = {t.lower() for t in current}
    return (current + [t for t in new if t.lower() not in lower])[:60]


def validate(form) -> tuple[dict, list[str]]:
    errors = []
    name = form.get("name", "").strip()
    if not name:
        errors.append("Le nom de la matière est obligatoire.")
    goal = form.get("goal", "exam") if form.get("goal", "exam") in GOALS else "exam"
    exam_date = form.get("exam_date") or None
    if goal == "exam":
        try:
            if date.fromisoformat(exam_date) <= date.today():
                errors.append("La date d'examen doit être dans le futur.")
        except (TypeError, ValueError):
            errors.append(MISSING_EXAM_DATE)
    else:
        exam_date = None
    return {
        "name": name[:100], "goal": goal, "exam_date": exam_date,
        "comfort": bounded_int(form.get("comfort"), 3, 1, 5),
        "weekly_hours": bounded_float(form.get("weekly_hours", 3) or 3, 3.0, 0.5, 40.0),
        "topics": extractor.clean_topics(form.get("topics", "").splitlines(), limit=60),
    }, errors
