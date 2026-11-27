"""Sources de données du campus sur internet :
- restaurants et menus des Crous : API CROUStillant (communautaire, gratuite, sans clé, 200 requêtes/min par IP,
  usage non commercial) ;
- géocodage d'une ville : Géoplateforme de l'IGN (service public).

Les réponses sont gardées en mémoire (les restaurants 6 h, un menu 30 min) ; si le service ne répond plus,
on garde la dernière donnée connue plutôt que de n'afficher rien. Toute erreur remonte en `Unavailable`."""
from __future__ import annotations

import threading
import time

import requests
from flask import current_app

CROUS_API = "https://api.croustillant.menu/v1"
GEOCODER = "https://data.geopf.fr/geocodage/search/"
UA = {"User-Agent": "PlanificateurRevisions/1.0 (application educative)"}
TIMEOUT = 8
RESTAURANTS_TTL = 6 * 3600
MENU_TTL = 30 * 60

_cache: dict = {}
_lock = threading.Lock()


class Unavailable(Exception):
    """Service injoignable, désactivé ou réponse inattendue."""


def _enabled() -> None:
    if not current_app.config.get("ENABLE_WEB"):
        raise Unavailable("Les recherches sur internet sont désactivées.")


def _cached(key: str, ttl: float, load):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        value = load()
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        if hit:
            return hit[1]  # donnée périmée plutôt que rien
        raise Unavailable(str(e)) from e
    with _lock:
        _cache[key] = (now, value)
    return value


def _get_json(url: str, **params):
    r = requests.get(url, params=params or None, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def restaurants() -> list[dict]:
    """Tous les restaurants et cafétérias Crous de France (coordonnées, horaires, jours d'ouverture)."""
    _enabled()

    def load():
        payload = _get_json(f"{CROUS_API}/restaurants")
        if not payload.get("success"):
            raise ValueError("réponse CROUStillant refusée")
        return payload["data"]
    return _cached("restaurants", RESTAURANTS_TTL, load)


def menu(code: int) -> list[dict]:
    """Menus à venir d'un restaurant (liste d'un élément par jour). Liste vide si le restaurant n'en publie pas
    (certains ne le font jamais : l'API répond alors 404, ce n'est pas une panne)."""
    _enabled()

    def load():
        r = requests.get(f"{CROUS_API}/restaurants/{int(code)}/menu", headers=UA, timeout=TIMEOUT)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        payload = r.json()
        if not payload.get("success"):
            raise ValueError("menu indisponible")
        return payload["data"]
    return _cached(f"menu:{int(code)}", MENU_TTL, load)


def geocode_city(name: str) -> tuple[float, float, str] | None:
    """(latitude, longitude, nom) d'une commune, None si elle est introuvable."""
    _enabled()
    name = " ".join((name or "").split())[:80]
    if not name:
        return None

    def load():
        features = _get_json(GEOCODER, q=name, type="municipality", limit=1).get("features") or []
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        return (lat, lon, features[0]["properties"].get("city") or name)
    return _cached(f"geo:{name.lower()}", 30 * 24 * 3600, load)
