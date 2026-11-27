"""Supports de cours déposés par l'utilisateur."""
from .db import execute, one, query


def list_for_subject(subject_id):
    return query("SELECT * FROM documents WHERE subject_id = ? ORDER BY created_at DESC", subject_id)


def get_owned(doc_id, user_id):
    return one("SELECT d.* FROM documents d JOIN subjects s ON s.id = d.subject_id WHERE d.id = ? AND s.user_id = ?",
               doc_id, user_id)


def create(subject_id, filename, stored_path, excerpt, topics_found) -> int:
    return execute("INSERT INTO documents (subject_id, filename, stored_path, excerpt, topics_found) "
                   "VALUES (?, ?, ?, ?, ?)", subject_id, filename, stored_path, excerpt, topics_found)


def delete(doc_id) -> None:
    execute("DELETE FROM documents WHERE id = ?", doc_id)


def excerpts(subject_id) -> str:
    return " ".join(r["excerpt"] for r in query("SELECT excerpt FROM documents WHERE subject_id = ?", subject_id))


def paths_for_subject(subject_id) -> list[str]:
    return [r["stored_path"] for r in query("SELECT stored_path FROM documents WHERE subject_id = ?", subject_id)]


def paths_for_user(user_id) -> list[str]:
    return [r["stored_path"] for r in query(
        "SELECT d.stored_path FROM documents d JOIN subjects s ON s.id = d.subject_id WHERE s.user_id = ?", user_id)]
