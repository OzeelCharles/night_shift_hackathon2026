"""Outils partagés par les services."""
import os
import threading

from flask import g, has_request_context
from werkzeug.exceptions import NotFound


def owned(row):
    """Ligne appartenant à l'utilisateur, sinon 404 (on ne révèle pas l'existence des données d'autrui)."""
    if row is None:
        raise NotFound()
    return row


# ---------------------------------------------------------------- fichiers écrits pendant une requête (F-037)
def track_written(path: str) -> None:
    """Fichier déposé pendant la requête : supprimé si la requête est annulée (la ligne en base l'est aussi)."""
    if has_request_context():
        g.setdefault("written_files", []).append(path)


def discard_written() -> None:
    """Requête annulée : les fichiers qu'elle a écrits ne doivent pas rester orphelins sur le disque."""
    if not has_request_context():
        return
    for path in g.pop("written_files", []):
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- un verrou par utilisateur (F-034)
_locks: dict = {}
_locks_guard = threading.Lock()


def user_lock(user_id) -> threading.Lock:
    """Sérialise, dans ce processus, les dépôts d'un même compte (lecture du quota puis écriture)."""
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())
