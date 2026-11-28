"""Ressources externes de l'atelier : PDF d'exercices corrigés et notebooks Jupyter trouvés en ligne.

Réutilise la recherche web du cœur (backend.dao.web). Les erreurs réseau remontent en
requests.RequestException ; l'appelant décide quoi afficher.
"""
from __future__ import annotations

import io
from urllib.parse import urlparse

from backend.dao import safe_http, web

MAX_PDF_BYTES = safe_http.MAX_BYTES
MAX_PDF_PAGES = 40
QUERY_WORDS = {"facile": "exercices corrigés faciles", "moyen": "exercices corrigés",
               "difficile": "exercices corrigés difficiles approfondissement"}


def search_exercise_pdfs(subject: str, chapter: str, difficulty: str, level_words: str) -> list[dict]:
    query = " ".join(p for p in (subject, chapter, QUERY_WORDS[difficulty], level_words, "filetype:pdf") if p)
    return [r for r in web.duckduckgo_search(query, limit=8) if ".pdf" in r["url"].lower()]


NOTEBOOK_SITES = ("kaggle.com/code/", "colab.research.google.com/")


def search_notebooks(subject: str, chapter: str) -> list[dict]:
    """Notebooks Jupyter existants (.ipynb sur GitHub et ailleurs, Kaggle, Colab) : {title, url, source}."""
    query = " ".join(p for p in (subject, chapter, "jupyter notebook exercises ipynb") if p)
    return [r for r in web.duckduckgo_search(query, limit=10)
            if ".ipynb" in r["url"].lower() or any(site in r["url"] for site in NOTEBOOK_SITES)]


def colab_url(url: str) -> str:
    """Lien « Ouvrir dans Colab » pour un notebook publié sur GitHub (vide sinon)."""
    parsed = urlparse(url)
    if parsed.netloc in ("github.com", "www.github.com") and "/blob/" in parsed.path and parsed.path.endswith(".ipynb"):
        return "https://colab.research.google.com/github" + parsed.path
    return ""


def pdf_text(url: str) -> str:
    """Texte d'un PDF en ligne (taille et nombre de pages limités). Chaîne vide si ce n'est pas un PDF lisible.
    L'URL vient de recherches web ou des ressources d'une matière : téléchargement via safe_http uniquement
    (adresse publique vérifiée une seule fois puis connexion à cette adresse, SEC-07)."""
    try:
        r = safe_http.get(url, headers=web.BROWSER, max_bytes=MAX_PDF_BYTES)
    except (safe_http.Blocked, safe_http.TooLarge):
        return ""
    if r.status_code != 200:
        return ""
    data = r.content
    if not data.startswith(b"%PDF"):
        return ""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES])
    except Exception:
        return ""
