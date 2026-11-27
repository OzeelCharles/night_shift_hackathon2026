"""Liste des établissements de France (backend/data/ecoles_france.json, construite par
scripts/build_schools.py à partir des données publiques de l'Éducation nationale et de l'ESR)."""
import json
import os
from functools import lru_cache

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ecoles_france.json")


@lru_cache(maxsize=1)
def all_rows() -> tuple:
    """(nom, ville, type, sigle) pour chaque établissement ; vide si le fichier manque."""
    try:
        with open(DATA, encoding="utf-8") as f:
            return tuple(tuple(r) for r in json.load(f)["rows"])
    except (OSError, ValueError, KeyError):
        return ()
