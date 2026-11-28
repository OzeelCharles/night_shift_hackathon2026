"""Tentatives d'authentification récentes (limitation par compte et par IP, SEC-03).

Stockées dans la base de l'application : l'état est propre à chaque instance (une base par application)
et partagé entre les processus d'un même déploiement (plusieurs workers WSGI). Les clés (e-mail, IP) sont
hachées : la table ne conserve aucune adresse en clair.
Chaque écriture est validée immédiatement : une tentative réservée est visible des requêtes simultanées
(et le verrou d'écriture de SQLite n'est tenu qu'un instant).
"""
import hashlib

from .db import commit, execute, scalar


def _key(kind: str, key: str) -> str:
    return hashlib.sha256(f"{kind}\0{key}".encode("utf-8", "replace")).hexdigest()


def record(kind: str, key: str, at: float) -> int:
    """Enregistre une tentative et la valide ; renvoie son identifiant (ordre d'arrivée)."""
    rowid = execute("INSERT INTO auth_attempts (kind, key, at) VALUES (?, ?, ?)", kind, _key(kind, key), at)
    commit()
    return rowid


def count_upto(kind: str, key: str, since: float, rowid: int) -> int:
    """Tentatives de la fenêtre enregistrées jusqu'à `rowid` inclus."""
    return scalar("SELECT COUNT(*) FROM auth_attempts WHERE kind = ? AND key = ? AND at >= ? AND rowid <= ?",
                  kind, _key(kind, key), since, rowid)


def forget(*rowids: int) -> None:
    for rowid in rowids:
        execute("DELETE FROM auth_attempts WHERE rowid = ?", rowid)
    commit()


def clear(kind: str, key: str) -> None:
    execute("DELETE FROM auth_attempts WHERE kind = ? AND key = ?", kind, _key(kind, key))
    commit()


def purge_before(at: float) -> None:
    """Oublie les tentatives trop anciennes pour compter encore."""
    execute("DELETE FROM auth_attempts WHERE at < ?", at)
    commit()
