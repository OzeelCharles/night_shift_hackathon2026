"""RAG sur les supports de cours : indexation des documents déposés et recherche des passages utiles.

Indexation : à chaque dépôt (en arrière-plan), et au moment d'une recherche pour tout support pas encore
indexé (documents déposés avant l'existence du RAG, changement de modèle). Un support = des passages
(business/chunking) avec leur embedding (dao/embedder) et leur index plein texte (FTS5).

Recherche : hybride (sens + mots, fusion RRF, voir business/retrieval), limitée aux supports de l'utilisateur
et, si elle est connue, à la matière. Chaque passage trouvé est renvoyé avec ses voisins et numéroté [S1]…
pour que l'assistant cite ses sources.
"""
from __future__ import annotations

import threading

from flask import current_app, url_for

from backend.business import chunking, extractor, retrieval
from backend.dao import embedder
from backend.dao import rag as dao
from backend.dao.db import commit


def index_document(doc, user_id) -> int:
    """Découpe, calcule les embeddings et enregistre les passages d'un support ; renvoie leur nombre."""
    chunks = chunking.chunk_pages(extractor.extract_pages(doc["stored_path"]))
    vectors = embedder.embed_passages([c.text for c in chunks], [c.heading for c in chunks]) if chunks else []
    dao.replace_chunks(doc["id"], doc["subject_id"], user_id, embedder.model_name(),
                       [(c.position, c.page, c.heading, c.text, retrieval.to_blob(v)) for c, v in zip(chunks, vectors)])
    return len(chunks)


def ensure_indexed(user_id, subject_id=None) -> int:
    """Indexe les supports qui ne le sont pas encore ; chaque support est validé aussitôt."""
    model = embedder.model_name()
    done = 0
    for doc in dao.documents_to_index(user_id, model, subject_id):
        try:
            index_document(doc, user_id)
        except Exception:  # un support illisible ne bloque pas les autres
            current_app.logger.exception("RAG : indexation impossible du document %s", doc["id"])
            dao.replace_chunks(doc["id"], doc["subject_id"], user_id, model, [])
        commit()  # base libérée tout de suite (la requête peut ensuite attendre Groq)
        done += 1
    return done


_job = threading.Lock()          # une seule indexation à la fois (le modèle occupe le processeur)
_queued: set = set()             # utilisateurs (ou "*" pour tous) dont l'indexation est déjà prévue
_queued_lock = threading.Lock()


def _background() -> bool:
    return current_app.config.get("RAG_BACKGROUND", not current_app.testing)


def _worker(app, who):
    with app.app_context():
        with _job:
            with _queued_lock:
                _queued.discard(who)
            try:
                users = dao.users_to_index(embedder.model_name()) if who == "*" else [who]
                for uid in users:
                    ensure_indexed(uid)
            except Exception:
                app.logger.exception("RAG : indexation en arrière-plan interrompue")


def _start(app, who) -> None:
    with _queued_lock:
        if who in _queued:
            return
        _queued.add(who)
    threading.Thread(target=_worker, args=(app, who), daemon=True).start()


def index_async(user_id, **_) -> list[str]:
    """Après un dépôt de supports : indexation sans faire attendre l'utilisateur."""
    if _background():
        commit()  # le traitement en arrière-plan doit voir les documents de la requête en cours
        _start(current_app._get_current_object(), user_id)
    return []


def index_all_async(app) -> None:
    """Au démarrage : indexe les supports déposés avant le RAG (ou avec un autre modèle)."""
    with app.app_context():
        if _background():
            _start(app, "*")


def search(user_id, question: str, subject_id=None, top_k: int = retrieval.TOP_K) -> dict:
    """Passages des supports qui répondent à la question : {"passages": [...], "sources": [...], "en_cours": [...]}.

    Les supports pas encore indexés le sont en arrière-plan (la question n'attend pas) et sont signalés dans
    « en_cours » ; sans arrière-plan (tests), ils sont indexés tout de suite."""
    pending = []
    if _background():
        pending = [d["filename"] for d in dao.documents_to_index(user_id, embedder.model_name(), subject_id)]
        if pending:
            _start(current_app._get_current_object(), user_id)
    else:
        ensure_indexed(user_id, subject_id)
    rows = dao.vectors(user_id, subject_id)
    if not rows:
        return {"passages": [], "sources": [], "en_cours": pending}
    min_score = retrieval.MIN_COSINE if embedder.model_name() != embedder.FALLBACK else 0.05
    by_meaning = retrieval.vector_ranking(embedder.embed_query(question), [r["id"] for r in rows],
                                          [r["vector"] for r in rows], min_score=min_score)
    by_words = dao.keyword_ranking(user_id, retrieval.fts_query(question), subject_id)
    best = [cid for cid, _ in retrieval.rrf([[cid for cid, _ in by_meaning], by_words])[:top_k]]
    if not best:
        return {"passages": [], "sources": [], "en_cours": pending}

    found = {r["id"]: r for r in dao.chunks_by_ids(user_id, best)}
    hits = [(found[cid]["document_id"], found[cid]["position"]) for cid in best if cid in found]
    passages, budget, used = [], retrieval.MAX_CONTEXT_CHARS, set()
    for doc_id, pos in hits:
        rows = [dict(r) | {"document_id": doc_id}
                for r in dao.window(user_id, doc_id, pos - retrieval.NEIGHBORS, pos + retrieval.NEIGHBORS)]
        hit = next((p for p in rows if p["position"] == pos), None)
        if hit is None or budget <= 200:
            continue
        part = retrieval.context(hit, rows, used)  # le passage et ses voisins de la même section
        if not part:
            continue
        text = _merge(part)[:budget]
        budget -= len(text)
        passages.append({"document": hit["filename"], "matiere": hit["subject_name"], "page": hit["page"],
                         "section": hit["heading"], "texte": text, "document_id": doc_id,
                         "subject_id": hit["subject_id"]})
    passages = retrieval.renumber(passages, 0)
    return {"passages": passages, "sources": [source(p) for p in passages], "en_cours": pending}


def _merge(part) -> str:
    """Texte continu d'une fenêtre de passages (sans répéter les 30 mots de chevauchement)."""
    out = part[0]["text"].split()
    for p in part[1:]:
        words = p["text"].split()
        overlap = next((k for k in range(min(len(out), len(words), chunking.OVERLAP_WORDS), 0, -1)
                        if out[-k:] == words[:k]), 0)
        out += words[overlap:]
    return " ".join(out)


def source(p: dict) -> dict:
    """Source affichée sous la réponse, avec un lien vers le support (à la bonne page pour un PDF)."""
    url = url_for("subjects.document_open", doc_id=p["document_id"])
    if p["page"] and p["document"].lower().endswith(".pdf"):
        url += f"#page={p['page']}"
    return {"source": p["source"], "document": p["document"], "page": p["page"], "matiere": p["matiere"], "url": url}
