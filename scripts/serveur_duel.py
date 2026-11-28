"""Serveur jetable pour le duel de sécurité : python scripts/serveur_duel.py  puis http://127.0.0.1:5001

- base SQLite et fichiers déposés dans un dossier temporaire, effacé à l'arrêt ;
- aucun accès à internet (ENABLE_WEB=False) et aucune IA (Claude, Groq) ;
- écoute uniquement sur 127.0.0.1 ;
- CSRF actif, comme en production.

Comptes créés au démarrage (mots de passe propres à ce serveur jetable) :
  admin@duel.local / AdminDuel-2026!   (administrateur)
  alice@duel.local / AliceDuel-2026!   (utilisateur)
  bob@duel.local   / BobDuel-2026!     (utilisateur)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Le premier admin est créé à partir de ces variables (l'application l'affiche encore dans le terminal : c'est F-07).
os.environ["ADMIN_EMAIL"] = "admin@duel.local"
os.environ["ADMIN_PASSWORD"] = "AdminDuel-2026!"
# Aucune clé d'API dans ce processus, même si elle existe dans la session.
for key in ("ANTHROPIC_API_KEY", "GROQ_API_KEY"):
    os.environ.pop(key, None)

from backend import create_app  # noqa: E402

USERS = [("alice@duel.local", "Alice", "AliceDuel-2026!"), ("bob@duel.local", "Bob", "BobDuel-2026!")]


def build(folder: str):
    app = create_app({
        "DATABASE": os.path.join(folder, "duel.sqlite"),
        "UPLOAD_FOLDER": os.path.join(folder, "uploads"),
        "ENABLE_WEB": False,
        "REVISION_AI": False,
        "STAGES_AI": False,
        "VISION_AI": False,
        "SECRET_KEY": os.urandom(32).hex(),
    })
    with app.app_context():
        from backend.dao import db, users
        from backend.services import auth_service
        for email, name, password in USERS:
            if not users.by_email(email):
                auth_service.create_user(email, name, password, level="licence", school="École du duel")
        db.commit()
    return app


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="rivezli-duel-") as folder:
        print(f"Base jetable : {folder}")
        print("Rivezli (duel) sur http://127.0.0.1:5001 — Ctrl+C pour arrêter et tout effacer.")
        build(folder).run(host="127.0.0.1", port=int(os.environ.get("DUEL_PORT", 5001)), debug=False)
