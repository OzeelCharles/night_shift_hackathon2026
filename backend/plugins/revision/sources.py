"""Ressources externes de l'atelier : PDF d'exercices corrigés et notebooks Jupyter trouvés en ligne.

Réutilise la recherche web du cœur (backend.dao.web). Les erreurs réseau remontent en
requests.RequestException ; l'appelant décide quoi afficher.
"""
from __future__ import annotations

import io
import ipaddress
import socket
from urllib.parse import urlparse

import requests

from backend.dao import web

MAX_PDF_BYTES = 8 * 1024 * 1024
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


def _public_host(url: str) -> bool:
    """Refuse les adresses locales ou privées (on ne télécharge que depuis internet)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def pdf_text(url: str) -> str:
    """Texte d'un PDF en ligne (taille et nombre de pages limités). Chaîne vide si ce n'est pas un PDF lisible."""
    if not _public_host(url):
        return ""
    with requests.get(url, headers=web.BROWSER, timeout=web.TIMEOUT, stream=True, allow_redirects=False) as r:
        r.raise_for_status()
        data = b""
        for chunk in r.iter_content(64 * 1024):
            data += chunk
            if len(data) > MAX_PDF_BYTES:
                return ""
    if not data.startswith(b"%PDF"):
        return ""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES])
    except Exception:
        return ""
