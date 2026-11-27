"""Matières."""
from .db import execute, execute_many, one, query

COLUMNS = {"name", "goal", "exam_date", "comfort", "weekly_hours", "topics", "exam_manual", "exam_source",
           "resources_status", "resources_sig"}


def get_owned(subject_id, user_id):
    return one("SELECT * FROM subjects WHERE id = ? AND user_id = ?", subject_id, user_id)


def list_for_user(user_id):
    return query("SELECT * FROM subjects WHERE user_id = ? ORDER BY name", user_id)


def create(user_id, **fields) -> int:
    cols = [c for c in fields if c in COLUMNS]
    return execute(f"INSERT INTO subjects (user_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
                   user_id, *[fields[c] for c in cols])


def update(subject_id, **fields) -> None:
    cols = [c for c in fields if c in COLUMNS]
    execute(f"UPDATE subjects SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            *[fields[c] for c in cols], subject_id)


def set_status(subject_ids, status: str) -> None:
    execute_many("UPDATE subjects SET resources_status = ? WHERE id = ?", [(status, i) for i in subject_ids])


def delete(subject_id) -> None:
    execute("DELETE FROM subjects WHERE id = ?", subject_id)


def with_progress(user_id, now_iso: str):
    """Matières avec minutes prévues / faites et séances manquées (tableau de bord)."""
    minutes = "(julianday(se.end) - julianday(se.start)) * 1440"
    return query(
        "SELECT su.*, "
        f" COALESCE(SUM({minutes}), 0) AS planned,"
        f" COALESCE(SUM(CASE WHEN se.done THEN {minutes} END), 0) AS done_min,"
        " SUM(CASE WHEN se.done = 0 AND se.end < ? THEN 1 ELSE 0 END) AS missed "
        "FROM subjects su LEFT JOIN sessions se ON se.subject_id = su.id "
        "WHERE su.user_id = ? GROUP BY su.id ORDER BY su.exam_date IS NULL, su.exam_date, su.name",
        now_iso, user_id)


def list_all_with_owner():
    return query("SELECT s.*, u.email, (SELECT COUNT(*) FROM documents d WHERE d.subject_id = s.id) AS n_docs,"
                 " (SELECT COUNT(*) FROM sessions se WHERE se.subject_id = s.id) AS n_sessions "
                 "FROM subjects s JOIN users u ON u.id = s.user_id ORDER BY s.created_at DESC")
