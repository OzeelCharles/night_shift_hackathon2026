"""Données du vérificateur de CV : sa table (préfixe vcv_), créée au démarrage.

Un seul CV par étudiant, et seulement son texte : le fichier déposé n'est jamais gardé. La ligne est
supprimée à la demande et en cascade avec le compte. Les offres sont lues dans stg_offers (plugin stages),
en lecture seule.
"""
from backend.dao.db import execute, one, query

SCHEMA = """
CREATE TABLE IF NOT EXISTS vcv_resumes (           -- texte extrait du CV (le fichier est supprimé)
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 1,
    signals TEXT NOT NULL DEFAULT '{}',            -- JSON : nombre de tableaux, caractères par page…
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


# ---------------------------------------------------------------- CV
def resume(user_id):
    return one("SELECT * FROM vcv_resumes WHERE user_id = ?", user_id)


def save_resume(user_id, filename, text, pages, signals_json) -> None:
    """Un nouveau dépôt remplace l'ancien."""
    execute("INSERT INTO vcv_resumes (user_id, filename, text, pages, signals) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET filename = excluded.filename, text = excluded.text, "
            "pages = excluded.pages, signals = excluded.signals, uploaded_at = datetime('now', 'localtime')",
            user_id, filename, text, pages, signals_json)


def delete_resume(user_id) -> None:
    execute("DELETE FROM vcv_resumes WHERE user_id = ?", user_id)


# ---------------------------------------------------------------- offres suivies (lecture seule)
def offers(user_id):
    return query("SELECT id, title, company, description FROM stg_offers WHERE user_id = ? "
                 "ORDER BY created_at DESC, id DESC", user_id)


def offer(offer_id, user_id):
    return one("SELECT id, title, company, location, description FROM stg_offers WHERE id = ? AND user_id = ?",
               offer_id, user_id)
