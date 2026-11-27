"""Campus : affluence (bibliothèque universitaire, restaurants Crous) et menus. Règles pures.

Il n'existe aucune source publique d'affluence en temps réel : l'indicateur « en direct » vient des
signalements des utilisateurs (voir `live_level`) ; sans signalement récent, on affiche une fréquentation
habituelle **estimée** (`typical_profile`), toujours présentée comme telle."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime

# Niveaux signalables : clé -> (libellé, taux de remplissage indicatif en %)
LEVELS = {1: ("Vide", 15), 2: ("Calme", 40), 3: ("Animé", 70), 4: ("Plein", 95)}
LIVE_WINDOW_MIN = 45        # un signalement plus vieux que ça n'est plus « en direct »
HALF_LIFE_MIN = 20          # un signalement compte deux fois moins toutes les 20 minutes
REPORT_COOLDOWN_MIN = 10    # un utilisateur signale un lieu au plus une fois toutes les 10 minutes
RELIABLE_COUNT = 3          # à partir de 3 signalements, l'indicateur est jugé fiable
RADIUS_KM = 5.0
MAX_RESTAURANTS = 8
DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


# ---------------------------------------------------------------- identifiants de lieux
def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def bu_key(school: str) -> str | None:
    """Une bibliothèque par établissement : tous ses étudiants partagent les mêmes signalements."""
    slug = _slug(school)
    return f"bu:{slug}" if slug else None


def crous_key(code) -> str:
    return f"crous:{int(code)}"


def parse_level(value) -> int | None:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if level in LEVELS else None


# ---------------------------------------------------------------- affluence « en direct » (signalements)
def live_level(reports) -> dict | None:
    """Niveau actuel d'après les signalements récents : [(niveau, ancienneté en minutes), …].
    Médiane pondérée : un signalement récent pèse plus, et un seul farceur ne fait pas basculer l'indicateur."""
    fresh = [(lvl, age) for lvl, age in reports if lvl in LEVELS and 0 <= age <= LIVE_WINDOW_MIN]
    if not fresh:
        return None
    weighted = sorted(((lvl, 0.5 ** (age / HALF_LIFE_MIN)) for lvl, age in fresh))
    half, acc, chosen = sum(w for _, w in weighted) / 2, 0.0, weighted[-1][0]
    for lvl, w in weighted:
        acc += w
        if acc >= half:
            chosen = lvl
            break
    label, pct = LEVELS[chosen]
    return {"level": chosen, "label": label, "pct": pct, "count": len(fresh),
            "age": round(min(age for _, age in fresh)), "reliable": len(fresh) >= RELIABLE_COUNT}


def level_for_pct(pct: float) -> int:
    """Niveau (1 à 4) le plus proche d'un taux de remplissage estimé."""
    return min(LEVELS, key=lambda k: abs(LEVELS[k][1] - pct))


# ---------------------------------------------------------------- fréquentation habituelle (estimation)
# Profils indicatifs par heure (0-100) : ce ne sont PAS des mesures. Heure -> taux de remplissage habituel.
_BU_WEEKDAY = {8: 15, 9: 30, 10: 50, 11: 60, 12: 45, 13: 60, 14: 80, 15: 90, 16: 85, 17: 65, 18: 40, 19: 20}
_BU_SATURDAY = {9: 15, 10: 25, 11: 35, 12: 30, 13: 35, 14: 45, 15: 45, 16: 35, 17: 20}
_LUNCH = {11: 35, 12: 95, 13: 65, 14: 10}
_DINNER = {19: 55, 20: 30}


def meals_open(jours_ouvert, weekday: int) -> set[str]:
    """Services ouverts ce jour-là (« midi », « soir ») d'après les jours d'ouverture donnés par le Crous."""
    for j in jours_ouvert or []:
        if _slug(j.get("jour", "")) == DAYS_FR[weekday]:
            return {meal for meal in ("matin", "midi", "soir") if (j.get("ouverture") or {}).get(meal)}
    return set()


def typical_profile(kind: str, weekday: int, meals: set[str] | None = None) -> dict[int, int]:
    """Heure -> fréquentation habituelle estimée. Vide si le lieu est habituellement fermé."""
    if kind == "bu":
        return dict(_BU_WEEKDAY if weekday < 5 else _BU_SATURDAY if weekday == 5 else {})
    profile = {}
    if "midi" in (meals or ()):
        profile.update(_LUNCH)
    if "soir" in (meals or ()):
        profile.update(_DINNER)
    return profile


def quiet_hours(profile: dict[int, int], from_hour: int, n: int = 2) -> list[int]:
    """Les n heures les plus calmes à venir (dans l'ordre chronologique), parmi celles où le lieu est ouvert."""
    ahead = sorted((v, h) for h, v in profile.items() if h >= from_hour)
    return sorted(h for _, h in ahead[:n])


def chart_hours(profile: dict[int, int], now: datetime) -> list[dict]:
    """Barres du graphique : de la première à la dernière heure d'ouverture habituelle."""
    if not profile:
        return []
    return [{"hour": h, "pct": profile.get(h, 0), "now": h == now.hour, "open": h in profile}
            for h in range(min(profile), max(profile) + 1)]


# ---------------------------------------------------------------- restaurants Crous
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def nearby(restaurants, lat: float, lon: float, radius_km: float = RADIUS_KM, limit: int = MAX_RESTAURANTS):
    """Restaurants triés par distance. Dans le rayon ; à défaut, les 3 plus proches (distance annoncée)."""
    located = [dict(r, distance_km=haversine_km(lat, lon, r["latitude"], r["longitude"]))
               for r in restaurants if r.get("latitude") is not None and r.get("longitude") is not None]
    located.sort(key=lambda r: r["distance_km"])
    inside = [r for r in located if r["distance_km"] <= radius_km]
    return (inside[:limit], True) if inside else (located[:3], False)


def service_state(jours_ouvert, now: datetime) -> dict:
    """État du jour : fermé, ou service du midi / du soir (en cours, à venir ou terminé)."""
    meals = meals_open(jours_ouvert, now.weekday())
    if not meals & {"midi", "soir"}:
        return {"open_today": False, "meals": meals, "text": "Fermé aujourd'hui", "now": False}
    hm = now.hour + now.minute / 60
    windows = {"midi": (11.0, 14.5), "soir": (18.5, 21.0)}
    for meal in ("midi", "soir"):
        if meal in meals and windows[meal][0] <= hm < windows[meal][1]:
            return {"open_today": True, "meals": meals, "now": True,
                    "text": "Service du midi en cours" if meal == "midi" else "Service du soir en cours"}
    upcoming = [m for m in ("midi", "soir") if m in meals and hm < windows[m][0]]
    if upcoming:
        return {"open_today": True, "meals": meals, "now": False,
                "text": "Ouvert ce midi" if upcoming[0] == "midi" else "Ouvert ce soir"}
    return {"open_today": True, "meals": meals, "now": False, "text": "Services terminés pour aujourd'hui"}


def menu_for(menus, day: date) -> dict[str, list[dict]]:
    """Menu d'un jour : {« midi » : [{catégorie, plats}], …}. `menus` : la liste « data » de CROUStillant."""
    wanted = day.strftime("%d-%m-%Y")
    for entry in menus or []:
        if entry.get("date") != wanted:
            continue
        out = {}
        for meal in entry.get("repas") or []:
            cats = []
            for cat in meal.get("categories") or []:
                dishes = []
                for p in cat.get("plats") or []:
                    name = re.sub(r"\s+", " ", (p.get("libelle") or "")).strip()
                    if name and name not in dishes:
                        dishes.append(name)
                if dishes:
                    cats.append({"category": (cat.get("libelle") or "").strip(), "dishes": dishes})
            if cats:
                out[meal.get("type") or "midi"] = cats
        return out
    return {}
