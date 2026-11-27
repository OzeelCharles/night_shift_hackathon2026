"""Configuration par défaut. Toute clé peut être surchargée par `create_app(test_config)`."""
import os
import secrets

# Fonctionnalités chargées au démarrage, dans cet ordre (voir core/features.py).
# Pour brancher une nouvelle fonctionnalité sans toucher au cœur : EXTRA_FEATURES.
FEATURES = [
    "backend.controllers.auth",
    "backend.controllers.main",
    "backend.controllers.planning",
    "backend.controllers.agenda",
    "backend.controllers.subjects",
    "backend.controllers.resources",
    "backend.controllers.caffeine",
    "backend.controllers.api",
    "backend.controllers.admin",
    "backend.controllers.dossier",
    "backend.controllers.assistant",  # chatbot (bulle flottante), modèle de Groq
    "backend.plugins.revision",  # atelier de révision : fiches, QCM notés, carnet de notes, exercices
]


def load_env(path: str) -> None:
    """Lit le fichier .env (lignes CLÉ=valeur, # pour les commentaires) dans les variables d'environnement.

    Une variable déjà définie dans le terminal garde la priorité ; une valeur vide est ignorée.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            value = value.strip("\"'")
            if key and value and not os.environ.get(key):
                os.environ[key] = value


def _secret_key(instance_path: str) -> str:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def defaults(instance_path: str) -> dict:
    return {
        "SECRET_KEY": _secret_key(instance_path),
        "DATABASE": os.path.join(instance_path, "revision.sqlite"),
        "UPLOAD_FOLDER": os.path.join(instance_path, "uploads"),
        "MAX_CONTENT_LENGTH": 25 * 1024 * 1024,
        "ENABLE_WEB": True,  # recherche de ressources sur internet
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "FEATURES": list(FEATURES),
        "EXTRA_FEATURES": [m for m in os.environ.get("EXTRA_FEATURES", "").split(",") if m.strip()],
    }
