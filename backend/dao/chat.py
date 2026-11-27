"""Conversations de l'assistant. Chaque requête filtre sur l'utilisateur : une conversation est privée."""
import json

from .db import execute, one, query


def current(user_id, session_key: str):
    """Conversation en cours de cette session de connexion : la plus récemment utilisée."""
    return one("SELECT * FROM chat_conversations WHERE user_id = ? AND session_key = ? "
               "ORDER BY updated_at DESC, id DESC LIMIT 1", user_id, session_key)


def get_owned(conversation_id, user_id):
    return one("SELECT * FROM chat_conversations WHERE id = ? AND user_id = ?", conversation_id, user_id)


def create(user_id, session_key: str, title: str = "") -> int:
    return execute("INSERT INTO chat_conversations (user_id, session_key, title) VALUES (?, ?, ?)",
                   user_id, session_key, title[:120])


def purge_other_sessions(user_id, session_key: str) -> None:
    """Efface les conversations d'une session de connexion terminée (navigateur fermé…).

    On vérifie d'abord par une lecture : sans rien à effacer, la base n'est pas verrouillée en écriture
    (la requête peut ensuite attendre Groq plusieurs secondes)."""
    if one("SELECT 1 FROM chat_conversations WHERE user_id = ? AND session_key != ? LIMIT 1", user_id, session_key):
        execute("DELETE FROM chat_conversations WHERE user_id = ? AND session_key != ?", user_id, session_key)


def delete_all(user_id) -> None:
    execute("DELETE FROM chat_conversations WHERE user_id = ?", user_id)


def delete_everyone() -> None:
    """Démarrage du serveur : aucune conversation ne survit à la fermeture de l'application."""
    execute("DELETE FROM chat_conversations")


def purge_idle(user_id, minutes: int) -> None:
    """Efface les conversations inactives depuis `minutes` (navigateur qui restaure son cookie de session)."""
    cutoff = f"-{int(minutes)} minutes"
    if one("SELECT 1 FROM chat_conversations WHERE user_id = ? AND updated_at < datetime('now', 'localtime', ?) "
           "LIMIT 1", user_id, cutoff):
        execute("DELETE FROM chat_conversations WHERE user_id = ? AND updated_at < datetime('now', 'localtime', ?)",
                user_id, cutoff)


def touch(conversation_id, user_id, title: str | None = None) -> None:
    if title:
        execute("UPDATE chat_conversations SET title = ? WHERE id = ? AND user_id = ? AND title = ''",
                title[:120], conversation_id, user_id)
    execute("UPDATE chat_conversations SET updated_at = datetime('now', 'localtime') WHERE id = ? AND user_id = ?",
            conversation_id, user_id)


def set_summary(conversation_id, user_id, summary: str, upto: int) -> None:
    execute("UPDATE chat_conversations SET summary = ?, summarized_upto = ? WHERE id = ? AND user_id = ?",
            summary, upto, conversation_id, user_id)


def delete(conversation_id, user_id) -> None:
    execute("DELETE FROM chat_conversations WHERE id = ? AND user_id = ?", conversation_id, user_id)


def messages(conversation_id, user_id, after_id: int = 0) -> list[dict]:
    rows = query("SELECT m.id, m.role, m.content, m.meta, m.created_at FROM chat_messages m "
                 "JOIN chat_conversations c ON c.id = m.conversation_id "
                 "WHERE m.conversation_id = ? AND c.user_id = ? AND m.id > ? ORDER BY m.id",
                 conversation_id, user_id, after_id)
    return [dict(r) | {"meta": json.loads(r["meta"] or "{}")} for r in rows]


def add_message(conversation_id, role: str, content: str, meta: dict | None = None) -> int:
    return execute("INSERT INTO chat_messages (conversation_id, role, content, meta) VALUES (?, ?, ?, ?)",
                   conversation_id, role, content, json.dumps(meta or {}, ensure_ascii=False))
