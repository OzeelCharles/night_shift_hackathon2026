"""Autocomplétion des établissements de France (collèges, lycées, supérieur).

Les données viennent de app/data/ecoles_france.json, généré par scripts/build_schools.py à partir
des jeux de données publics de l'Éducation nationale et de l'Enseignement supérieur.
La recherche ne dépend d'aucun service extérieur : rien de ce que tape l'utilisateur ne sort du serveur.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache

DATA = os.path.join(os.path.dirname(__file__), "data", "ecoles_france.json")
SECONDARY = {"Collège", "Lycée"}
STOP = {"de", "du", "des", "la", "le", "les", "d", "l", "et", "en", "a", "au", "aux"}


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@lru_cache(maxsize=1)
def _index() -> list[tuple]:
    try:
        with open(DATA, encoding="utf-8") as f:
            rows = json.load(f)["rows"]
    except (OSError, ValueError, KeyError):
        rows = []
    out = []
    for name, city, kind, sigle in rows:
        words = set(_key(f"{name} {city}").split()) - STOP
        sig = _key(sigle).replace(" ", "")
        out.append((name, city, kind, sigle, _key(name), words, sig))
    return out


def count() -> int:
    return len(_index())


def display_value(name: str, city: str, kind: str, sigle: str) -> str:
    """Valeur enregistrée dans le profil : courte et non ambiguë.
    ENSAI (sigle connu) ; « Collège Jean Moulin (Rennes) » (72 homonymes en France)."""
    if sigle and len(sigle) <= 15:
        return sigle
    if kind in SECONDARY and city:
        return f"{name} ({city})"
    return name


def search(query: str, level: str | None = None, limit: int = 12) -> list[dict]:
    q = _key(query)
    if len(q) < 2:
        return []
    tokens = [t for t in q.split() if t not in STOP] or q.split()
    q_compact = q.replace(" ", "")
    prefer_secondary = level in ("college", "lycee")
    results = []
    for name, city, kind, sigle, name_key, words, sig in _index():
        score = 0.0
        if sig and sig == q_compact:
            score = 100  # sigle exact : « ENSAI », « INSA »
        elif sig and sig.startswith(q_compact) and len(q_compact) >= 3:
            score = 70
        else:
            # chaque mot tapé doit commencer un mot du nom ou de la ville (« univ renn »)
            if not all(any(w.startswith(t) for w in words) for t in tokens):
                continue
            score = 40
            if name_key.startswith(q):
                score += 20
            score += sum(5 for t in tokens if t in words)  # mots complets
        if prefer_secondary == (kind in SECONDARY):
            score += 8  # un lycéen cherche plutôt un lycée, un étudiant plutôt le supérieur
        score -= len(name) / 40  # à score égal, le nom le plus court (« Université de Rennes »)
        results.append((score, name, city, kind, sigle))
    results.sort(key=lambda r: -r[0])
    out, seen = [], set()
    for _, name, city, kind, sigle in results:
        value = display_value(name, city, kind, sigle)
        dedup = " ".join(w for w in _key(value).split() if w not in STOP)  # « Rennes 2 » = « de Rennes 2 »
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append({"value": value, "name": name, "city": city, "kind": kind, "sigle": sigle})
        if len(out) >= limit:
            break
    return out
