"""Signalements d'affluence des utilisateurs : une ligne par signalement, conservés 2 jours."""
from .db import execute, one, query


def add(user_id, place_key: str, level: int) -> int:
    execute("DELETE FROM occupancy_reports WHERE created_at < datetime('now', 'localtime', '-2 days')")
    return execute("INSERT INTO occupancy_reports (user_id, place_key, level) VALUES (?, ?, ?)",
                   user_id, place_key, level)


def recent(place_keys, minutes: int):
    """(lieu, niveau, ancienneté en minutes) des signalements récents des lieux demandés."""
    keys = list(place_keys)
    if not keys:
        return []
    return query(f"SELECT place_key, level, CAST((julianday('now', 'localtime') - julianday(created_at)) * 1440 AS REAL) AS age "
                 f"FROM occupancy_reports WHERE place_key IN ({','.join('?' * len(keys))}) "
                 f"AND created_at >= datetime('now', 'localtime', ?)", *keys, f"-{int(minutes)} minutes")


def last_by_user(user_id, place_key: str):
    """Âge en minutes du dernier signalement de cet utilisateur pour ce lieu (None s'il n'en a jamais fait)."""
    row = one("SELECT CAST((julianday('now', 'localtime') - julianday(created_at)) * 1440 AS REAL) AS age "
              "FROM occupancy_reports WHERE user_id = ? AND place_key = ? ORDER BY id DESC LIMIT 1", user_id, place_key)
    return row["age"] if row else None
