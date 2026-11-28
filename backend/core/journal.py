"""SEC-10 : journal de sécurité structuré (une ligne JSON par événement).

Connexions réussies ou échouées, blocages, actions des administrateurs. Jamais de mot de passe : seuls les champs
passés explicitement sont écrits, et l'appelant n'y met que des identifiants (e-mail, id, IP).
"""
import json
import logging
from datetime import datetime, timezone

from flask import g, has_request_context, request

logger = logging.getLogger("rivezli.securite")
logger.setLevel(logging.INFO)


def log(event: str, **fields) -> dict:
    """Écrit l'événement dans le journal et le renvoie (pour l'enregistrer aussi en base si besoin)."""
    data = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
    if has_request_context():
        data["ip"] = request.remote_addr or ""
        user = g.get("user")
        if user is not None:
            data["actor_id"] = user["id"]
    data.update(fields)
    logger.info(json.dumps(data, ensure_ascii=False, default=str))
    return data


def init_app(app) -> None:
    """Hors tests : le journal sort sur la sortie d'erreur standard (récupérée par le serveur ou l'hébergeur)."""
    if not app.config.get("TESTING") and not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
