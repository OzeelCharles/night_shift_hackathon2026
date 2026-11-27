"""Données de l'atelier de révision : ses propres tables (préfixe rev_), créées au démarrage.

Le schéma du cœur n'est pas modifié : tout est supprimé en cascade avec l'utilisateur ou la matière.
"""
import json

from backend.dao.db import execute, get_db, one, query

SCHEMA = """
CREATE TABLE IF NOT EXISTS rev_difficulty (
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter TEXT NOT NULL DEFAULT '',          -- '' = toute la matière
    difficulty TEXT NOT NULL,
    PRIMARY KEY (subject_id, chapter)
);
CREATE TABLE IF NOT EXISTS rev_corpus (         -- texte extrait des supports (cache)
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    source TEXT NOT NULL,                       -- doc:<id> ou wiki
    text TEXT NOT NULL,
    PRIMARY KEY (subject_id, source)
);
CREATE TABLE IF NOT EXISTS rev_quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL,
    questions TEXT NOT NULL,
    answers TEXT,
    total INTEGER NOT NULL,
    correct INTEGER,
    score REAL,                                 -- note sur 20, NULL tant que le QCM n'est pas rendu
    seconds INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    submitted_at TEXT
);
CREATE TABLE IF NOT EXISTS rev_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    solution TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (subject_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_rev_quizzes_subject ON rev_quizzes(subject_id, submitted_at);
"""


def init_schema() -> None:
    get_db().executescript(SCHEMA)


# ---------------------------------------------------------------- difficulté
def difficulties(subject_id) -> dict[str, str]:
    return {r["chapter"]: r["difficulty"] for r in query(
        "SELECT chapter, difficulty FROM rev_difficulty WHERE subject_id = ?", subject_id)}


def set_difficulties(subject_id, values: dict[str, str]) -> None:
    execute("DELETE FROM rev_difficulty WHERE subject_id = ?", subject_id)
    for chapter, difficulty in values.items():
        execute("INSERT INTO rev_difficulty (subject_id, chapter, difficulty) VALUES (?, ?, ?)",
                subject_id, chapter, difficulty)


def difficulties_for_user(user_id) -> dict[int, str]:
    return {r["subject_id"]: r["difficulty"] for r in query(
        "SELECT d.subject_id, d.difficulty FROM rev_difficulty d JOIN subjects s ON s.id = d.subject_id "
        "WHERE s.user_id = ? AND d.chapter = ''", user_id)}


# ---------------------------------------------------------------- texte des supports
def corpus(subject_id) -> dict[str, str]:
    return {r["source"]: r["text"] for r in query("SELECT source, text FROM rev_corpus WHERE subject_id = ?",
                                                    subject_id)}


def save_corpus(subject_id, source, text) -> None:
    execute("INSERT OR REPLACE INTO rev_corpus (subject_id, source, text) VALUES (?, ?, ?)", subject_id, source, text)


def drop_corpus(subject_id, source) -> None:
    execute("DELETE FROM rev_corpus WHERE subject_id = ? AND source = ?", subject_id, source)


# ---------------------------------------------------------------- QCM et carnet de notes
def create_quiz(user_id, subject_id, chapter, difficulty, questions) -> int:
    return execute("INSERT INTO rev_quizzes (user_id, subject_id, chapter, difficulty, questions, total) "
                   "VALUES (?, ?, ?, ?, ?, ?)", user_id, subject_id, chapter, difficulty,
                   json.dumps(questions, ensure_ascii=False), len(questions))


def get_quiz(quiz_id, user_id):
    row = one("SELECT q.*, s.name AS subject_name FROM rev_quizzes q JOIN subjects s ON s.id = q.subject_id "
              "WHERE q.id = ? AND q.user_id = ?", quiz_id, user_id)
    if row is None:
        return None
    quiz = dict(row)
    quiz["questions"] = json.loads(quiz["questions"])
    quiz["answers"] = json.loads(quiz["answers"]) if quiz["answers"] else None
    return quiz


def submit_quiz(quiz_id, answers, correct, score) -> None:
    execute("UPDATE rev_quizzes SET answers = ?, correct = ?, score = ?, submitted_at = datetime('now', 'localtime'),"
            " seconds = CAST((julianday('now', 'localtime') - julianday(created_at)) * 86400 AS INTEGER) "
            "WHERE id = ? AND submitted_at IS NULL", json.dumps(answers), correct, score, quiz_id)


def attempts(subject_id):
    """Essais rendus, du plus ancien au plus récent."""
    return query("SELECT id, chapter, difficulty, total, correct, score, seconds, submitted_at FROM rev_quizzes "
                 "WHERE subject_id = ? AND submitted_at IS NOT NULL ORDER BY submitted_at, id", subject_id)


def chapter_results(subject_id):
    return query("SELECT questions, answers FROM rev_quizzes WHERE subject_id = ? AND submitted_at IS NOT NULL",
                 subject_id)


def scores_by_subject(user_id) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for r in query("SELECT q.subject_id, q.score FROM rev_quizzes q JOIN subjects s ON s.id = q.subject_id "
                   "WHERE s.user_id = ? AND q.submitted_at IS NOT NULL ORDER BY q.submitted_at, q.id", user_id):
        out.setdefault(r["subject_id"], []).append(r["score"])
    return out


# ---------------------------------------------------------------- exercices
def add_exercise(subject_id, chapter, title, statement, solution, difficulty, source, url, fingerprint) -> bool:
    before = get_db().total_changes
    execute("INSERT OR IGNORE INTO rev_exercises (subject_id, chapter, title, statement, solution, difficulty, "
            "source, url, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            subject_id, chapter, title[:200], statement, solution, difficulty, source[:200], url, fingerprint)
    return get_db().total_changes > before


def exercises(subject_id):
    return query("SELECT * FROM rev_exercises WHERE subject_id = ? ORDER BY chapter, id", subject_id)


def get_exercise(exercise_id, user_id):
    return one("SELECT e.* FROM rev_exercises e JOIN subjects s ON s.id = e.subject_id WHERE e.id = ? AND s.user_id = ?",
               exercise_id, user_id)


def delete_exercise(exercise_id) -> None:
    execute("DELETE FROM rev_exercises WHERE id = ?", exercise_id)
