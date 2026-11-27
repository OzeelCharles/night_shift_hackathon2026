"""Règles des comptes : niveaux d'études, validation des saisies."""
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8

# Niveau scolaire : sert à adapter les ressources proposées (requêtes, tri des vidéos, plateformes).
LEVELS = {
    "college": "Collège",
    "lycee": "Lycée",
    "prepa": "Classe préparatoire",
    "bts_but": "BTS / BUT",
    "licence": "Licence (L1 à L3)",
    "master": "Master",
    "ecole": "École d'ingénieur ou de commerce",
    "doctorat": "Doctorat",
    "autre": "Autre / formation continue",
}


def validate_username(username: str) -> list[str]:
    return [] if 2 <= len(username) <= 40 else ["Le nom d'utilisateur doit faire entre 2 et 40 caractères."]


def validate_account(email: str, username: str, password: str | None, require_password=True) -> list[str]:
    errors = [] if EMAIL_RE.match(email) else ["Adresse e-mail invalide."]
    errors += validate_username(username)
    if (require_password or password) and len(password or "") < MIN_PASSWORD:
        errors.append(f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères.")
    return errors


def validate_profile(level: str, school: str) -> list[str]:
    errors = []
    if level not in LEVELS:
        errors.append("Choisis ton niveau d'études.")
    if not (2 <= len(school) <= 120):
        errors.append("Indique ton école ou ton université (2 à 120 caractères).")
    return errors
