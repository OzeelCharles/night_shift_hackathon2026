"""Notes du dossier scolaire. Toutes les requêtes sont filtrées par utilisateur : une note n'est
jamais lue ni modifiée par quelqu'un d'autre que son propriétaire."""
from .db import execute, execute_many, one, query


def list_for_user(user_id):
    return query("SELECT * FROM grades WHERE user_id = ? ORDER BY day, id", user_id)


def get_owned(grade_id, user_id):
    return one("SELECT * FROM grades WHERE id = ? AND user_id = ?", grade_id, user_id)


def create(user_id, subject_id, course, value, out_of, coefficient, label, day) -> int:
    return execute("INSERT INTO grades (user_id, subject_id, course, value, out_of, coefficient, label, day) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", user_id, subject_id, course, value, out_of, coefficient, label, day)


def delete(grade_id, user_id) -> None:
    execute("DELETE FROM grades WHERE id = ? AND user_id = ?", grade_id, user_id)


def link_to_subject(user_id, grade_ids, subject_id) -> None:
    """Rattache à une matière des notes saisies avant qu'elle existe (même nom)."""
    execute_many("UPDATE grades SET subject_id = ? WHERE id = ? AND user_id = ?",
                 [(subject_id, gid, user_id) for gid in grade_ids])
