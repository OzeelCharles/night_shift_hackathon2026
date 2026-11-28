"""Mise en forme en français et lecture tolérante des saisies (sans dépendance au web)."""
from datetime import date, datetime, time

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def iso(dt: datetime) -> str:
    """Format de stockage des dates-heures : AAAA-MM-JJTHH:MM."""
    return dt.isoformat(timespec="minutes")


def to_datetime(v) -> datetime:
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def date_fr(d) -> str:
    """« 9 décembre 2026 »."""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def day_fr(v) -> str:
    """« mercredi 9 décembre »."""
    d = v if isinstance(v, date) and not isinstance(v, datetime) else to_datetime(v)
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def number_fr(v) -> str:
    """4.0 -> « 4 », 2.5 -> « 2,5 »."""
    v = float(v or 0)
    return str(int(v)) if v.is_integer() else f"{v:.1f}".replace(".", ",")


def bounded_int(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def bounded_float(value, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def parse_time(value, default: time | None = None) -> time | None:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        return default


# BDD-06 : dates plausibles seulement (la base refuse aussi les années hors de 1900-2200 ; 2199 au plus ici
# pour laisser la place aux répétitions hebdomadaires)
MIN_YEAR, MAX_YEAR = 1900, 2199
MAX_ID = 2 ** 63 - 1  # entier SQLite


def parse_date(value) -> date | None:
    try:
        d = date.fromisoformat(value or "")
    except (TypeError, ValueError):
        return None
    return d if MIN_YEAR <= d.year <= MAX_YEAR else None


def parse_id(value) -> int | None:
    """Identifiant saisi (« 12 ») ; None s'il n'est pas un entier positif stockable par SQLite."""
    value = str(value or "").strip()
    return int(value) if value.isdigit() and 0 < int(value) <= MAX_ID else None
