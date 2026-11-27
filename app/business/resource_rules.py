"""Règles de choix des ressources : adaptation au niveau, priorité aux PDF et à l'établissement."""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

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
GENERIC_SCHOOL_WORDS = {"lycee", "lycée", "college", "collège", "universite", "université", "ecole", "école",
                        "institut", "de", "du", "des", "la", "le", "les", "d", "l", "et", "en", "nationale",
                        "superieure", "supérieure", "polytechnique", "sciences", "faculte", "faculté"}


def level_score(title: str, level: str) -> int:
    """+2 par indice du bon niveau, -2 par indice d'un niveau éloigné (≥ 2 crans)."""
    if level not in LEVEL_RANK:
        return 0
    t = f" {title.lower()} "
    score = 0
    for lvl, terms in LEVEL_TERMS.items():
        if not any(re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", t) for term in terms):
            continue
        if lvl == level:
            score += 2
        elif abs(LEVEL_RANK[lvl] - LEVEL_RANK[level]) >= 2:
            score -= 2
    return score


def rank_for_level(items: list[dict], level: str) -> list[dict]:
    # tri stable : à pertinence égale on garde l'ordre du moteur de recherche
    return sorted(items, key=lambda r: -level_score(r["title"] + " " + r.get("meta", ""), level))


def is_pdf(r: dict) -> bool:
    path = urlparse(r.get("url", "")).path.lower()
    return path.endswith(".pdf") or "/pdf/" in path or r.get("title", "").upper().startswith("PDF ")


def school_keyword(school: str) -> str:
    """Mot le plus distinctif du nom d'établissement : « Lycée Chateaubriand » -> « chateaubriand »,
    « Université de Rennes » -> « rennes », « ENSAI » -> « ensai »."""
    words = [w for w in re.findall(r"[\wÀ-ÿ]+", (school or "").lower()) if w not in GENERIC_SCHOOL_WORDS]
    return max(words, key=len) if words else ""


def label_documents(results: list[dict], level: str, school: str, from_school: bool, kind: str) -> list[dict]:
    """Marque les PDF et les documents de l'établissement, nettoie les titres."""
    key = school_keyword(school)
    for r in results:
        r["kind"] = kind
        r["pdf"] = is_pdf(r)
        if r["title"].upper().startswith("PDF "):
            r["title"] = r["title"][4:].strip()  # préfixe ajouté par le moteur de recherche
        if from_school and key and (key in r["url"].lower() or key in r["title"].lower()):
            r["meta"] = f"Lié à {school}"
    return results


def documents_first(results: list[dict], level: str) -> list[dict]:
    """Ordre : établissement, puis PDF, puis pages web ; à rang égal, le plus adapté au niveau."""
    return sorted(results, key=lambda r: (not r.get("meta"), not r["pdf"], -level_score(r["title"], level)))


def suited_videos(videos: list[dict], level: str, limit: int = 9) -> list[dict]:
    """Sans doublon ; on écarte les vidéos visiblement d'un autre niveau (« Seconde » pour un
    étudiant de master), sauf s'il n'en resterait presque plus."""
    seen, unique = set(), []
    for v in videos:
        if v["url"] not in seen:
            seen.add(v["url"])
            unique.append(v)
    suited = [v for v in unique if level_score(v["title"], level) >= 0]
    return rank_for_level(suited if len(suited) >= 4 else unique, level)[:limit]


def platform_links(query: str, level: str = "autre") -> list[dict]:
    q = quote_plus(query)
    rank = LEVEL_RANK.get(level, 3)
    links = []
    if rank <= 1:
        links += [
            ("Lumni", f"https://www.lumni.fr/recherche?query={q}", "Vidéos et fiches du service public, du collège au lycée."),
            ("Khan Academy", f"https://fr.khanacademy.org/search?page_search_query={q}", "Cours et exercices interactifs gratuits."),
        ]
    else:
        links += [
            ("FUN MOOC", f"https://www.fun-mooc.fr/fr/cours/?query={q}", "MOOC des universités et grandes écoles françaises."),
            ("OpenClassrooms", f"https://openclassrooms.com/fr/search?query={q}", "Cours en ligne structurés."),
        ]
    if rank >= 4:
        links += [
            ("MIT OCW", f"https://ocw.mit.edu/search/?q={q}", "Cours universitaires complets (en anglais)."),
            ("Google Scholar", f"https://scholar.google.fr/scholar?q={q}", "Articles et polycopiés de recherche."),
        ]
    links.append(("Wikiversité", f"https://fr.wikiversity.org/w/index.php?search={q}", "Leçons et exercices libres."))
    titles = {"FUN MOOC": "MOOC FUN", "MIT OCW": "MIT OpenCourseWare"}
    return [{"title": f"{titles.get(source, source)} : {query}", "url": url, "source": source, "snippet": snippet,
             "kind": "platform"} for source, url, snippet in links]
