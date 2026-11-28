"""Configuration par défaut. Toute clé peut être surchargée par `create_app(test_config)`."""
import os
import secrets
from datetime import timedelta

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
    "backend.controllers.community",
    "backend.controllers.api",
    "backend.controllers.admin",
    "backend.controllers.dossier",
    "backend.controllers.assistant",  # chatbot (bulle flottante), modèle de Groq
    "backend.plugins.revision",  # atelier de révision : fiches, QCM notés, carnet de notes, exercices
    "backend.plugins.stages",  # recherche de stage : offres, suivi des candidatures, lettres de motivation
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


MIN_SECRET_KEY = 32


def production_errors(config: dict) -> list[str]:
    """DEP-02 / F-06 : réglages qui empêchent de démarrer en production (RIVEZLI_ENV=production)."""
    errors = []
    if len(os.environ.get("SECRET_KEY", "")) < MIN_SECRET_KEY:
        errors.append(f"SECRET_KEY doit être définie dans l'environnement ({MIN_SECRET_KEY} caractères au moins ; "
                      "par exemple : python -c \"import secrets; print(secrets.token_hex(32))\").")
    if os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes") or config.get("DEBUG"):
        errors.append("FLASK_DEBUG doit être désactivé : le débogueur interactif permet d'exécuter du code.")
    return errors


def defaults(instance_path: str) -> dict:
    production = os.environ.get("RIVEZLI_ENV", "").lower() == "production"
    return {
        # Production (RIVEZLI_ENV=production) : HSTS et cookie de session réservé à HTTPS (SEC-09)
        "PRODUCTION": production,
        "SESSION_COOKIE_SECURE": production,
        "SECRET_KEY": _secret_key(instance_path),
        "DATABASE": os.path.join(instance_path, "revision.sqlite"),
        "UPLOAD_FOLDER": os.path.join(instance_path, "uploads"),
        "MAX_CONTENT_LENGTH": 25 * 1024 * 1024,
        "DONATION_URL": os.environ.get("DONATION_URL", "").strip(),  # page de don (https://…) ; vide = pas de bouton
        "ENABLE_WEB": True,  # recherche de ressources sur internet
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        # SEC-03 : une session (cookie signé) n'est plus acceptée 7 jours après la connexion
        "PERMANENT_SESSION_LIFETIME": timedelta(days=7),
        # SEC-03 : limitation des tentatives (actives aussi en TESTING)
        "LOGIN_MAX_FAILURES": 5,  # échecs par compte et par IP…
        "LOGIN_WINDOW_SECONDS": 15 * 60,  # …sur 15 minutes
        "LOGIN_FAILURE_MIN_SECONDS": 0.5,  # durée minimale d'un échec de connexion (temps de réponse égalisé)
        "REGISTER_MAX_PER_IP": 5,  # inscriptions par IP…
        "REGISTER_WINDOW_SECONDS": 60 * 60,  # …sur une heure
        # SEC-08 : générations par l'IA par utilisateur et par jour (QCM, exercices, notebooks, lettres, photos)
        "LLM_DAILY_QUOTA": 20,
        "FEATURES": list(FEATURES),
        "EXTRA_FEATURES": [m for m in os.environ.get("EXTRA_FEATURES", "").split(",") if m.strip()],
    }
