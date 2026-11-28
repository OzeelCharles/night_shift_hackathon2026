"""Générations par l'IA consommées par utilisateur et par jour (quota SEC-08).

Le compteur est incrémenté de façon atomique (une seule instruction UPDATE … WHERE count < limite) et validé
tout de suite : des demandes simultanées ne peuvent pas dépasser la limite, et une génération consommée
reste comptée même si la suite de la requête échoue.
"""
from .db import commit, execute, get_db


def consume(user_id: int, day: str, limit: int) -> bool:
    """Réserve une génération ; False si le quota du jour est atteint."""
    execute("INSERT OR IGNORE INTO llm_usage (user_id, day, count) VALUES (?, ?, 0)", user_id, day)
    reserved = get_db().execute("UPDATE llm_usage SET count = count + 1 WHERE user_id = ? AND day = ? AND count < ?",
                                (user_id, day, limit)).rowcount == 1
    commit()
    return reserved


def used(user_id: int, day: str) -> int:
    row = get_db().execute("SELECT count FROM llm_usage WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()
    return row[0] if row else 0
