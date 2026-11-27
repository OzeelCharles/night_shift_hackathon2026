"""Préférences de travail (une ligne par utilisateur)."""
from .db import execute, one

FIELDS = ("method", "session_minutes", "break_minutes", "daily_max_minutes", "day_start", "day_end",
          "rest_days", "buffer_minutes", "horizon_weeks")


def get(user_id):
    row = one("SELECT * FROM preferences WHERE user_id = ?", user_id)
    if row is None:
        execute("INSERT INTO preferences (user_id) VALUES (?)", user_id)
        row = one("SELECT * FROM preferences WHERE user_id = ?", user_id)
    return row


def save(user_id, values: dict) -> None:
    execute(f"UPDATE preferences SET {', '.join(f'{c} = ?' for c in FIELDS)} WHERE user_id = ?",
            *[values[c] for c in FIELDS], user_id)


def save_sleep(user_id, bedtime: str, wake_time: str) -> None:
    get(user_id)  # crée la ligne au besoin
    execute("UPDATE preferences SET bedtime = ?, wake_time = ? WHERE user_id = ?", bedtime, wake_time, user_id)
