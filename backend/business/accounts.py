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


# ---------------------------------------------------------------- informations personnelles (toutes facultatives)
# Elles préremplissent ce qui s'écrit ailleurs (lettres de motivation, recherche de stage) : rien n'est obligatoire.
LICENSE = {"oui": "Oui", "en_cours": "En cours", "non": "Non"}
INTERNSHIP = {"oui": "Oui, je cherche", "plus_tard": "Plus tard", "non": "Non"}
DETAIL_LIMITS = {"full_name": 80, "phone": 30, "home_city": 80, "study_city": 80, "field": 80, "program": 120,
                 "languages": 160, "website": 200}
DETAILS = (*DETAIL_LIMITS, "birth_date", "driving_license", "wants_internship")
PHONE_RE = re.compile(r"^\+?[\d\s.\-()]{6,30}$")


def age(birth_date: str, today) -> int | None:
    """Âge en années révolues, ou None si la date est absente ou invalide."""
    from datetime import date
    try:
        d = date.fromisoformat(birth_date or "")
    except ValueError:
        return None
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def clean_details(form, today) -> tuple[dict, list[str]]:
    """Informations personnelles nettoyées, et erreurs éventuelles (toutes les cases sont facultatives)."""
    values = {k: " ".join(str(form.get(k) or "").split())[:n] for k, n in DETAIL_LIMITS.items()}
    errors = []
    birth = (form.get("birth_date") or "").strip()
    years = age(birth, today)
    if birth and (years is None or not 8 <= years <= 99):
        errors.append("Date de naissance invalide.")
        birth = ""
    values["birth_date"] = birth
    values["driving_license"] = form.get("driving_license") if form.get("driving_license") in LICENSE else ""
    values["wants_internship"] = form.get("wants_internship") if form.get("wants_internship") in INTERNSHIP else ""
    if values["phone"] and not PHONE_RE.match(values["phone"]):
        errors.append("Numéro de téléphone invalide.")
    if values["website"] and not re.match(r"^https?://", values["website"]):
        values["website"] = "https://" + values["website"]
    return values, errors

# Domaines d'études proposés (saisie libre possible) : toutes les filières, pas seulement l'université
STUDY_FIELDS = ["Droit", "Économie et gestion", "Commerce et marketing", "Informatique", "Ingénierie", "Santé",
                "Soins infirmiers", "Sciences", "Lettres et langues", "Sciences humaines et sociales",
                "Arts et design", "Communication et journalisme", "Éducation", "Social", "Architecture et BTP",
                "Hôtellerie et tourisme", "Agriculture et environnement", "Sport"]
