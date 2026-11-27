"""Emploi du temps lu sur une photo : consigne envoyée au modèle de vision et mise en créneaux de sa réponse."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

PROMPT = (
    "Cette photo montre un emploi du temps, un planning ou une liste d'examens d'étudiant. "
    "Relève chaque créneau visible (cours, TD, TP, examen, partiel, autre occupation).\n"
    "Réponds uniquement par un objet JSON de la forme :\n"
    '{"events": [{"title": "CM Probabilités", "date": "2026-12-03", "weekday": 4, '
    '"start": "08:30", "end": "10:00", "exam": false}]}\n'
    "- title : intitulé tel qu'écrit (matière, type de cours), sans la salle ni l'enseignant.\n"
    "- date : AAAA-MM-JJ si la date est lisible sur la photo, sinon \"\".\n"
    "- weekday : jour de la semaine de 1 (lundi) à 7 (dimanche), 0 si inconnu.\n"
    "- start, end : heures au format HH:MM (24 h).\n"
    "- exam : true pour un examen, partiel, DS, contrôle.\n"
    "N'invente aucun créneau : si rien n'est lisible, renvoie {\"events\": []}."
)
_TIME = re.compile(r"^\s*(\d{1,2})\s*[:hH.]\s*(\d{2})?\s*$")


def _time(v) -> tuple[int, int] | None:
    m = _TIME.match(str(v or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return (h, mi) if h < 24 and mi < 60 else None


def _day(item: dict, today: date) -> date | None:
    try:
        return date.fromisoformat(str(item.get("date") or "").strip())
    except ValueError:
        pass
    try:
        wd = int(item.get("weekday") or 0)
    except (TypeError, ValueError):
        return None
    if not 1 <= wd <= 7:
        return None
    return today + timedelta(days=(wd - 1 - today.weekday()) % 7)  # prochaine occurrence, aujourd'hui compris


def slots(items, today: date, weeks: int = 1) -> tuple[list[dict], int]:
    """Créneaux {title, start, end, exam} tirés de la réponse ; renvoie aussi le nombre d'éléments ignorés.

    Un créneau sans date prend la prochaine occurrence de son jour de la semaine ; tous sont répétés
    `weeks` semaines (un emploi du temps hebdomadaire). Les créneaux passés sont ignorés."""
    out, skipped, seen = [], 0, set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            skipped += 1
            continue
        title = " ".join(str(item.get("title") or "").split())[:200]
        d, a, b = _day(item, today), _time(item.get("start")), _time(item.get("end"))
        if not title or d is None or a is None or b is None or b <= a:
            skipped += 1
            continue
        exam = item.get("exam") is True or str(item.get("exam")).lower() == "true"
        for k in range(1 if exam else weeks):  # un examen n'a lieu qu'une fois
            day = d + timedelta(weeks=k)
            start, end = datetime.combine(day, datetime.min.time()).replace(hour=a[0], minute=a[1]), \
                datetime.combine(day, datetime.min.time()).replace(hour=b[0], minute=b[1])
            if day < today or (title, start) in seen:
                continue
            seen.add((title, start))
            out.append({"title": title, "start": start, "end": end, "exam": exam})
    return out, skipped
