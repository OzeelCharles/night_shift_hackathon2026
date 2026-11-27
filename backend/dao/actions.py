"""Modifications proposées par l'assistant, en attente de validation par l'étudiant (filtrées par utilisateur)."""
import json

from .db import execute, one, query


def create(user_id, kind: str, payload: dict) -> int:
    execute("DELETE FROM chat_actions WHERE user_id = ? AND created_at < datetime('now', 'localtime', '-1 day')",
            user_id)  # propositions trop anciennes : le planning a pu changer depuis
    return execute("INSERT INTO chat_actions (user_id, kind, payload) VALUES (?, ?, ?)",
                   user_id, kind, json.dumps(payload, ensure_ascii=False))


def get_owned(action_id, user_id) -> dict | None:
    row = one("SELECT * FROM chat_actions WHERE id = ? AND user_id = ?", action_id, user_id)
    return dict(row) | {"payload": json.loads(row["payload"])} if row else None


def statuses(user_id, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: {"status": r["status"], "choice": r["choice"]} for r in query(
        f"SELECT id, status, choice FROM chat_actions WHERE user_id = ? AND id IN ({marks})", user_id, *ids)}


def close(action_id, user_id, status: str, choice: str = "") -> None:
    execute("UPDATE chat_actions SET status = ?, choice = ? WHERE id = ? AND user_id = ? AND status = 'pending'",
            status, choice, action_id, user_id)
