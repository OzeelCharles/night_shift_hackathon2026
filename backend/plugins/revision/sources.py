"""Ressources externes de l'atelier : texte Wikipédia (matière sans support), PDF d'exercices en ligne.

Réutilise la recherche web du cœur (backend.dao.web). Les erreurs réseau remontent en
requests.RequestException ; l'appelant décide quoi afficher.
"""
from __future__ import annotations

import io
import ipaddress
import re
import socket
from urllib.parse import urlparse

import requests

from backend.dao import web

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 40
QUERY_WORDS = {"facile": "exercices corrigés faciles", "moyen": "exercices corrigés",
               "difficile": "exercices corrigés difficiles approfondissement"}


def wikipedia_text(subject: str, lang: str = "fr") -> str:
    """Texte brut de l'article le plus pertinent ; les titres de section deviennent des lignes seules."""
    hits = web.wikipedia_search(subject, lang, limit=1)
    if not hits:
        return ""
    title = hits[0]["title"].split(" : ", 1)[1]
    r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                     params={"action": "query", "prop": "extracts", "explaintext": 1, "titles": title,
                             "format": "json", "redirects": 1},
                     headers=web.UA, timeout=web.TIMEOUT)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    text = next(iter(pages.values()), {}).get("extract", "") if pages else ""
    return re.sub(r"^=+\s*(.*?)\s*=+$", r"\1", text, flags=re.M)


def search_exercise_pdfs(subject: str, chapter: str, difficulty: str, level_words: str) -> list[dict]:
    query = " ".join(p for p in (subject, chapter, QUERY_WORDS[difficulty], level_words, "filetype:pdf") if p)
    return [r for r in web.duckduckgo_search(query, limit=8) if ".pdf" in r["url"].lower()]


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
