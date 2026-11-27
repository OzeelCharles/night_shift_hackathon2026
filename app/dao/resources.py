"""Ressources d'une matière : suggestions (saved = 0) et ressources gardées par l'utilisateur."""
from .db import execute, execute_many, one, query


def list_for_subject(subject_id):
    return query("SELECT * FROM resources WHERE subject_id = ? ORDER BY id", subject_id)


def get_owned(resource_id, user_id):
    return one("SELECT r.* FROM resources r JOIN subjects s ON s.id = r.subject_id WHERE r.id = ? AND s.user_id = ?",
               resource_id, user_id)


def saved_urls(subject_id) -> set[str]:
    return {r["url"] for r in query("SELECT url FROM resources WHERE subject_id = ? AND saved = 1", subject_id)}


def replace_suggestions(subject_id, items) -> None:
    execute("DELETE FROM resources WHERE subject_id = ? AND saved = 0", subject_id)
    execute_many("INSERT INTO resources (subject_id, title, url, source, snippet, kind, thumbnail, meta) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 [(subject_id, r["title"][:300], r["url"], r["source"], r["snippet"], r["kind"], r["thumbnail"],
                   r["meta"]) for r in items])


def set_saved(resource_id, saved: bool) -> None:
    execute("UPDATE resources SET saved = ? WHERE id = ?", int(saved), resource_id)


def delete(resource_id) -> None:
    execute("DELETE FROM resources WHERE id = ?", resource_id)


def add_personal(subject_id, title, url, source) -> None:
    execute("INSERT INTO resources (subject_id, title, url, source, kind, saved) VALUES (?, ?, ?, ?, 'perso', 1)",
            subject_id, title, url, source)
