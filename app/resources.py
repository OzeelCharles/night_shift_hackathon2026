"""Recherche de ressources de révision sur internet, adaptées au niveau et à l'établissement.

Sources :
- API publique de Wikipédia (fiable, sans clé) : articles liés et plan des chapitres ;
- recherche DuckDuckGo (version HTML) : cours et exercices en PDF, pages de l'établissement ;
- YouTube (page de résultats) : vidéos de cours, triées selon le niveau de l'étudiant ;
- liens vers des plateformes éducatives choisies selon le niveau (toujours disponibles).
Toutes les fonctions publiques tolèrent l'absence de réseau.
"""
from __future__ import annotations

import html
import json
import re
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

# Mots ajoutés aux requêtes selon le niveau
LEVEL_QUERY = {
    "college": "collège", "lycee": "lycée", "prepa": "prépa", "bts_but": "BTS BUT",
    "licence": "licence université", "master": "master", "ecole": "école ingénieur",
    "doctorat": "master recherche", "autre": "",
}
# Indices de niveau repérés dans les titres (pour trier les vidéos et les PDF)
LEVEL_TERMS = {
    "college": ["collège", "college", "6e", "5e", "4e", "3e", "brevet", "sixième", "cinquième", "quatrième", "troisième"],
    "lycee": ["lycée", "lycee", "seconde", "2de", "première", "1ère", "1ere", "1re", "terminale", "tale", "tle",
              "bac", "spé maths", "spécialité"],
    "prepa": ["prépa", "prepa", "mpsi", "pcsi", "mp2i", "ecg", "cpge"],
    "bts_but": ["bts", "iut", "dut"],
    "licence": ["licence", "l1", "l2", "l3", "université", "fac", "bachelor", "1ère année", "supérieur"],
    "master": ["master", "m1", "m2"],
    "ecole": ["ingénieur", "école", "grande école", "master"],
    "doctorat": ["doctorat", "thèse", "recherche", "master"],
}
LEVEL_RANK = {"college": 0, "lycee": 1, "prepa": 2, "bts_but": 2, "licence": 3, "master": 4, "ecole": 4, "doctorat": 5}


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def level_score(title: str, level: str) -> int:
    """+2 par indice du bon niveau, -2 par indice d'un niveau éloigné (≥ 2 crans)."""
    if level not in LEVEL_RANK:
        return 0
    t = f" {title.lower()} "
    score = 0
    for lvl, terms in LEVEL_TERMS.items():
        hit = any(re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", t) for term in terms)
        if not hit:
            continue
        if lvl == level:
            score += 2
        elif abs(LEVEL_RANK[lvl] - LEVEL_RANK[level]) >= 2:
            score -= 2
    return score


def rank_for_level(items: list[dict], level: str) -> list[dict]:
    # tri stable : à pertinence égale on garde l'ordre du moteur de recherche
    return sorted(items, key=lambda r: -level_score(r["title"] + " " + r.get("meta", ""), level))


# ------------------------------------------------------------------ Wikipédia
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
            "source": "Wikipédia", "kind": "web",
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


# ------------------------------------------------------------------ Web
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
            out.append({
                "id": v["videoId"], "title": runs("title"), "channel": runs("ownerText"),
                "duration": (v.get("lengthText") or {}).get("simpleText", ""),
            })
        for x in node.values():
            _walk_videos(x, out)
    elif isinstance(node, list):
        for x in node:
            _walk_videos(x, out)


def _video_item(vid: str, title: str, channel: str = "", duration: str = "") -> dict:
    meta = ", ".join(x for x in (channel, duration) if x)
    return {"title": title, "url": f"https://www.youtube.com/watch?v={vid}", "source": "YouTube",
            "kind": "video", "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "meta": meta,
            "snippet": ""}


def youtube_search(query: str, limit: int = 8) -> list[dict]:
    r = requests.get("https://www.youtube.com/results",
                     params={"search_query": query, "hl": "fr", "gl": "FR"},
                     headers=BROWSER, cookies={"CONSENT": "YES+cb", "SOCS": "CAI"}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
    if not m:
        return []
    found: list = []
    _walk_videos(json.loads(m.group(1)), found)
    # on écarte les vidéos sans durée (directs) et les formats très courts (shorts)
    found = [v for v in found if v["duration"] and v["duration"].count(":") >= 1
             and not re.fullmatch(r"0:\d\d", v["duration"])]
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


# ------------------------------------------------------------------ Plateformes
def platform_links(query: str, level: str = "autre") -> list[dict]:
    q = quote_plus(query)
    rank = LEVEL_RANK.get(level, 3)
    links = []
    if rank <= 1:
        links += [
            {"title": f"Lumni : {query}", "url": f"https://www.lumni.fr/recherche?query={q}", "source": "Lumni",
             "snippet": "Vidéos et fiches du service public, du collège au lycée."},
            {"title": f"Khan Academy : {query}", "url": f"https://fr.khanacademy.org/search?page_search_query={q}",
             "source": "Khan Academy", "snippet": "Cours et exercices interactifs gratuits."},
        ]
    else:
        links += [
            {"title": f"MOOC FUN : {query}", "url": f"https://www.fun-mooc.fr/fr/cours/?query={q}",
             "source": "FUN MOOC", "snippet": "MOOC des universités et grandes écoles françaises."},
            {"title": f"OpenClassrooms : {query}", "url": f"https://openclassrooms.com/fr/search?query={q}",
             "source": "OpenClassrooms", "snippet": "Cours en ligne structurés."},
        ]
    if rank >= 4:
        links += [
            {"title": f"MIT OpenCourseWare : {query}", "url": f"https://ocw.mit.edu/search/?q={q}",
             "source": "MIT OCW", "snippet": "Cours universitaires complets (en anglais)."},
            {"title": f"Google Scholar : {query}", "url": f"https://scholar.google.fr/scholar?q={q}",
             "source": "Google Scholar", "snippet": "Articles et polycopiés de recherche."},
        ]
    links.append({"title": f"Wikiversité : {query}", "url": f"https://fr.wikiversity.org/w/index.php?search={q}",
                  "source": "Wikiversité", "snippet": "Leçons et exercices libres."})
    for l in links:
        l["kind"] = "platform"
    return links


# ------------------------------------------------------------------ Assemblage
GENERIC_SCHOOL_WORDS = {"lycee", "lycée", "college", "collège", "universite", "université", "ecole", "école",
                        "institut", "de", "du", "des", "la", "le", "les", "d", "l", "et", "en", "nationale",
                        "superieure", "supérieure", "polytechnique", "sciences", "faculte", "faculté"}


def school_keyword(school: str) -> str:
    """Mot le plus distinctif du nom d'établissement : « Lycée Chateaubriand » -> « chateaubriand »,
    « Université de Rennes » -> « rennes », « ENSAI » -> « ensai »."""
    words = [w for w in re.findall(r"[\wÀ-ÿ]+", (school or "").lower()) if w not in GENERIC_SCHOOL_WORDS]
    return max(words, key=len) if words else ""

def find_resources(subject: str, topics: list[str], extra_keywords: list[str] | None = None,
                   level: str = "autre", school: str = "") -> tuple[list[dict], list[str]]:
    """Retourne (ressources, erreurs). Chaque ressource a un `kind` : web, video ou platform."""
    results: list[dict] = []
    errors: list[str] = []
    lvl = LEVEL_QUERY.get(level, "")

    # 1. Wikipédia : la matière et ses premiers chapitres
    queries = [subject] + [f"{subject} {t}" for t in topics[:3]]
    if extra_keywords:
        queries.append(f"{subject} {' '.join(extra_keywords[:3])}")
    for q in queries:
        try:
            results += wikipedia_search(q, limit=3 if q == subject else 2)
        except requests.RequestException as e:
            errors.append(f"Wikipédia indisponible ({e.__class__.__name__}).")
            break

    # 2. Web : établissement d'abord (polycopiés de ton école), puis cours/exercices du bon niveau
    web_queries = []
    if school:
        web_queries.append((f"{subject} {school} cours pdf", f"Lié à {school}"))
    web_queries += [(f"{subject} cours {lvl} pdf".replace("  ", " "), None),
                    (f"{subject} exercices corrigés {lvl}".replace("  ", " "), None)]
    web = []
    for q, label in web_queries:
        try:
            found = duckduckgo_search(q, limit=4)
        except requests.RequestException as e:
            errors.append(f"Recherche web indisponible ({e.__class__.__name__}).")
            break
        if label:
            school_key = school_keyword(school)
            for r in found:
                if school_key and (school_key in r["url"].lower() or school_key in r["title"].lower()):
                    r["meta"] = label
        web += found
    results += rank_for_level(web, level)

    # 3. Vidéos : YouTube, sinon plan B via la recherche web
    videos: list[dict] = []
    video_queries = [f"{subject} cours {lvl}".strip()] + [f"{subject} {t} {lvl}".strip() for t in topics[:2]]
    try:
        for q in video_queries:
            videos += youtube_search(q, limit=6)
    except (requests.RequestException, ValueError):
        try:
            videos = videos_via_duckduckgo(f"{subject} cours {lvl}")
        except requests.RequestException as e:
            errors.append(f"Vidéos indisponibles ({e.__class__.__name__}).")
    # on écarte les vidéos visiblement d'un autre niveau (« Seconde » pour un étudiant de master),
    # sauf s'il n'en resterait presque plus
    seen_ids, unique_videos = set(), []
    for v in videos:
        if v["url"] not in seen_ids:
            seen_ids.add(v["url"])
            unique_videos.append(v)
    suited = [v for v in unique_videos if level_score(v["title"], level) >= 0]
    results += rank_for_level(suited if len(suited) >= 4 else unique_videos, level)[:9]

    # 4. Plateformes adaptées au niveau
    results += platform_links(subject, level)
    seen, unique = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            r.setdefault("meta", "")
            r.setdefault("thumbnail", "")
            r.setdefault("snippet", "")
            unique.append(r)
    return unique, errors
