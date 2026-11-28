"""Utilisateurs."""
from .db import execute, one, query, scalar


def get(user_id):
    return one("SELECT * FROM users WHERE id = ?", user_id)


def by_email(email: str):
    return one("SELECT * FROM users WHERE email = ?", email.lower())


def email_taken(email: str, except_id: int | None = None) -> bool:
    return scalar("SELECT 1 FROM users WHERE email = ? AND id != ?", email.lower(), except_id or -1) is not None


def create(email, username, password_hash, role, level, school) -> int:
    uid = execute("INSERT INTO users (email, username, password_hash, role, level, school) VALUES (?, ?, ?, ?, ?, ?)",
                  email.lower(), username, password_hash, role, level, school)
    execute("INSERT INTO preferences (user_id) VALUES (?)", uid)
    return uid


def update(user_id, **fields) -> None:
    allowed = {"email", "username", "role", "is_active", "level", "school", "password_hash", "full_name",
               "birth_date", "phone", "home_city", "study_city", "driving_license", "wants_internship", "field",
               "program", "languages", "website",
               "is_moderator", "community_muted"}
    cols = [k for k in fields if k in allowed]
    execute(f"UPDATE users SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            *[fields[c] for c in cols], user_id)


def bump_session_version(user_id) -> None:
    """Invalide toutes les sessions ouvertes de l'utilisateur (SEC-03)."""
    execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", user_id)


def delete(user_id) -> None:
    execute("DELETE FROM users WHERE id = ?", user_id)


def any_admin() -> bool:
    return scalar("SELECT 1 FROM users WHERE role = 'admin'") is not None


def active_admin_count() -> int:
    return scalar("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1")


def list_with_counts(search: str = ""):
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM subjects s WHERE s.user_id = u.id) AS n_subjects,"
           " (SELECT COUNT(*) FROM sessions se WHERE se.user_id = u.id) AS n_sessions FROM users u")
    if search:
        return query(sql + " WHERE u.email LIKE ? OR u.username LIKE ? ORDER BY u.created_at DESC",
                     f"%{search}%", f"%{search}%")
    return query(sql + " ORDER BY u.created_at DESC")


def global_stats() -> dict:
    return {
        "users": scalar("SELECT COUNT(*) FROM users"),
        "subjects": scalar("SELECT COUNT(*) FROM subjects"),
        "documents": scalar("SELECT COUNT(*) FROM documents"),
        "sessions": scalar("SELECT COUNT(*) FROM sessions"),
        "done": scalar("SELECT COUNT(*) FROM sessions WHERE done = 1"),
    }
