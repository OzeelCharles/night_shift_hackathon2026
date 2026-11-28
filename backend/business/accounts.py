"""Règles des comptes : niveaux d'études, validation des saisies, politique de mot de passe."""
import re
import unicodedata

from . import moderation

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
MIN_DISTINCT_CHARS = 4  # « aaaaaaaaaa », « ababababab »… refusés
_EDGES = re.compile(r"^[\W\d_]+|[\W\d_]+$")  # chiffres et symboles ajoutés au début ou à la fin
_LEET = str.maketrans({"@": "a", "4": "a", "0": "o", "3": "e", "$": "s", "5": "s", "1": "i", "!": "i", "7": "t"})

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


def validate_username(username: str, banned: "moderation.Terms | None" = None) -> list[str]:
    """Longueur, et pas de terme interdit (le nom est visible par d'autres personnes, SEC-05)."""
    if not 2 <= len(username) <= 40:
        return ["Le nom d'utilisateur doit faire entre 2 et 40 caractères."]
    if banned is not None and moderation.contains_banned(username, banned):
        return ["Ce nom d'utilisateur contient un terme interdit (insulte, propos haineux ou contenu sexuel) : "
                "choisis-en un autre."]
    return []


def _fold(value: str) -> str:
    """Minuscules sans accents ni espaces superflus (comparaison tolérante)."""
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return value.strip().lower()


def _alnum(value: str) -> str:
    return re.sub(r"[\W_]+", "", _fold(value))


def _common_forms(password: str) -> set[str]:
    """Formes sous lesquelles un mot de passe courant se cache : casse, chiffres ou symboles en bordure,
    substitutions classiques (p@ssw0rd)."""
    low = _fold(password)
    forms = set()
    # séparateurs glissés au milieu (« mot de passe », « m.o.t-d_e », « 1234*5678 », « 1qaz#2wsx »…) : on les
    # retire aussi, tous sauf les symboles qui remplacent une lettre (@ $ !), traités par _LEET (F-036)
    for base in (low, re.sub(r"[\s._\-]+", "", low), re.sub(r"[^a-z0-9@$!]+", "", low)):
        forms |= {base, _EDGES.sub("", base), base.translate(_LEET), _EDGES.sub("", base).translate(_LEET),
                  _EDGES.sub("", base.translate(_LEET))}
        leet = base.translate(_LEET)
        forms |= {re.sub(r"[\W_]+", "", leet), _EDGES.sub("", re.sub(r"[\W_]+", "", base))}
    return {f for f in forms if f}


def password_errors(password: str, email: str = "", username: str = "", common=frozenset()) -> list[str]:
    """Politique SEC-03 : 10 caractères minimum, pas un mot de passe courant (liste `common`, en minuscules),
    pas l'adresse e-mail (ni sa partie avant @) ni le nom."""
    password = password or ""
    if len(password) < MIN_PASSWORD:
        return [f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères."]
    errors = []
    if len(set(password.lower())) < MIN_DISTINCT_CHARS or _common_forms(password) & set(common):
        errors.append("Ce mot de passe est trop courant : choisis-en un autre "
                      "(par exemple trois ou quatre mots sans rapport, séparés par des tirets).")
    personal = {_alnum(email), _alnum(email.split("@")[0]), _alnum(username)} - {""}
    if _alnum(password) in personal:
        errors.append("Le mot de passe ne doit reprendre ni ton adresse e-mail ni ton nom.")
    return errors


def validate_account(email: str, username: str, password: str | None, require_password=True,
                     common=frozenset(), banned: "moderation.Terms | None" = None) -> list[str]:
    errors = [] if EMAIL_RE.match(email) else ["Adresse e-mail invalide."]
    errors += validate_username(username, banned)
    if require_password or password:
        errors += password_errors(password or "", email, username, common)
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
