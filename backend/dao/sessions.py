"""Séances de révision (planifiées automatiquement, ou posées à la main : manual = 1)."""
from .db import MINUTES, execute, execute_many, one, placeholders, query

WITH_SUBJECT = "SELECT se.*, su.name AS subject_name FROM sessions se JOIN subjects su ON su.id = se.subject_id "


def between(user_id, start_iso: str, end_iso: str):
    return query(WITH_SUBJECT + "WHERE se.user_id = ? AND se.start >= ? AND se.start < ? ORDER BY se.start",
                 user_id, start_iso, end_iso)


def upcoming(user_id, from_iso: str, limit: int):
    return query(WITH_SUBJECT + "WHERE se.user_id = ? AND se.start >= ? ORDER BY se.start LIMIT ?",
                 user_id, from_iso, limit)


def from_date(user_id, from_iso: str):
    return query(WITH_SUBJECT + "WHERE se.user_id = ? AND se.start >= ? ORDER BY se.start", user_id, from_iso)


def manual_upcoming(user_id, now_iso: str):
    return query(WITH_SUBJECT + "WHERE se.user_id = ? AND se.manual = 1 AND se.end >= ? ORDER BY se.start",
                 user_id, now_iso)


def for_subject(subject_id, now_iso: str, future: bool, limit: int):
    if future:
        return query("SELECT * FROM sessions WHERE subject_id = ? AND end >= ? ORDER BY start LIMIT ?",
                     subject_id, now_iso, limit)
    return query("SELECT * FROM sessions WHERE subject_id = ? AND end < ? ORDER BY start DESC LIMIT ?",
                 subject_id, now_iso, limit)


def stats(subject_id):
    return one(f"SELECT COUNT(*) AS n, COALESCE(SUM(done), 0) AS done, COALESCE(SUM({MINUTES}), 0) AS minutes,"
               f" COALESCE(SUM(CASE WHEN done THEN {MINUTES} END), 0) AS done_min "
               "FROM sessions WHERE subject_id = ?", subject_id)


def replannable(user_id, now_iso: str, subject_ids):
    """Séances automatiques, non faites, à venir : celles que le planning peut déplacer."""
    return query(f"SELECT * FROM sessions WHERE user_id = ? AND done = 0 AND manual = 0 AND start >= ? "
                 f"AND subject_id IN ({placeholders(subject_ids)}) ORDER BY start", user_id, now_iso, *subject_ids)


def locked_from(user_id, now_iso: str):
    """Séances à venir qui ne bougent jamais : faites, ou posées à la main."""
    return query("SELECT start, end FROM sessions WHERE user_id = ? AND start >= ? AND (done = 1 OR manual = 1)",
                 user_id, now_iso)


def future(user_id, now_iso: str):
    return query("SELECT id, start, end FROM sessions WHERE user_id = ? AND start >= ?", user_id, now_iso)


def done_study_minutes(user_id):
    """Minutes d'étude déjà faites par chapitre (pour ne pas replanifier un chapitre étudié)."""
    return query(f"SELECT subject_id, topic, MAX(substr(start, 1, 10)) AS last, SUM({MINUTES}) AS minutes "
                 "FROM sessions WHERE user_id = ? AND kind = 'study' AND done = 1 GROUP BY subject_id, topic", user_id)


def insert_planned(user_id, sessions) -> None:
    execute_many("INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 [(user_id, s["subject_id"], s["start"], s["end"], s["topic"], s["kind"], s["activity"])
                  for s in sessions])


def insert_manual(user_id, subject_id, slots, topic, activity) -> None:
    execute_many("INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity, manual) "
                 "VALUES (?, ?, ?, ?, ?, 'review', ?, 1)",
                 [(user_id, subject_id, a, b, topic, activity) for a, b in slots])


def delete_ids(ids) -> None:
    execute_many("DELETE FROM sessions WHERE id = ?", [(i,) for i in ids])


def delete_replannable(user_id, now_iso: str, subject_ids) -> None:
    execute(f"DELETE FROM sessions WHERE user_id = ? AND done = 0 AND manual = 0 AND start >= ? "
            f"AND subject_id IN ({placeholders(subject_ids)})", user_id, now_iso, *subject_ids)


def delete_manual(session_id, user_id) -> None:
    execute("DELETE FROM sessions WHERE id = ? AND user_id = ? AND manual = 1", session_id, user_id)


def toggle_done(session_id, user_id) -> None:
    execute("UPDATE sessions SET done = 1 - done WHERE id = ? AND user_id = ?", session_id, user_id)
