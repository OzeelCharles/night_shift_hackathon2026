"""Extraction du texte et des chapitres depuis les supports de cours.

Sécurité (SEC-06) : le type est vérifié par le contenu (signature) ; les archives Office / OpenDocument sont
inspectées par leurs en-têtes ZIP (taille décompressée, nombre d'entrées) avant toute lecture, puis lues en flux
(XML lu par morceaux, borné en octets, en texte extrait et en temps) : un fichier piégé ne bloque pas le serveur.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

ALLOWED_EXTENSIONS = {"pdf", "pptx", "docx", "odt", "odp", "txt", "md"}
OOXML = {"docx", "pptx"}
ODF = {"odt", "odp"}
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024  # taille totale décompressée déclarée
MAX_ARCHIVE_ENTRIES = 2000
MAX_XML_BYTES = 20 * 1024 * 1024  # XML lu au plus par document (au-delà, on garde ce qui a été lu)
MAX_TEXT_CHARS = 1_000_000  # texte extrait au plus par document
EXTRACT_SECONDS = 10.0  # durée maximale de lecture d'un document
_CHUNK = 64 * 1024
_PDF_PADDING = b"\xef\xbb\xbf\x00\t\n\r\x0c "

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


def check_content(path: str | Path) -> str | None:
    """Vérifie que le contenu correspond à l'extension, avant toute analyse. Renvoie la raison du refus, ou None.
    PDF : signature %PDF-. Office / OpenDocument : archive ZIP dont les en-têtes annoncent au plus 2 000 entrées
    et 100 Mo décompressés, avec la partie principale attendue."""
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    with open(path, "rb") as f:
        head = f.read(1024)
    if ext == "pdf":
        return None if head.lstrip(_PDF_PADDING).startswith(b"%PDF-") else "ce n'est pas un vrai fichier PDF"
    if ext not in OOXML | ODF:
        return None
    if not head.startswith(b"PK\x03\x04"):
        return f"ce n'est pas un vrai fichier .{ext}"
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError):
        return f"archive .{ext} illisible"
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        return f"archive de plus de {MAX_ARCHIVE_ENTRIES} éléments"
    if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
        return f"plus de {MAX_ARCHIVE_BYTES // (1024 * 1024)} Mo une fois décompressé"
    names = {i.filename for i in infos}
    if ("[Content_Types].xml" if ext in OOXML else "content.xml") not in names:
        return f"ce n'est pas un vrai fichier .{ext}"
    return None


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


# ---------------------------------------------------------------- archives Office / OpenDocument, lues en flux
class _Budget:
    """Travail permis pour un document : octets de XML lus, caractères extraits, durée."""

    def __init__(self):
        self.deadline = time.monotonic() + EXTRACT_SECONDS
        self.xml_left = MAX_XML_BYTES
        self.chars = 0

    def spent(self) -> bool:
        return self.xml_left <= 0 or self.chars >= MAX_TEXT_CHARS or time.monotonic() > self.deadline

    def add(self, text: str) -> str:
        self.chars += len(text)
        return text


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _xml_events(z: zipfile.ZipFile, name: str, budget: _Budget):
    """(événement, élément, parent) d'une partie XML de l'archive, lue par morceaux tant que le budget le permet.
    L'appelant détache les éléments traités (parent.remove) : la mémoire reste bornée."""
    parser = ET.XMLPullParser(("start", "end"))
    stack: list = []
    with z.open(name) as f:
        while not budget.spent():
            chunk = f.read(min(_CHUNK, budget.xml_left))
            if not chunk:
                break
            budget.xml_left -= len(chunk)
            parser.feed(chunk)
            for event, elem in parser.read_events():
                if event == "start":
                    stack.append(elem)
                    yield event, elem, (stack[-2] if len(stack) > 1 else None)
                else:
                    stack.pop()
                    yield event, elem, (stack[-1] if stack else None)
            if budget.spent():
                break


def _detach(elem, parent) -> None:
    if parent is not None:
        parent.remove(elem)


def _ooxml_text(elem) -> str:
    out = []
    for el in elem.iter():
        tag = _local(el.tag)
        if tag == "t":
            out.append(el.text or "")
        elif tag == "tab":
            out.append("\t")
        elif tag in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def _docx(path: Path):
    heads, parts, budget = [], [], _Budget()
    with zipfile.ZipFile(path) as z:
        for event, elem, parent in _xml_events(z, "word/document.xml", budget):
            if event != "end" or _local(elem.tag) != "p":
                continue
            text = budget.add(_ooxml_text(elem))
            parts.append(text)
            style = next((s.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "")
                          for s in elem.iter() if _local(s.tag) == "pStyle"), "")
            m = re.match(r"(heading|titre)\s*(\d)", style.lower())
            if m and int(m.group(2)) <= 2 and text.strip():
                heads.append(text.strip())
            _detach(elem, parent)
    return "\n".join(parts), heads


def _pptx(path: Path):
    heads, parts, budget = [], [], _Budget()
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        for name in slides:
            if budget.spent():
                break
            shapes: list[dict] = []
            titled = False
            for event, elem, parent in _xml_events(z, name, budget):
                tag = _local(elem.tag)
                if event == "start":
                    if tag == "sp":
                        shapes.append({"title": False, "paras": []})
                    elif tag == "ph" and shapes and elem.get("type") in ("title", "ctrTitle"):
                        shapes[-1]["title"] = True
                    continue
                if tag == "p" and shapes:
                    shapes[-1]["paras"].append(budget.add(_ooxml_text(elem)))
                    _detach(elem, parent)
                elif tag == "sp" and shapes:
                    shape = shapes.pop()
                    text = "\n".join(shape["paras"])
                    parts.append(text)
                    if shape["title"] and not titled and text.strip():
                        heads.append(text.strip().split("\n")[0])
                        titled = True
                    _detach(elem, parent)
    return "\n".join(parts), heads


_TEXTNS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_PRESENTATIONNS = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"


def _odf_text(elem) -> str:
    out = [elem.text or ""]
    for child in elem:
        tag = _local(child.tag)
        if tag == "s":
            try:
                n = int(child.get(f"{{{_TEXTNS}}}c") or 1)
            except ValueError:
                n = 1
            out.append(" " * max(1, min(n, 100)))
        elif tag == "tab":
            out.append("\t")
        elif tag == "line-break":
            out.append("\n")
        else:
            out.append(_odf_text(child))
        out.append(child.tail or "")
    return "".join(out)


def _odf(path: Path):
    heads, parts, budget = [], [], _Budget()
    titles: list[list[str]] = []  # cadres « titre » (diapositives) en cours de lecture
    with zipfile.ZipFile(path) as z:
        for event, elem, parent in _xml_events(z, "content.xml", budget):
            tag = _local(elem.tag)
            if tag == "frame" and elem.get(f"{{{_PRESENTATIONNS}}}class") == "title":
                if event == "start":
                    titles.append([])
                elif titles:
                    text = " ".join(t for t in titles.pop() if t.strip())
                    if text.strip():
                        heads.append(text.strip())
                continue
            if event != "end" or tag not in ("p", "h"):
                continue
            text = budget.add(_odf_text(elem))
            parts.append(text)
            if titles:
                titles[-1].append(text)
            if tag == "h":
                try:
                    level = int(elem.get(f"{{{_TEXTNS}}}outline-level") or 1)
                except ValueError:
                    level = 1
                if level <= 2 and text.strip():
                    heads.append(text.strip())
            _detach(elem, parent)
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
    text = text[:MAX_TEXT_CHARS]
    topics = clean_topics(heads)
    if len(topics) < 3:
        topics = clean_topics(topics + guess_headings(text))
    return text, topics
