"""Événements de l'agenda : cours, examens, occupations (table `events`)."""
from .db import execute, one, query, scalar

WITH_SUBJECT = "SELECT e.*, s.name AS subject_name FROM events e LEFT JOIN subjects s ON s.id = e.subject_id "


def since(user_id, since_iso: str) -> list[dict]:
    """Événements récents et à venir, avec le nom de la matière rattachée."""
    return [dict(r) for r in query(WITH_SUBJECT + "WHERE e.user_id = ? AND e.end >= ? ORDER BY e.start",
                                   user_id, since_iso)]


def upcoming(user_id, now_iso: str, limit: int = 300):
    return query(WITH_SUBJECT + "WHERE e.user_id = ? AND e.end >= ? ORDER BY e.start LIMIT ?", user_id, now_iso, limit)


def in_window(user_id, start_iso: str, end_iso: str):
    return query(WITH_SUBJECT + "WHERE e.user_id = ? AND e.start < ? AND e.end > ? ORDER BY e.start",
                 user_id, end_iso, start_iso)


def busy_from(user_id, now_iso: str):
    return query("SELECT title, start, end FROM events WHERE user_id = ? AND end >= ?", user_id, now_iso)


def for_subject(subject_id, user_id, now_iso: str, exams: bool):
    op = "=" if exams else "!="
    return query(f"SELECT * FROM events WHERE subject_id = ? AND user_id = ? AND end >= ? AND kind {op} 'examen' "
                 "ORDER BY start", subject_id, user_id, now_iso)


def get_owned(event_id, user_id):
    return one("SELECT * FROM events WHERE id = ? AND user_id = ?", event_id, user_id)


def create(user_id, title, start, end, source, kind, subject_id=None, uid="") -> int:
    return execute("INSERT INTO events (user_id, title, start, end, source, kind, subject_id, uid) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", user_id, title, start, end, source, kind, subject_id, uid)


def update(event_id, title, start, end, subject_id, kind) -> None:
    execute("UPDATE events SET title = ?, start = ?, end = ?, subject_id = ?, kind = ? WHERE id = ?",
            title, start, end, subject_id, kind, event_id)


def link(event_id, subject_id, kind) -> None:
    execute("UPDATE events SET subject_id = ?, kind = ? WHERE id = ?", subject_id, kind, event_id)


def link_same_title(user_id, title, subject_id, kind) -> None:
    """Rattache toutes les occurrences d'un même cours (même intitulé)."""
    execute("UPDATE events SET subject_id = ?, kind = ? WHERE user_id = ? AND title = ?",
            subject_id, kind, user_id, title)


def unlink_subject(subject_id) -> None:
    execute("UPDATE events SET subject_id = NULL, kind = 'autre' WHERE subject_id = ?", subject_id)


def delete(event_id, user_id) -> None:
    execute("DELETE FROM events WHERE id = ? AND user_id = ?", event_id, user_id)


def delete_all(user_id) -> None:
    execute("DELETE FROM events WHERE user_id = ?", user_id)


def imported_from(user_id, since_iso: str):
    """Événements déjà importés d'un fichier .ics, à venir."""
    return query("SELECT * FROM events WHERE user_id = ? AND source = 'ics' AND end >= ?", user_id, since_iso)


def known_links(user_id) -> dict:
    """Rattachements déjà connus (corrections manuelles comprises) : intitulé -> (matière, type)."""
    return {r["title"]: (r["subject_id"], r["kind"]) for r in query(
        "SELECT title, subject_id, kind FROM events WHERE user_id = ? AND source = 'ics' AND subject_id IS NOT NULL "
        "ORDER BY id", user_id)}


def next_exam_day(subject_id, after_iso: str) -> str | None:
    return scalar("SELECT MIN(substr(start, 1, 10)) FROM events WHERE subject_id = ? AND kind = 'examen' "
                  "AND substr(start, 1, 10) > ?", subject_id, after_iso)


def last_class_day(subject_id, after_iso: str) -> str | None:
    return scalar("SELECT MAX(substr(start, 1, 10)) FROM events WHERE subject_id = ? AND kind = 'cours' "
                  "AND substr(start, 1, 10) > ?", subject_id, after_iso)


def exam_title_on(subject_id, day_iso: str) -> str | None:
    return scalar("SELECT title FROM events WHERE subject_id = ? AND kind = 'examen' AND substr(start, 1, 10) = ?",
                  subject_id, day_iso)


def default_exam_ids(user_id) -> set[int]:
    """Séances de cours qui servent d'examen par défaut : la dernière séance (tous ses exemplaires,
    si l'export la répète par groupe) des matières dont l'examen est fixé à la dernière séance."""
    return {r["id"] for r in query(
        "SELECT e.id FROM events e JOIN subjects s ON s.id = e.subject_id "
        "WHERE s.user_id = ? AND s.goal = 'exam' AND s.exam_source = 'derniere_seance' AND e.kind = 'cours' "
        "AND e.start = (SELECT MAX(start) FROM events WHERE subject_id = s.id AND kind = 'cours' "
        "               AND substr(start, 1, 10) = s.exam_date)", user_id)}
