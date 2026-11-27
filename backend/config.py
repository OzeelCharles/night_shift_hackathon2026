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
    "backend.controllers.campus",
    "backend.controllers.api",
    "backend.controllers.admin",
    "backend.controllers.dossier",
    "backend.plugins.revision",  # atelier de révision : fiches, QCM notés, carnet de notes, exercices
    "backend.plugins.stages",  # recherche de stage : offres, suivi des candidatures, lettres de motivation
]


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
