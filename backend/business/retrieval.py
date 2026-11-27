"""Recherche dans les supports de cours (RAG) : règles pures.

Recherche hybride :
- par le sens : similarité cosinus entre l'embedding de la question et celui de chaque passage ;
- par les mots : BM25 (index plein texte FTS5 de SQLite), utile pour une notation ou un nom propre
  (« ARIMA », « Cramér-Rao ») ;
- les deux classements sont fusionnés par Reciprocal Rank Fusion (RRF) : un passage bien classé par l'un
  ou l'autre remonte, sans avoir à comparer des scores d'échelles différentes.
Chaque passage trouvé est ensuite renvoyé avec ses voisins (contexte), et cité [S1], [S2]…
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np

from .extractor import STOPWORDS

RRF_K = 60
CANDIDATES = 20        # passages retenus par chaque méthode avant fusion
TOP_K = 4              # passages renvoyés au modèle
NEIGHBORS = 1          # passages voisins ajoutés de chaque côté
MAX_CONTEXT_CHARS = 5200
MIN_COSINE = 0.25      # en dessous, un passage n'a pas de rapport avec la question
CITATION = re.compile(r"\[S(\d+)\]")
# mots courts sans intérêt pour la recherche (la liste de l'extracteur ne garde que les mots longs)
SHORT_STOPWORDS = set("""
les des une est sont son ses sur par pour dans avec que qui quoi quel quelle quels quelles comment pourquoi
quand ont aux elle ils nous vous leur tout tous toute cette ces cet mon ton mes tes peut faut fait etre avoir
entre sans sous plus moins tres bien car donc mais the and for what how
""".split())


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def fts_query(question: str, max_terms: int = 12) -> str:
    """Requête FTS5 tolérante : mots utiles de la question, réunis par OR, en préfixe pour les mots longs
    (« estimateurs » trouve « estimation »)."""
    terms = []
    for w in re.findall(r"[a-z0-9]+", _plain(question)):
        if len(w) < 3 or w in STOPWORDS or w in SHORT_STOPWORDS or w in terms:
            continue
        terms.append(w)
    parts = [f'"{t[:-2]}"*' if len(t) >= 7 else f'"{t}"' for t in terms[:max_terms]]
    return " OR ".join(parts)


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def to_blob(vector) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def vector_ranking(query: np.ndarray, ids: list[int], blobs: list[bytes], n: int = CANDIDATES,
                   min_score: float = MIN_COSINE) -> list[tuple[int, float]]:
    """(id, cosinus) des passages les plus proches de la question, du plus proche au moins proche."""
    if not ids:
        return []
    matrix = np.vstack([from_blob(b) for b in blobs])
    q = np.asarray(query, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(q) or 1.0)
    scores = matrix @ q / np.where(norms == 0, 1.0, norms)
    order = np.argsort(-scores)[:n]
    return [(ids[i], float(scores[i])) for i in order if scores[i] >= min_score]


def context(hit: dict, neighbors: list[dict], used: set) -> list[dict]:
    """Passages à donner pour un passage trouvé : lui et ses voisins de la même section, sans répéter un passage
    déjà donné. `used` (ensemble de (document, position)) est mis à jour."""
    if (hit["document_id"], hit["position"]) in used:
        return []
    part = [p for p in neighbors if p["heading"] == hit["heading"] and (p["document_id"], p["position"]) not in used]
    used.update((p["document_id"], p["position"]) for p in part)
    return sorted(part, key=lambda p: p["position"])


def renumber(passages: list[dict], start: int) -> list[dict]:
    """Numérote les sources à partir de start+1 (plusieurs recherches dans une même réponse)."""
    return [p | {"source": f"S{start + i}"} for i, p in enumerate(passages, 1)]


def filter_citations(answer: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Retire les citations inventées ([S9] sans source) ; renvoie (réponse, sources réellement citées).

    Si la réponse ne cite rien, toutes les sources consultées sont gardées (« passages consultés »)."""
    known = {s["source"]: s for s in sources}
    cited = []
    for n in CITATION.findall(answer):
        label = f"S{n}"
        if label in known and known[label] not in cited:
            cited.append(known[label])
    answer = CITATION.sub(lambda m: m.group(0) if f"S{m.group(1)}" in known else "", answer)
    return answer, (cited or sources)
