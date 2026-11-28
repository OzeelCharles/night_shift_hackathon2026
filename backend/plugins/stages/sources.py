"""Où chercher : liens de recherche préremplis vers les sites d'offres, et lecture d'une offre à partir de son lien.

Les sites d'offres (Indeed, LinkedIn, Glassdoor…) interdisent l'aspiration automatique de leurs résultats :
on ouvre donc leur propre recherche, déjà remplie avec le poste et le lieu. Pour une offre précise, son lien
suffit souvent à préremplir la fiche : la plupart des pages d'offres décrivent le poste en données
structurées schema.org (JobPosting), que Google lit aussi.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import quote, quote_plus, urlparse

CONTRACT_WORDS = re.compile(r"\b(stage|stagiaire|alternance|alternant|apprenti|apprentissage|internship|intern)\b", re.I)


def query_for(title: str) -> str:
    """« stage » est ajouté sauf si l'intitulé précise déjà le contrat (alternance, apprentissage…)."""
    title = " ".join(title.split())
    return title if CONTRACT_WORDS.search(title) else f"stage {title}"


def search_links(title: str, location: str = "") -> list[dict]:
    q, loc = query_for(title), " ".join((location or "").split())
    qp, lp = quote_plus(q), quote_plus(loc)
    google = quote_plus(f"{q} {loc}".strip())
    return [
        {"site": "Indeed", "url": f"https://fr.indeed.com/jobs?q={qp}&l={lp}"},
        {"site": "LinkedIn", "url": f"https://www.linkedin.com/jobs/search/?keywords={qp}&location={lp}&f_JT=I"},
        {"site": "Glassdoor", "url": f"https://www.glassdoor.fr/Emploi/emplois.htm?sc.keyword={qp}&locKeyword={lp}"},
        {"site": "Welcome to the Jungle",
         "url": f"https://www.welcometothejungle.com/fr/jobs?query={quote(title)}"
                f"&refinementList%5Bcontract_type%5D%5B%5D=internship"},
        {"site": "HelloWork", "url": f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={qp}&l={lp}"},
        {"site": "JobTeaser", "url": f"https://www.jobteaser.com/fr/job-offers?q={qp}&locality={lp}"},
        {"site": "Google (toutes les offres)", "url": f"https://www.google.com/search?q={google}&ibp=htl;jobs"},
    ]


def site_name(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("fr.")
    for key, name in (("indeed", "Indeed"), ("linkedin", "LinkedIn"), ("glassdoor", "Glassdoor"),
                      ("welcometothejungle", "Welcome to the Jungle"), ("hellowork", "HelloWork"),
                      ("jobteaser", "JobTeaser"), ("francetravail", "France Travail")):
        if key in host:
            return name
    return host


# ---------------------------------------------------------------- encodage des pages d'offres
# Un texte UTF-8 lu comme Latin-1 ou Windows-1252 devient « Ã© » au lieu de « é », « â€™ » au lieu de « ’ »
# (« mojibake »). Une séquence abîmée = un caractère de tête (Â à ô) suivi de 1 à 3 octets de continuation,
# lus comme caractères Latin-1 (U+0080 à U+00BF) ou leurs équivalents Windows-1252 (€, ™, ’, –…).
_CP1252_CONT = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
_MOJIBAKE = re.compile(f"[Â-ô][\u0080-¿{re.escape(_CP1252_CONT)}]{{1,3}}")


def _as_bytes(run: str) -> bytes | None:
    out = bytearray()
    for ch in run:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) > 0xFF:
                return None
            out.append(ord(ch))   # octets 0x80-0x9F sans équivalent Windows-1252
    return bytes(out)


def _fix_run(m: re.Match) -> str:
    raw = _as_bytes(m.group(0))
    try:
        return raw.decode("utf-8") if raw else m.group(0)
    except UnicodeDecodeError:
        return m.group(0)


def fix_mojibake(text: str) -> str:
    """Répare « Stage dâ€™Ã©tÃ© » en « Stage d’été ». Un texte correct reste identique : seules les séquences
    qui redonnent de l'UTF-8 valide sont remplacées (deux passes pour un texte encodé deux fois)."""
    text = str(text or "")
    for _ in range(2):
        fixed = _MOJIBAKE.sub(_fix_run, text)
        if fixed == text:
            break
        text = fixed
    return text


def decode_page(content: bytes, content_type: str = "", apparent: str | None = None) -> str:
    """Texte d'une page web : charset annoncé par le serveur, sinon celui de la balise <meta>, sinon UTF-8.
    (Sans charset annoncé, `requests` suppose Latin-1, d'où les intitulés illisibles.)"""
    declared = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I)
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", content[:4096], re.I)
    candidates = [declared.group(1) if declared else None, meta.group(1).decode("ascii", "ignore") if meta else None,
                  "utf-8"]
    for enc in filter(None, candidates):
        try:
            return content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode(apparent or "utf-8", errors="replace")


def _text(v) -> str:
    v = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", str(v or ""), flags=re.I)
    v = fix_mojibake(html.unescape(re.sub(r"<[^>]+>", " ", v)))
    return "\n".join(" ".join(line.split()) for line in v.splitlines() if line.strip())


def _postings(node):
    if isinstance(node, list):
        for x in node:
            yield from _postings(x)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        for x in node.get("@graph", []) if isinstance(node.get("@graph"), list) else []:
            yield from _postings(x)


def parse_offer(page: str) -> dict | None:
    """{title, company, location, description} lus dans les données JobPosting de la page, sinon dans ses balises."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, flags=re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for p in _postings(data):
            org = p.get("hiringOrganization") or {}
            place = p.get("jobLocation") or {}
            place = place[0] if isinstance(place, list) and place else place
            addr = place.get("address", {}) if isinstance(place, dict) else {}
            addr = addr if isinstance(addr, dict) else {}
            return {"title": _text(p.get("title"))[:200],
                    "company": _text(org.get("name") if isinstance(org, dict) else org)[:200],
                    "location": _text(addr.get("addressLocality") or addr.get("addressRegion"))[:120],
                    "description": _text(p.get("description"))[:6000]}
    title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', page, re.I) or \
        re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    desc = re.search(r'<meta[^>]+(?:name|property)="(?:og:)?description"[^>]+content="([^"]*)"', page, re.I)
    if not title:
        return None
    return {"title": _text(title.group(1))[:200], "company": "", "location": "",
            "description": _text(desc.group(1))[:6000] if desc else ""}
