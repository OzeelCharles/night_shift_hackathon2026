"""Recherche de ressources de révision sur internet.

Sources :
- API publique de Wikipédia (fiable, sans clé) : articles liés et plan des chapitres ;
- recherche DuckDuckGo (version HTML, au mieux : peut échouer si le service bloque) ;
- liens de recherche vers des plateformes éducatives (toujours disponibles).
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, quote_plus, urlparse

import requests

UA = {"User-Agent": "PlanificateurRevisions/1.0 (application educative)"}
TIMEOUT = 8
SKIP_SECTIONS = {
    "notes et références", "références", "voir aussi", "bibliographie", "liens externes",
    "articles connexes", "annexes", "notes", "sources", "see also", "references", "external links",
    "further reading", "notes and references", "galerie", "filmographie",
}


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def wikipedia_search(query: str, lang: str = "fr", limit: int = 4) -> list[dict]:
    r = requests.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "format": "json",
                "srlimit": limit, "utf8": 1},
        headers=UA, timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for hit in r.json().get("query", {}).get("search", []):
        title = hit["title"]
        out.append({
            "title": f"Wikipédia : {title}",
            "url": f"https://{lang}.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'), safe='_')}",
            "snippet": _strip_tags(hit.get("snippet", ""))[:220],
            "source": "Wikipédia",
        })
    return out


def wikipedia_outline(subject: str, lang: str = "fr") -> list[str]:
    """Propose un plan de chapitres à partir de l'article Wikipédia le plus pertinent."""
    hits = wikipedia_search(subject, lang, limit=1)
    if not hits:
        return []
    title = hits[0]["title"].split(" : ", 1)[1]
    r = requests.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "parse", "page": title, "prop": "sections", "format": "json", "redirects": 1},
        headers=UA, timeout=TIMEOUT,
    )
    r.raise_for_status()
    topics = []
    for sec in r.json().get("parse", {}).get("sections", []):
        name = _strip_tags(sec.get("line", ""))
        if str(sec.get("toclevel")) == "1" and name.lower() not in SKIP_SECTIONS:
            topics.append(name)
    return topics[:15]


def duckduckgo_search(query: str, limit: int = 5) -> list[dict]:
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "fr-fr"},
                      headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    pattern = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    for href, title, snippet in pattern.findall(r.text):
        href = html.unescape(href)
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        out.append({"title": _strip_tags(title), "url": href, "snippet": _strip_tags(snippet)[:220],
                    "source": urlparse(href).netloc.replace("www.", "")})
        if len(out) >= limit:
            break
    return out


def platform_links(query: str) -> list[dict]:
    q = quote_plus(query)
    return [
        {"title": f"Vidéos de cours : {query}", "url": f"https://www.youtube.com/results?search_query={q}+cours",
         "source": "YouTube", "snippet": "Explications en vidéo, souvent par des enseignants."},
        {"title": f"Khan Academy : {query}", "url": f"https://fr.khanacademy.org/search?page_search_query={q}",
         "source": "Khan Academy", "snippet": "Cours et exercices interactifs gratuits."},
        {"title": f"OpenClassrooms : {query}", "url": f"https://openclassrooms.com/fr/search?query={q}",
         "source": "OpenClassrooms", "snippet": "Cours en ligne structurés."},
        {"title": f"MOOC FUN : {query}", "url": f"https://www.fun-mooc.fr/fr/cours/?query={q}",
         "source": "FUN MOOC", "snippet": "MOOC des universités françaises."},
        {"title": f"Wikiversité : {query}", "url": f"https://fr.wikiversity.org/w/index.php?search={q}",
         "source": "Wikiversité", "snippet": "Leçons et exercices libres."},
        {"title": f"Exercices corrigés : {query}",
         "url": f"https://duckduckgo.com/?q={q}+exercices+corrig%C3%A9s+pdf",
         "source": "Recherche web", "snippet": "Annales et exercices corrigés au format PDF."},
    ]


def find_resources(subject: str, topics: list[str], extra_keywords: list[str] | None = None,
                   max_topics: int = 3) -> tuple[list[dict], list[str]]:
    """Retourne (ressources, erreurs). Ne lève jamais d'exception réseau."""
    results: list[dict] = []
    errors: list[str] = []
    queries = [subject] + [f"{subject} {t}" for t in topics[:max_topics]]
    if extra_keywords:
        queries.append(f"{subject} {' '.join(extra_keywords[:3])}")
    for q in queries:
        try:
            results += wikipedia_search(q, limit=3 if q == subject else 2)
        except requests.RequestException as e:
            errors.append(f"Wikipédia indisponible ({e.__class__.__name__})")
            break
    for q in (f"{subject} cours pdf", f"{subject} exercices corrigés"):
        try:
            results += duckduckgo_search(q, limit=4)
        except requests.RequestException as e:
            errors.append(f"Recherche web indisponible ({e.__class__.__name__})")
            break
    results += platform_links(subject)
    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique, errors
