"""Prises de caféine (boissons énergisantes, café…) : une ligne par boisson consommée."""
from .db import execute, query


def add(user_id, label: str, mg: int, taken_at_iso: str) -> int:
    return execute("INSERT INTO caffeine_intakes (user_id, label, mg, taken_at) VALUES (?, ?, ?, ?)",
                   user_id, label, mg, taken_at_iso)


def since(user_id, from_iso: str):
    return query("SELECT * FROM caffeine_intakes WHERE user_id = ? AND taken_at >= ? ORDER BY taken_at",
                 user_id, from_iso)


def delete(intake_id, user_id) -> None:
    execute("DELETE FROM caffeine_intakes WHERE id = ? AND user_id = ?", intake_id, user_id)


def daily_totals(user_id, from_day_iso: str):
    """Total de mg par jour (AAAA-MM-JJ) depuis un jour donné ; les jours sans prise sont absents."""
    return query("SELECT substr(taken_at, 1, 10) AS day, SUM(mg) AS mg, COUNT(*) AS n FROM caffeine_intakes "
                 "WHERE user_id = ? AND substr(taken_at, 1, 10) >= ? GROUP BY day ORDER BY day",
                 user_id, from_day_iso)
