"""Données de la recherche de stage : ses propres tables (préfixe stg_), créées au démarrage.

Le schéma du cœur n'est pas modifié : tout est supprimé en cascade avec l'utilisateur.
"""
from backend.dao.db import execute, get_db, one, query

SCHEMA = """
CREATE TABLE IF NOT EXISTS stg_profile (           -- profil candidat, valable pour tout cursus
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL DEFAULT '',
    field TEXT NOT NULL DEFAULT '',                -- domaine d'études : droit, informatique, soins infirmiers…
    program TEXT NOT NULL DEFAULT '',              -- intitulé exact de la formation
    skills TEXT NOT NULL DEFAULT '',
    experiences TEXT NOT NULL DEFAULT '',
    qualities TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',             -- « 2 mois », « 6 mois »…
    start TEXT NOT NULL DEFAULT '',                -- « juin 2027 »…
    contact TEXT NOT NULL DEFAULT ''               -- coordonnées en tête de lettre
);
CREATE TABLE IF NOT EXISTS stg_wishes (            -- postes recherchés
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS stg_offers (            -- offres repérées et suivi des candidatures
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'a_postuler',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS stg_letters (           -- lettres de motivation générées puis retouchées
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    offer_id INTEGER REFERENCES stg_offers(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',               -- « Groq » ou « modèle »
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_stg_offers_user ON stg_offers(user_id, created_at);
"""
# Ce qui ne sert qu'aux candidatures. Nom, villes, études, coordonnées, permis et langues viennent du compte
# (les anciennes colonnes full_name, field, program, location et contact restent, inutilisées).
PROFILE_FIELDS = ("skills", "experiences", "qualities", "duration", "start")


def init_schema() -> None:
    get_db().executescript(SCHEMA)


# ---------------------------------------------------------------- profil
def profile(user_id) -> dict:
    row = one("SELECT * FROM stg_profile WHERE user_id = ?", user_id)
    return dict(row) if row else {k: "" for k in PROFILE_FIELDS}


def save_profile(user_id, values: dict) -> None:
    cols = ", ".join(PROFILE_FIELDS)
    marks = ", ".join("?" for _ in PROFILE_FIELDS)
    updates = ", ".join(f"{k} = excluded.{k}" for k in PROFILE_FIELDS)
    execute(f"INSERT INTO stg_profile (user_id, {cols}) VALUES (?, {marks}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}", user_id, *(values[k] for k in PROFILE_FIELDS))


# ---------------------------------------------------------------- postes recherchés
def wishes(user_id):
    return query("SELECT * FROM stg_wishes WHERE user_id = ? ORDER BY created_at, id", user_id)


def wish(wish_id, user_id):
    return one("SELECT * FROM stg_wishes WHERE id = ? AND user_id = ?", wish_id, user_id)


def add_wish(user_id, title, location) -> int:
    return execute("INSERT INTO stg_wishes (user_id, title, location) VALUES (?, ?, ?)", user_id, title, location)


def delete_wish(wish_id, user_id) -> None:
    execute("DELETE FROM stg_wishes WHERE id = ? AND user_id = ?", wish_id, user_id)


# ---------------------------------------------------------------- offres
def offers(user_id):
    return query("SELECT o.*, (SELECT COUNT(*) FROM stg_letters l WHERE l.offer_id = o.id) AS n_letters "
                 "FROM stg_offers o WHERE o.user_id = ? ORDER BY o.created_at DESC, o.id DESC", user_id)


def offer(offer_id, user_id):
    return one("SELECT * FROM stg_offers WHERE id = ? AND user_id = ?", offer_id, user_id)


def add_offer(user_id, title, company, location, url, source, description) -> int:
    return execute("INSERT INTO stg_offers (user_id, title, company, location, url, source, description) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", user_id, title, company, location, url, source, description)


def set_status(offer_id, user_id, status) -> None:
    execute("UPDATE stg_offers SET status = ? WHERE id = ? AND user_id = ?", status, offer_id, user_id)


def delete_offer(offer_id, user_id) -> None:
    execute("DELETE FROM stg_offers WHERE id = ? AND user_id = ?", offer_id, user_id)


# ---------------------------------------------------------------- lettres
def letters(user_id):
    return query("SELECT id, offer_id, title, company, author, created_at FROM stg_letters WHERE user_id = ? "
                 "ORDER BY created_at DESC, id DESC", user_id)


def letter(letter_id, user_id):
    return one("SELECT * FROM stg_letters WHERE id = ? AND user_id = ?", letter_id, user_id)


def add_letter(user_id, offer_id, title, company, content, author) -> int:
    return execute("INSERT INTO stg_letters (user_id, offer_id, title, company, content, author) "
                   "VALUES (?, ?, ?, ?, ?, ?)", user_id, offer_id, title, company, content, author)


def update_letter(letter_id, user_id, content) -> None:
    execute("UPDATE stg_letters SET content = ? WHERE id = ? AND user_id = ?", content, letter_id, user_id)


def delete_letter(letter_id, user_id) -> None:
    execute("DELETE FROM stg_letters WHERE id = ? AND user_id = ?", letter_id, user_id)
