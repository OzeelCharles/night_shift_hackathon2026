"""SEC-10 : journal d'audit des actions des administrateurs (consultable dans /admin)."""
import json

from .db import execute, query


def record(actor_id, action: str, target_id=None, target_email: str = "", details: dict | None = None) -> None:
    execute("INSERT INTO audit_log (actor_id, action, target_id, target_email, details) VALUES (?, ?, ?, ?, ?)",
            actor_id, action, target_id, target_email, json.dumps(details or {}, ensure_ascii=False))


def recent(limit: int = 100) -> list:
    return query("SELECT a.*, u.email AS actor_email FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id "
                 "ORDER BY a.id DESC LIMIT ?", limit)
