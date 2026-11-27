"""Communauté : messages privés, forum et fichiers partagés."""
from .db import execute, one, query, scalar


# ---------------------------------------------------------------- annuaire
def search_users(user_id, q: str, limit=20):
    """Autres utilisateurs actifs, par nom (jamais l'adresse e-mail : elle reste privée)."""
    like = f"%{q}%"
    return query("SELECT id, username, level, school FROM users WHERE is_active = 1 AND id != ? "
                 "AND (username LIKE ? OR school LIKE ?) ORDER BY username LIMIT ?", user_id, like, like, limit)


def public_user(user_id):
    return one("SELECT id, username, level, school FROM users WHERE id = ? AND is_active = 1", user_id)


# ---------------------------------------------------------------- messages privés
def add_message(sender_id, recipient_id, body) -> int:
    return execute("INSERT INTO dm_messages (sender_id, recipient_id, body) VALUES (?, ?, ?)",
                   sender_id, recipient_id, body)


def thread(user_id, other_id, limit=200):
    rows = query("SELECT * FROM (SELECT * FROM dm_messages WHERE (sender_id = ? AND recipient_id = ?) "
                 "OR (sender_id = ? AND recipient_id = ?) ORDER BY id DESC LIMIT ?) ORDER BY id",
                 user_id, other_id, other_id, user_id, limit)
    return rows


def mark_read(user_id, other_id) -> None:
    execute("UPDATE dm_messages SET read_at = datetime('now', 'localtime') "
            "WHERE recipient_id = ? AND sender_id = ? AND read_at IS NULL", user_id, other_id)


def conversations(user_id):
    """Un interlocuteur par ligne, avec le dernier message et le nombre de messages non lus."""
    return query(
        "SELECT u.id, u.username, u.school, u.level, m.body AS last_body, m.created_at AS last_at, "
        "m.sender_id AS last_sender, "
        "(SELECT COUNT(*) FROM dm_messages x WHERE x.sender_id = u.id AND x.recipient_id = ? "
        " AND x.read_at IS NULL) AS unread "
        "FROM users u JOIN dm_messages m ON m.id = (SELECT MAX(id) FROM dm_messages WHERE "
        " (sender_id = ? AND recipient_id = u.id) OR (sender_id = u.id AND recipient_id = ?)) "
        "ORDER BY m.id DESC", user_id, user_id, user_id)


def unread_count(user_id) -> int:
    return scalar("SELECT COUNT(*) FROM dm_messages WHERE recipient_id = ? AND read_at IS NULL", user_id)


# ---------------------------------------------------------------- forum
def create_topic(author_id, category, title) -> int:
    return execute("INSERT INTO forum_topics (author_id, category, title) VALUES (?, ?, ?)", author_id, category, title)


def add_post(topic_id, author_id, body) -> int:
    pid = execute("INSERT INTO forum_posts (topic_id, author_id, body) VALUES (?, ?, ?)", topic_id, author_id, body)
    execute("UPDATE forum_topics SET last_activity = datetime('now', 'localtime') WHERE id = ?", topic_id)
    return pid


def get_topic(topic_id):
    return one("SELECT t.*, u.username AS author FROM forum_topics t JOIN users u ON u.id = t.author_id "
               "WHERE t.id = ?", topic_id)


def topics(category: str | None, q: str, limit: int, offset: int):
    where, args = [], []
    if category:
        where.append("t.category = ?")
        args.append(category)
    if q:
        where.append("(t.title LIKE ? OR EXISTS (SELECT 1 FROM forum_posts p WHERE p.topic_id = t.id AND p.body LIKE ?))")
        args += [f"%{q}%", f"%{q}%"]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return query(f"SELECT t.*, u.username AS author, u.level AS author_level, "
                 f"(SELECT COUNT(*) FROM forum_posts p WHERE p.topic_id = t.id) - 1 AS replies "
                 f"FROM forum_topics t JOIN users u ON u.id = t.author_id {clause} "
                 f"ORDER BY t.last_activity DESC, t.id DESC LIMIT ? OFFSET ?", *args, limit, offset)


def count_topics(category: str | None, q: str) -> int:
    where, args = [], []
    if category:
        where.append("t.category = ?")
        args.append(category)
    if q:
        where.append("(t.title LIKE ? OR EXISTS (SELECT 1 FROM forum_posts p WHERE p.topic_id = t.id AND p.body LIKE ?))")
        args += [f"%{q}%", f"%{q}%"]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return scalar(f"SELECT COUNT(*) FROM forum_topics t {clause}", *args)


def category_counts() -> dict:
    return {r["category"]: r["n"] for r in query("SELECT category, COUNT(*) AS n FROM forum_topics GROUP BY category")}


def posts(topic_id):
    return query("SELECT p.*, u.username AS author, u.level AS author_level, u.school AS author_school, "
                 "u.role AS author_role, u.is_moderator AS author_is_moderator "
                 "FROM forum_posts p JOIN users u ON u.id = p.author_id WHERE p.topic_id = ? ORDER BY p.id", topic_id)


def get_post(post_id):
    return one("SELECT * FROM forum_posts WHERE id = ?", post_id)


def delete_post(post_id) -> None:
    execute("DELETE FROM forum_posts WHERE id = ?", post_id)


def delete_topic(topic_id) -> None:
    execute("DELETE FROM forum_topics WHERE id = ?", topic_id)


# ---------------------------------------------------------------- fichiers joints
def add_file(owner_id, filename, stored_path, size, message_id=None, post_id=None) -> int:
    return execute("INSERT INTO shared_files (owner_id, filename, stored_path, size, message_id, post_id) "
                   "VALUES (?, ?, ?, ?, ?, ?)", owner_id, filename, stored_path, size, message_id, post_id)


def files_of_messages(message_ids):
    ids = list(message_ids)
    if not ids:
        return []
    return query(f"SELECT * FROM shared_files WHERE message_id IN ({','.join('?' * len(ids))}) ORDER BY id", *ids)


def files_of_posts(post_ids):
    ids = list(post_ids)
    if not ids:
        return []
    return query(f"SELECT * FROM shared_files WHERE post_id IN ({','.join('?' * len(ids))}) ORDER BY id", *ids)


def get_file(file_id):
    """Un fichier avec, s'il est dans un message privé, ses deux participants."""
    return one("SELECT f.*, m.sender_id, m.recipient_id FROM shared_files f "
               "LEFT JOIN dm_messages m ON m.id = f.message_id WHERE f.id = ?", file_id)


def paths_for_user(user_id) -> list[str]:
    """Fichiers déposés par l'utilisateur, ainsi que ceux des conversations et sujets qui disparaîtront avec lui."""
    return [r["stored_path"] for r in query(
        "SELECT stored_path FROM shared_files WHERE owner_id = ? "
        "OR message_id IN (SELECT id FROM dm_messages WHERE sender_id = ? OR recipient_id = ?) "
        "OR post_id IN (SELECT id FROM forum_posts WHERE author_id = ? "
        "OR topic_id IN (SELECT id FROM forum_topics WHERE author_id = ?))",
        user_id, user_id, user_id, user_id, user_id)]


def paths_for_topic(topic_id) -> list[str]:
    return [r["stored_path"] for r in query(
        "SELECT stored_path FROM shared_files WHERE post_id IN (SELECT id FROM forum_posts WHERE topic_id = ?)", topic_id)]


def paths_for_post(post_id) -> list[str]:
    return [r["stored_path"] for r in query("SELECT stored_path FROM shared_files WHERE post_id = ?", post_id)]


def my_documents(user_id):
    """Supports de cours de l'utilisateur, proposés au partage."""
    return query("SELECT d.id, d.filename, s.name AS subject FROM documents d JOIN subjects s ON s.id = d.subject_id "
                 "WHERE s.user_id = ? ORDER BY s.name, d.filename", user_id)


# ---------------------------------------------------------------- messages privés : accès unitaire
def get_message(message_id):
    return one("SELECT m.*, s.username AS sender, r.username AS recipient FROM dm_messages m "
               "JOIN users s ON s.id = m.sender_id JOIN users r ON r.id = m.recipient_id WHERE m.id = ?", message_id)


def delete_message(message_id) -> None:
    execute("DELETE FROM dm_messages WHERE id = ?", message_id)


def paths_for_message(message_id) -> list[str]:
    return [r["stored_path"] for r in query("SELECT stored_path FROM shared_files WHERE message_id = ?", message_id)]


def filenames_for_message(message_id) -> list[str]:
    return [r["filename"] for r in query("SELECT filename FROM shared_files WHERE message_id = ?", message_id)]


# ---------------------------------------------------------------- signalements
def add_report(reporter_id, target_type, target_id, author_id, content, context, topic_id, reason, details) -> int | None:
    """None si ce signalement existe déjà (même personne, même contenu, encore ouvert)."""
    if scalar("SELECT 1 FROM reports WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND status = 'open'",
              reporter_id, target_type, target_id):
        return None
    return execute("INSERT INTO reports (reporter_id, target_type, target_id, author_id, content, context, topic_id, "
                   "reason, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   reporter_id, target_type, target_id, author_id, content, context, topic_id, reason, details)


def reported_ids(user_id, target_type, ids) -> set:
    ids = list(ids)
    if not ids:
        return set()
    rows = query(f"SELECT target_id FROM reports WHERE reporter_id = ? AND target_type = ? AND status = 'open' "
                 f"AND target_id IN ({','.join('?' * len(ids))})", user_id, target_type, *ids)
    return {r["target_id"] for r in rows}


_REPORT_COLUMNS = ("r.*, rep.username AS reporter, au.username AS author, au.role AS author_role, "
                   "au.is_moderator AS author_is_moderator, au.community_muted AS author_muted, hb.username AS handler")
_REPORT_JOINS = ("FROM reports r LEFT JOIN users rep ON rep.id = r.reporter_id LEFT JOIN users au ON au.id = r.author_id "
                 "LEFT JOIN users hb ON hb.id = r.handled_by")


def open_reports():
    return query(f"SELECT {_REPORT_COLUMNS} {_REPORT_JOINS} WHERE r.status = 'open' ORDER BY r.id")


def recent_closed(limit=30):
    return query(f"SELECT {_REPORT_COLUMNS} {_REPORT_JOINS} WHERE r.status = 'closed' "
                 f"GROUP BY r.target_type, r.target_id, r.handled_at ORDER BY r.handled_at DESC, r.id DESC LIMIT ?", limit)


def get_report(report_id):
    return one(f"SELECT {_REPORT_COLUMNS} {_REPORT_JOINS} WHERE r.id = ?", report_id)


def close_reports(target_type, target_id, decision, handled_by, note) -> None:
    """Clôt d'un coup tous les signalements ouverts sur le même contenu."""
    execute("UPDATE reports SET status = 'closed', decision = ?, handled_by = ?, note = ?, "
            "handled_at = datetime('now', 'localtime') WHERE status = 'open' AND target_type = ? AND target_id = ?",
            decision, handled_by, note, target_type, target_id)


def open_count() -> int:
    return scalar("SELECT COUNT(DISTINCT target_type || ':' || target_id) FROM reports WHERE status = 'open'")


def muted_users():
    return query("SELECT id, username, school FROM users WHERE community_muted = 1 ORDER BY username")


def moderators():
    return query("SELECT id, username, school FROM users WHERE is_moderator = 1 AND role != 'admin' AND is_active = 1 "
                 "ORDER BY username")
