"""Préférences de travail."""
from datetime import time

from backend.business import planner
from backend.business.text import bounded_int, parse_time
from backend.dao import preferences as dao


def load(user_id) -> tuple[planner.Prefs, object]:
    """(préférences prêtes pour le planificateur, ligne brute pour les formulaires)."""
    row = dao.get(user_id)
    prefs = planner.Prefs(
        method=row["method"], session_minutes=row["session_minutes"], break_minutes=row["break_minutes"],
        daily_max_minutes=row["daily_max_minutes"], day_start=parse_time(row["day_start"], time(8)),
        day_end=parse_time(row["day_end"], time(21)),
        rest_days={int(d) for d in row["rest_days"].split(",") if d.strip().isdigit()},
        buffer_minutes=row["buffer_minutes"])
    return prefs, row


def rest_days_of(row) -> list[int]:
    return [int(d) for d in row["rest_days"].split(",") if d.isdigit()]


def save(user_id, form) -> str | None:
    """Enregistre les préférences ; renvoie un message d'erreur, ou None si tout va bien."""
    start = parse_time(form.get("day_start"), time(8))
    end = parse_time(form.get("day_end"), time(21))
    if end <= start:
        return "L'heure de fin doit être après l'heure de début."
    rest = sorted({int(d) for d in form.getlist("rest_days") if d.isdigit() and 0 <= int(d) <= 6})
    if len(rest) == 7:
        return "Garde au moins un jour de travail."
    dao.save(user_id, {
        "method": form.get("method") if form.get("method") in planner.METHODS else "mixte",
        "session_minutes": bounded_int(form.get("session_minutes"), 50, 20, 240),
        "break_minutes": bounded_int(form.get("break_minutes"), 10, 0, 60),
        "daily_max_minutes": bounded_int(form.get("daily_max_minutes"), 180, 20, 720),
        "day_start": start.strftime("%H:%M"), "day_end": end.strftime("%H:%M"),
        "rest_days": ",".join(map(str, rest)),
        "buffer_minutes": bounded_int(form.get("buffer_minutes"), 15, 0, 120),
        "horizon_weeks": bounded_int(form.get("horizon_weeks"), 4, 1, 16),
    })
    return None
