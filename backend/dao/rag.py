"""Index des supports de cours pour l'assistant (RAG). Chaque requête filtre sur l'utilisateur."""
import sqlite3

from .db import execute, execute_many, query


def documents_to_index(user_id, model: str, subject_id=None) -> list:
    """Supports de l'utilisateur pas encore indexés (ou indexés avec un autre modèle)."""
    sql = ("SELECT d.id, d.subject_id, d.filename, d.stored_path FROM documents d "
           "JOIN subjects s ON s.id = d.subject_id LEFT JOIN doc_index i ON i.document_id = d.id "
           "WHERE s.user_id = ? AND (i.document_id IS NULL OR i.model != ?)")
    args = [user_id, model]
    if subject_id is not None:
        sql += " AND d.subject_id = ?"
        args.append(subject_id)
    return query(sql + " ORDER BY d.id", *args)


def users_to_index(model: str) -> list[int]:
    """Utilisateurs dont au moins un support n'est pas encore indexé avec ce modèle (démarrage de l'application)."""
    return [r["user_id"] for r in query(
        "SELECT DISTINCT s.user_id FROM documents d JOIN subjects s ON s.id = d.subject_id "
        "LEFT JOIN doc_index i ON i.document_id = d.id WHERE i.document_id IS NULL OR i.model != ?", model)]


def replace_chunks(document_id, subject_id, user_id, model: str, rows: list[tuple]) -> None:
    """rows : (position, page, heading, text, vector) ; remplace l'index d'un support."""
    execute("DELETE FROM doc_chunks WHERE document_id = ?", document_id)
    execute_many("INSERT INTO doc_chunks (document_id, subject_id, user_id, position, page, heading, text, vector) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 [(document_id, subject_id, user_id, *r) for r in rows])
    execute("INSERT OR REPLACE INTO doc_index (document_id, model, chunks) VALUES (?, ?, ?)",
            document_id, model, len(rows))


def _scope(user_id, subject_id, alias: str = "c") -> tuple[str, list]:
    if subject_id is None:
        return f"{alias}.user_id = ?", [user_id]
    return f"{alias}.user_id = ? AND {alias}.subject_id = ?", [user_id, subject_id]


def vectors(user_id, subject_id=None) -> list:
    where, args = _scope(user_id, subject_id)
    return query(f"SELECT c.id, c.vector FROM doc_chunks c WHERE {where}", *args)


def keyword_ranking(user_id, match: str, subject_id=None, limit: int = 20) -> list[int]:
    """Passages classés par BM25 (meilleurs d'abord)."""
    if not match:
        return []
    where, args = _scope(user_id, subject_id)
    try:
        rows = query(f"SELECT c.id FROM doc_chunks_fts f JOIN doc_chunks c ON c.id = f.rowid "
                     f"WHERE doc_chunks_fts MATCH ? AND {where} ORDER BY bm25(doc_chunks_fts) LIMIT ?",
                     match, *args, limit)
    except sqlite3.OperationalError:  # requête FTS mal formée : la recherche par le sens suffit
        return []
    return [r["id"] for r in rows]


def chunks_by_ids(user_id, ids: list[int]) -> list:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return query(f"SELECT c.id, c.document_id, c.subject_id, c.position, c.page FROM doc_chunks c "
                 f"WHERE c.user_id = ? AND c.id IN ({marks})", user_id, *ids)


def window(user_id, document_id, first: int, last: int) -> list:
    return query("SELECT c.id, c.position, c.page, c.heading, c.text, c.subject_id, d.filename, s.name AS subject_name "
                 "FROM doc_chunks c JOIN documents d ON d.id = c.document_id JOIN subjects s ON s.id = c.subject_id "
                 "WHERE c.user_id = ? AND c.document_id = ? AND c.position BETWEEN ? AND ? ORDER BY c.position",
                 user_id, document_id, first, last)


def stats(user_id) -> dict:
    row = query("SELECT COUNT(DISTINCT document_id) AS docs, COUNT(*) AS chunks FROM doc_chunks WHERE user_id = ?",
                user_id)[0]
    return {"docs": row["docs"], "chunks": row["chunks"]}
