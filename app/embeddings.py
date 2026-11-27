"""Embeddings de noms de cours et similarité cosinus.

Chaque intitulé est transformé en un vecteur dense de dimension fixe (DIM), par « feature
hashing » à la manière de fastText : on extrait des traits (racines de mots, abréviations
développées, n-grammes de caractères de 2 à 4 lettres) puis on les projette dans le vecteur
avec une fonction de hachage stable et un signe aléatoire, avant de normaliser (norme L2 = 1).
La similarité entre deux intitulés est alors le cosinus de leurs vecteurs (= produit scalaire).

Pourquoi ce choix plutôt qu'un modèle neuronal ? Il tourne sans GPU, sans téléchargement de
modèle ni connexion, en quelques microsecondes, et il gère ce qui compte pour des intitulés
d'emploi du temps : abréviations (« Proba »), saisie partielle (« prob »), fautes, pluriels.
Il est lexical : il ne sait pas que « Probabilités » et « Aléatoire » sont liés par le sens.
La fonction `embed` est le seul point à remplacer pour brancher un modèle sémantique.
"""
from __future__ import annotations

import hashlib
import math
from functools import lru_cache

from .matching import _stem, normalize

DIM = 512
WEIGHTS = {"word": 1.0, "prefix": 0.6, "ngram": 0.35}


def _bucket(feature: str) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    n = int.from_bytes(h, "little")
    return n % DIM, (1.0 if (n >> 63) & 1 else -1.0)


def _features(text: str):
    for w in normalize(text).split():
        if w.isdigit():
            yield f"num:{w}", WEIGHTS["word"]
            continue
        stem = _stem(w)
        yield f"w:{stem}", WEIGHTS["word"]
        # début de mot : rapproche une saisie partielle (« prob ») du mot complet
        for k in (3, 4, 5):
            if len(stem) >= k:
                yield f"p:{stem[:k]}", WEIGHTS["prefix"]
        padded = f"<{stem}>"
        for n in (2, 3, 4):
            for i in range(len(padded) - n + 1):
                yield f"g{n}:{padded[i:i + n]}", WEIGHTS["ngram"]


@lru_cache(maxsize=4096)
def embed(text: str) -> tuple[float, ...]:
    """Vecteur unitaire de dimension DIM (tuple, pour pouvoir être mis en cache)."""
    v = [0.0] * DIM
    for feat, weight in _features(text):
        i, sign = _bucket(feat)
        v[i] += sign * weight
    norm = math.sqrt(sum(x * x for x in v))
    return tuple(x / norm for x in v) if norm else tuple(v)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return max(0.0, sum(x * y for x, y in zip(a, b)))  # vecteurs déjà normalisés


def similarity(query: str, candidate: str) -> float:
    """Cosinus entre la saisie et l'intitulé entier, ou avec chacun de ses mots si la saisie
    tient en un mot : « dro » doit trouver « Droit des contrats » dès les premières lettres."""
    q = embed(query)
    best = cosine(q, embed(candidate))
    if len(normalize(query).split()) == 1:
        for w in normalize(candidate).split():
            best = max(best, cosine(q, embed(w)))
    return round(best, 3)


def rank(query: str, candidates: list[str], min_score: float = 0.25) -> list[tuple[str, float]]:
    """Classe des intitulés par similarité cosinus décroissante avec la requête."""
    if not any(embed(query)):
        return []
    scored = [(c, similarity(query, c)) for c in candidates]
    return sorted([s for s in scored if s[1] >= min_score], key=lambda s: -s[1])
