"""Outils partagés par les services."""
from werkzeug.exceptions import NotFound


def owned(row):
    """Ligne appartenant à l'utilisateur, sinon 404 (on ne révèle pas l'existence des données d'autrui)."""
    if row is None:
        raise NotFound()
    return row
