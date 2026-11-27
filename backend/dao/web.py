"""Sources de données sur internet : Wikipédia (API publique), DuckDuckGo (version HTML), YouTube.

Chaque fonction renvoie des dictionnaires « ressource » : title, url, snippet, source, kind
(et thumbnail / meta pour les vidéos). Les erreurs réseau remontent en requests.RequestException.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from urllib.parse import parse_qs, quote_plus, urlparse

import requests

UA = {"User-Agent": "PlanificateurRevisions/1.0 (application educative)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Accept-Language": "fr-FR,fr;q=0.9"}
TIMEOUT = 8
SKIP_SECTIONS = {
    "notes et références", "références", "voir aussi", "bibliographie", "liens externes",
    "articles connexes", "annexes", "notes", "sources", "see also", "references", "external links",
    "further reading", "notes and references", "galerie", "filmographie",
}


def secure_session() -> requests.Session:
    """Session qui vérifie les certificats avec le magasin du système plutôt que certifi : un antivirus
    qui inspecte le TLS (Norton, Kaspersky…) n'ajoute sa racine qu'au magasin du système."""
    import ssl

    from requests.adapters import HTTPAdapter

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = ssl.create_default_context()
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", _Adapter())
    return session


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


# ------------------------------------------------------------------ Wikipédia
def wikipedia_search(query: str, lang: str = "fr", limit: int = 4) -> list[dict]:
    r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                     params={"action": "query", "list": "search", "srsearch": query, "format": "json",
                             "srlimit": limit, "utf8": 1},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"title": f"Wikipédia : {hit['title']}",
             "url": f"https://{lang}.wikipedia.org/wiki/{quote_plus(hit['title'].replace(' ', '_'), safe='_')}",
             "snippet": _strip_tags(hit.get("snippet", ""))[:220], "source": "Wikipédia", "kind": "web"}
            for hit in r.json().get("query", {}).get("search", [])]


def wikipedia_outline(subject: str, lang: str = "fr") -> list[str]:
    """Plan de chapitres tiré de l'article Wikipédia le plus pertinent."""
    hits = wikipedia_search(subject, lang, limit=1)
    if not hits:
        return []
    title = hits[0]["title"].split(" : ", 1)[1]
    r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                     params={"action": "parse", "page": title, "prop": "sections", "format": "json", "redirects": 1},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    sections = r.json().get("parse", {}).get("sections", [])
    names = [_strip_tags(s.get("line", "")) for s in sections if str(s.get("toclevel")) == "1"]
    return [n for n in names if n.lower() not in SKIP_SECTIONS][:15]


# ------------------------------------------------------------------ Recherche web
_ddg_lock = threading.Lock()
_ddg_last = [0.0]
DDG_MIN_INTERVAL = 1.2  # secondes entre deux requêtes : enchaînées trop vite, elles reviennent vides


def _ddg_fetch(query: str) -> str:
    with _ddg_lock:
        wait = _ddg_last[0] + DDG_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "fr-fr"},
                              headers=UA, timeout=TIMEOUT)
        finally:
            _ddg_last[0] = time.monotonic()
    r.raise_for_status()
    return r.text


def duckduckgo_search(query: str, limit: int = 5) -> list[dict]:
    text = _ddg_fetch(query)
    if 'class="result__a"' not in text:  # réponse vide (limitation de débit) : un nouvel essai
        time.sleep(2)
        text = _ddg_fetch(query)
    pattern = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
    out = []
    for href, title, snippet in pattern.findall(text):
        href = html.unescape(href)
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        out.append({"title": _strip_tags(title), "url": href, "snippet": _strip_tags(snippet)[:220],
                    "source": urlparse(href).netloc.replace("www.", ""), "kind": "web"})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ Vidéos
def _walk_videos(node, out: list):
    if isinstance(node, dict):
        v = node.get("videoRenderer")
        if v and v.get("videoId"):
            runs = lambda k: ((v.get(k) or {}).get("runs") or [{}])[0].get("text", "")
            out.append({"id": v["videoId"], "title": runs("title"), "channel": runs("ownerText"),
                        "duration": (v.get("lengthText") or {}).get("simpleText", "")})
        for x in node.values():
            _walk_videos(x, out)
    elif isinstance(node, list):
        for x in node:
            _walk_videos(x, out)


def _video_item(vid: str, title: str, channel: str = "", duration: str = "") -> dict:
    return {"title": title, "url": f"https://www.youtube.com/watch?v={vid}", "source": "YouTube", "kind": "video",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "meta": ", ".join(x for x in (channel, duration) if x), "snippet": ""}


def youtube_search(query: str, limit: int = 8) -> list[dict]:
    r = requests.get("https://www.youtube.com/results", params={"search_query": query, "hl": "fr", "gl": "FR"},
                     headers=BROWSER, cookies={"CONSENT": "YES+cb", "SOCS": "CAI"}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
    if not m:
        return []
    found: list = []
    _walk_videos(json.loads(m.group(1)), found)
    # on écarte les vidéos sans durée (directs) et les formats très courts (shorts)
    found = [v for v in found if v["duration"].count(":") >= 1 and not re.fullmatch(r"0:\d\d", v["duration"])]
    return [_video_item(v["id"], v["title"], v["channel"], v["duration"]) for v in found[:limit]]


def videos_via_duckduckgo(query: str, limit: int = 6) -> list[dict]:
    """Plan B si YouTube ne répond pas : recherche web restreinte à youtube.com."""
    out = []
    for r in duckduckgo_search(f"site:youtube.com {query}", limit=limit * 2):
        m = re.search(r"[?&]v=([\w-]{11})", r["url"])
        if m:
            out.append(_video_item(m.group(1), r["title"].replace(" - YouTube", "")))
        if len(out) >= limit:
            break
    return out
