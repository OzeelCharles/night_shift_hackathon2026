"""Extraction du texte et des chapitres depuis les supports de cours."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ALLOWED_EXTENSIONS = {"pdf", "pptx", "docx", "odt", "odp", "txt", "md"}

STOPWORDS = set("""
alors aucun aussi autre avant avec avoir bon car cela ces cette ceci ceux chaque comme comment dans des
donc dont elle elles encore entre est et etre être fait faire fois font hors ici ils leur leurs mais
même mes moins mon nous notre nos par parce pas peu peut plus pour pourquoi quand que quel quelle
quelles quels qui sans ses selon sera seront son sont sous sur tandis tout toute toutes tous très
une vers voici voilà vous votre vos ainsi après assez avoir cours exemple exemples partie chapitre
section page slide figure table tableau définition definition donc soit elles cette celui celle
about after also because been before being between both could does doing each from have having here
into itself more most other over same should some such than that their them then there these they
this those through under until very what when where which while with would your
""".split())

HEADING_RE = re.compile(
    r"^(chapitre|chapter|partie|part|section|th[eè]me|le[cç]on|module|unit[eé]?)\b"
    r"|^(\d+(\.\d+){0,2}|[IVX]{1,5}|[A-H])\s?[.)\-–]\s+\S",
    re.IGNORECASE,
)
JUNK_TITLES = {
    "sommaire", "plan", "table des matières", "merci", "questions", "questions ?", "références",
    "bibliographie", "conclusion", "annexe", "annexes", "contents", "outline", "thank you",
    "références bibliographiques", "remerciements",
}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def clean_topics(raw: list[str], limit: int = 30) -> list[str]:
    seen, out = set(), []
    for t in raw:
        t = re.sub(r"\s+", " ", t or "").strip()
        t = re.sub(r"[.\s·…]{3,}\d*$", "", t)  # points de conduite + n° de page
        t = t.strip(" -–:•")
        if not (3 <= len(t) <= 90) or t.lower() in JUNK_TITLES:
            continue
        if not re.search(r"[A-Za-zÀ-ÿ]", t):
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def guess_headings(text: str) -> list[str]:
    heads = []
    for line in text.splitlines():
        line = line.strip()
        if not (4 <= len(line) <= 80):
            continue
        words = line.split()
        if HEADING_RE.match(line) and len(words) <= 12:
            heads.append(line)
        elif line.isupper() and 1 <= len(words) <= 8 and sum(c.isalpha() for c in line) >= 4:
            heads.append(line.title())
    return heads


def keywords(text: str, n: int = 10) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]{4,}", text.lower())
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in counts.most_common(n)]


def _pdf(path: Path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    heads = []

    def walk(items, depth=0):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
            elif depth <= 1:
                try:
                    heads.append(it.title)
                except Exception:
                    pass

    try:
        walk(reader.outline)
    except Exception:
        pass
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:60])
    return text, heads


def _pptx(path: Path):
    from pptx import Presentation

    prs = Presentation(str(path))
    heads, parts = [], []
    for slide in prs.slides:
        title = slide.shapes.title
        if title is not None and title.has_text_frame and title.text_frame.text.strip():
            heads.append(title.text_frame.text.strip().split("\n")[0])
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts), heads


def _docx(path: Path):
    import docx

    d = docx.Document(str(path))
    heads, parts = [], []
    for p in d.paragraphs:
        parts.append(p.text)
        name = (p.style.name or "").lower() if p.style is not None else ""
        m = re.match(r"(heading|titre)\s*(\d)", name)
        if m and int(m.group(2)) <= 2 and p.text.strip():
            heads.append(p.text.strip())
    return "\n".join(parts), heads


def _odf(path: Path):
    from odf import draw, teletype
    from odf import text as odftext
    from odf.namespaces import PRESENTATIONNS, TEXTNS
    from odf.opendocument import load

    doc = load(str(path))
    heads = []
    for h in doc.getElementsByType(odftext.H):
        try:
            level = int(h.getAttrNS(TEXTNS, "outline-level") or 1)
        except (TypeError, ValueError):
            level = 1
        if level <= 2:
            heads.append(teletype.extractText(h))
    for frame in doc.getElementsByType(draw.Frame):
        if frame.getAttrNS(PRESENTATIONNS, "class") == "title":
            heads.append(teletype.extractText(frame))
    parts = [teletype.extractText(p) for p in doc.getElementsByType(odftext.P)]
    return "\n".join(parts), heads


def _txt(path: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    heads = [l.lstrip("#").strip() for l in text.splitlines() if l.startswith("#")]
    return text, heads


READERS = {"pdf": _pdf, "pptx": _pptx, "docx": _docx, "odt": _odf, "odp": _odf, "txt": _txt, "md": _txt}


def extract(path: str | Path) -> tuple[str, list[str]]:
    """Retourne (texte, chapitres détectés). Ne lève pas d'exception sur un fichier illisible."""
    path = Path(path)
    reader = READERS.get(path.suffix.lower().lstrip("."))
    if reader is None:
        return "", []
    try:
        text, heads = reader(path)
    except Exception:
        return "", []
    topics = clean_topics(heads)
    if len(topics) < 3:
        topics = clean_topics(topics + guess_headings(text))
    return text, topics
