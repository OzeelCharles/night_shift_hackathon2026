"""Règles des fichiers déposés (SEC-06) : quotas, photos, nom de stockage."""
import os

USER_QUOTA_BYTES = 200 * 1024 * 1024  # supports de cours stockés au plus par utilisateur
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # par photo envoyée à la lecture automatique
MAX_PHOTOS_PER_UPLOAD = 10
MAX_STEM = 80  # caractères gardés du nom d'origine dans le nom de stockage
MAX_EXT = 10


def stored_name(unique: str, safe_name: str, default: str = "document") -> str:
    """Nom de stockage : identifiant unique + nom d'origine (déjà nettoyé) raccourci, extension conservée.
    Reste loin des limites des systèmes de fichiers (255 octets par nom, 260 caractères de chemin sous Windows)."""
    stem, ext = os.path.splitext(safe_name or default)
    if len(ext) > MAX_EXT:
        stem, ext = stem + ext, ""
    return f"{unique}_{(stem or default)[:MAX_STEM]}{ext.lower()}"


def over_quota(used: int, incoming: int, quota: int = USER_QUOTA_BYTES) -> bool:
    return used + incoming > quota
