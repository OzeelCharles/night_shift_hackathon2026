import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    method TEXT NOT NULL DEFAULT 'pomodoro',
    session_minutes INTEGER NOT NULL DEFAULT 50,
    break_minutes INTEGER NOT NULL DEFAULT 10,
    daily_max_minutes INTEGER NOT NULL DEFAULT 180,
    day_start TEXT NOT NULL DEFAULT '08:00',
    day_end TEXT NOT NULL DEFAULT '21:00',
    rest_days TEXT NOT NULL DEFAULT '6',
    buffer_minutes INTEGER NOT NULL DEFAULT 15,
    horizon_weeks INTEGER NOT NULL DEFAULT 4
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    goal TEXT NOT NULL CHECK (goal IN ('exam', 'a_jour')),
    exam_date TEXT,
    comfort INTEGER NOT NULL DEFAULT 3 CHECK (comfort BETWEEN 1 AND 5),
    weekly_hours REAL NOT NULL DEFAULT 3,
    topics TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    excerpt TEXT NOT NULL DEFAULT '',
    topics_found INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manuel'
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    topic TEXT NOT NULL,
    kind TEXT NOT NULL,
    activity TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS caffeine_intakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    mg INTEGER NOT NULL CHECK (mg > 0),
    taken_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_caffeine_user_taken ON caffeine_intakes(user_id, taken_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_start ON sessions(user_id, start);
CREATE INDEX IF NOT EXISTS idx_events_user_start ON events(user_id, start);
-- Dossier scolaire : notes privées de l'utilisateur. Une note survit à la suppression de sa
-- matière (subject_id passe à NULL, le nom reste dans `course`) : c'est un historique.
CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    course TEXT NOT NULL,
    value REAL NOT NULL,
    out_of REAL NOT NULL DEFAULT 20,
    coefficient REAL NOT NULL DEFAULT 1,
    label TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_grades_user ON grades(user_id);
-- Communauté : messages privés, forum et fichiers échangés. Les messages et sujets d'un utilisateur
-- supprimé disparaissent avec lui.
CREATE TABLE IF NOT EXISTS dm_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dm_pair ON dm_messages(sender_id, recipient_id);
CREATE INDEX IF NOT EXISTS idx_dm_recipient ON dm_messages(recipient_id, read_at);
CREATE TABLE IF NOT EXISTS forum_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_activity TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_topics_category ON forum_topics(category, last_activity);
CREATE TABLE IF NOT EXISTS forum_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES forum_topics(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_posts_topic ON forum_posts(topic_id);
CREATE TABLE IF NOT EXISTS shared_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER REFERENCES dm_messages(id) ON DELETE CASCADE,
    post_id INTEGER REFERENCES forum_posts(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_files_message ON shared_files(message_id);
CREATE INDEX IF NOT EXISTS idx_files_post ON shared_files(post_id);
-- Modération : un signalement garde une copie du contenu (il reste traitable si l'auteur le supprime).
-- Il survit à la suppression du contenu, de l'auteur et de celui qui signale.
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('post', 'message')),
    target_id INTEGER NOT NULL,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    content TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    topic_id INTEGER,
    reason TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    decision TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    handled_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    handled_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status, target_type, target_id);
-- Un utilisateur ne peut pas signaler deux fois le même contenu tant que le premier signalement est ouvert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_once ON reports(reporter_id, target_type, target_id) WHERE status = 'open';
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exc=None):
    """Fin de requête (ou de contexte) : on valide tout ce qui a été écrit, ou on annule en cas
    d'erreur. Chaque requête HTTP est ainsi une seule transaction."""
    db = g.pop("db", None)
    if db is not None:
        if exc is None:
            db.commit()
        else:
            db.rollback()
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        get_db().executescript(SCHEMA)
        migrate(get_db())


# ---------------------------------------------------------------- raccourcis pour les DAO
def query(sql: str, *args) -> list[sqlite3.Row]:
    return get_db().execute(sql, args).fetchall()


def one(sql: str, *args) -> sqlite3.Row | None:
    return get_db().execute(sql, args).fetchone()


def scalar(sql: str, *args):
    row = one(sql, *args)
    return row[0] if row else None


def execute(sql: str, *args) -> int:
    """Exécute une écriture ; renvoie l'identifiant de la ligne insérée (le cas échéant)."""
    return get_db().execute(sql, args).lastrowid


def execute_many(sql: str, rows) -> None:
    get_db().executemany(sql, list(rows))


def commit() -> None:
    """Validation anticipée (avant de lancer un traitement en arrière-plan qui lit la base)."""
    get_db().commit()


def placeholders(values) -> str:
    return ",".join("?" * len(values))


# Durée d'une séance ou d'un événement, en minutes, calculée par SQLite
MINUTES = "((julianday(end) - julianday(start)) * 1440)"


# Colonnes ajoutées après la première version : on les crée sur les bases existantes
# sans perdre les données.
MIGRATIONS = {
    "users": {"level": "TEXT NOT NULL DEFAULT 'autre'", "school": "TEXT NOT NULL DEFAULT ''",
              # modération de la communauté : nommé par un administrateur ; suspendu = lecture seule
              "is_moderator": "INTEGER NOT NULL DEFAULT 0", "community_muted": "INTEGER NOT NULL DEFAULT 0"},
    "subjects": {"resources_status": "TEXT NOT NULL DEFAULT 'ok'", "resources_sig": "TEXT NOT NULL DEFAULT ''",
                 # date d'examen choisie par l'utilisateur (prioritaire) et origine de la date retenue :
                 # manuel, agenda (épreuve de l'emploi du temps) ou derniere_seance (par défaut)
                 "exam_manual": "TEXT", "exam_source": "TEXT NOT NULL DEFAULT ''"},
    "events": {"subject_id": "INTEGER REFERENCES subjects(id) ON DELETE SET NULL",
               "kind": "TEXT NOT NULL DEFAULT 'autre'",
               "uid": "TEXT NOT NULL DEFAULT ''"},  # clé de synchronisation d'un événement importé
    "sessions": {"manual": "INTEGER NOT NULL DEFAULT 0"},
    "preferences": {"bedtime": "TEXT NOT NULL DEFAULT '23:00'", "wake_time": "TEXT NOT NULL DEFAULT '07:00'"},
    "resources": {"kind": "TEXT NOT NULL DEFAULT 'web'", "thumbnail": "TEXT NOT NULL DEFAULT ''",
                  "meta": "TEXT NOT NULL DEFAULT ''", "saved": "INTEGER NOT NULL DEFAULT 0"},
}


def migrate(db: sqlite3.Connection):
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject_id)")
    if db.execute("PRAGMA user_version").fetchone()[0] < 1:
        # Dates d'examen enregistrées avant l'existence de exam_source : on les considère comme
        # choisies par l'utilisateur, pour ne jamais les écraser automatiquement. (Une seule fois.)
        db.execute("UPDATE subjects SET exam_manual = exam_date, exam_source = 'manuel' "
                   "WHERE exam_source = '' AND exam_date IS NOT NULL")
        db.execute("PRAGMA user_version = 1")
    db.commit()
