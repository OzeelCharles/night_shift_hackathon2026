"""Recherche d'établissement : par sigle (« ENSAI ») ou par débuts de mots (« univ renn »)."""
from __future__ import annotations

import re
import unicodedata

SECONDARY = {"Collège", "Lycée"}
STOP = {"de", "du", "des", "la", "le", "les", "d", "l", "et", "en", "a", "au", "aux"}


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def build_index(rows) -> list[tuple]:
    """Précalcule, pour chaque établissement, les clés de recherche."""
    return [(name, city, kind, sigle, _key(name), set(_key(f"{name} {city}").split()) - STOP,
             _key(sigle).replace(" ", "")) for name, city, kind, sigle in rows]


def display_value(name: str, city: str, kind: str, sigle: str) -> str:
    """Valeur enregistrée dans le profil, courte et non ambiguë :
    « ENSAI » (sigle connu) ; « Collège Jean Moulin (Rennes) » (72 homonymes en France)."""
    if sigle and len(sigle) <= 15:
        return sigle
    if kind in SECONDARY and city:
        return f"{name} ({city})"
    return name


def _score(entry, q: str, tokens: list[str], prefer_secondary: bool) -> float | None:
    name, city, kind, sigle, name_key, words, sig = entry
    q_compact = q.replace(" ", "")
    if sig and sig == q_compact:
        score = 100.0  # sigle exact : « ENSAI », « INSA »
    elif sig and sig.startswith(q_compact) and len(q_compact) >= 3:
        score = 70.0
    elif all(any(w.startswith(t) for w in words) for t in tokens):
        # chaque mot tapé commence un mot du nom ou de la ville (« univ renn »)
        score = 40.0 + (20 if name_key.startswith(q) else 0) + sum(5 for t in tokens if t in words)
    else:
        return None
    if prefer_secondary == (kind in SECONDARY):
        score += 8  # un lycéen cherche plutôt un lycée, un étudiant plutôt le supérieur
    return score - len(name) / 40  # à score égal, le nom le plus court (« Université de Rennes »)


def search(index: list[tuple], query: str, level: str | None = None, limit: int = 12) -> list[dict]:
    q = _key(query)
    if len(q) < 2:
        return []
    tokens = [t for t in q.split() if t not in STOP] or q.split()
    prefer_secondary = level in ("college", "lycee")
    scored = [(s, e) for e in index if (s := _score(e, q, tokens, prefer_secondary)) is not None]
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, (name, city, kind, sigle, *_rest) in scored:
        value = display_value(name, city, kind, sigle)
        dedup = " ".join(w for w in _key(value).split() if w not in STOP)  # « Rennes 2 » = « de Rennes 2 »
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append({"value": value, "name": name, "city": city, "kind": kind, "sigle": sigle})
        if len(out) >= limit:
            break
    return out
