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
CREATE INDEX IF NOT EXISTS idx_sessions_user_start ON sessions(user_id, start);
CREATE INDEX IF NOT EXISTS idx_events_user_start ON events(user_id, start);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        get_db().executescript(SCHEMA)
        migrate(get_db())


# Colonnes ajoutées après la première version : on les crée sur les bases existantes
# sans perdre les données.
MIGRATIONS = {
    "users": {"level": "TEXT NOT NULL DEFAULT 'autre'", "school": "TEXT NOT NULL DEFAULT ''"},
    "subjects": {"resources_status": "TEXT NOT NULL DEFAULT 'ok'", "resources_sig": "TEXT NOT NULL DEFAULT ''",
                 # date d'examen choisie par l'utilisateur (prioritaire) et origine de la date retenue :
                 # manuel, agenda (épreuve de l'emploi du temps) ou derniere_seance (par défaut)
                 "exam_manual": "TEXT", "exam_source": "TEXT NOT NULL DEFAULT ''"},
    "events": {"subject_id": "INTEGER REFERENCES subjects(id) ON DELETE SET NULL",
               "kind": "TEXT NOT NULL DEFAULT 'autre'",
               "uid": "TEXT NOT NULL DEFAULT ''"},  # clé de synchronisation d'un événement importé
    "sessions": {"manual": "INTEGER NOT NULL DEFAULT 0"},
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
    # Dates d'examen enregistrées avant l'existence de exam_source : on les considère comme
    # choisies par l'utilisateur, pour ne jamais les écraser automatiquement.
    db.execute("UPDATE subjects SET exam_manual = exam_date, exam_source = 'manuel' "
               "WHERE exam_source = '' AND exam_date IS NOT NULL")
    db.commit()
