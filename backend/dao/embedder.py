"""Modèle d'embeddings local du RAG : EmbeddingGemma-300m via fastembed (ONNX, sur le processeur, sans clé).

Retenu après comparaison sur des questions de cours reformulées (« fourchette où se trouve le paramètre » doit
trouver l'intervalle de confiance) : 6/6, contre 3/6 pour les modèles MiniLM, mpnet et potion multilingues ;
environ 100 passages par seconde. Téléchargé une fois (~1,2 Go) dans instance/models (exclu de git).

Repli : si le modèle est désactivé (tests : RAG_EMBEDDINGS=False) ou indisponible, on utilise les embeddings
par hachage du projet (business/embeddings.py). Le nom du modèle est enregistré avec l'index : un changement
de modèle déclenche la réindexation.
"""
from __future__ import annotations

import os
import threading

import numpy as np
from flask import current_app

from backend.business import embeddings as hashed

MODEL = "google/embeddinggemma-300m"
QUERY_PREFIX = "task: search result | query: "      # préfixes attendus par EmbeddingGemma
FALLBACK = "hash512"

_lock = threading.Lock()
_models: dict[str, object] = {}
_failed: set[str] = set()


def _model():
    """Le modèle fastembed (chargé une fois par processus), ou None s'il est désactivé ou indisponible."""
    cfg = current_app.config
    if not cfg.get("RAG_EMBEDDINGS", not current_app.testing):
        return None
    name = cfg.get("RAG_MODEL", MODEL)
    if name in _failed:
        return None
    with _lock:
        if name not in _models:
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")   # téléchargement HTTP classique (antivirus, proxy)
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            try:
                from fastembed import TextEmbedding
                _models[name] = TextEmbedding(name, cache_dir=os.path.join(current_app.instance_path, "models"))
            except Exception:
                current_app.logger.exception("RAG : modèle d'embeddings %s indisponible, repli sur le hachage", name)
                _failed.add(name)
                return None
        return _models[name]


def model_name() -> str:
    return current_app.config.get("RAG_MODEL", MODEL) if _model() is not None else FALLBACK


def embed_passages(texts: list[str], headings: list[str]) -> list[np.ndarray]:
    model = _model()
    if model is None:
        return [np.asarray(hashed.embed(f"{h} {t}"), dtype=np.float32) for t, h in zip(texts, headings)]
    docs = [f"title: {h or 'none'} | text: {t}" for t, h in zip(texts, headings)]
    with _lock:
        return [np.asarray(v, dtype=np.float32) for v in model.embed(docs, batch_size=16)]


def embed_query(text: str) -> np.ndarray:
    model = _model()
    if model is None:
        return np.asarray(hashed.embed(text), dtype=np.float32)
    with _lock:
        return np.asarray(next(iter(model.embed([QUERY_PREFIX + text]))), dtype=np.float32)
