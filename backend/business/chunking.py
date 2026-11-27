"""Découpage des supports de cours en passages pour la recherche (RAG).

Passages d'environ 200 mots, coupés de préférence en fin de phrase, avec un chevauchement de 30 mots
pour ne pas couper une idée en deux. Un titre de section (« Chapitre 2 », « 3.1 Estimation »…) commence
toujours un nouveau passage et reste attaché aux passages qui suivent. Chaque passage garde la page
(ou la diapositive) où il commence, pour citer la source.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .extractor import HEADING_RE

TARGET_WORDS = 200
MAX_WORDS = 320
OVERLAP_WORDS = 30
MIN_WORDS = 12          # un passage plus court est rattaché au précédent


@dataclass
class Chunk:
    position: int
    page: int | None
    heading: str
    text: str


# Accents séparés de leur lettre, fréquents dans les PDF produits par LaTeX (« R´egression », « mod`ele »)
SPACING_ACCENTS = {"´": "́", "`": "̀", "ˆ": "̂", "¨": "̈", "˜": "̃"}
LOOSE_ACCENT = re.compile(r"([´`ˆ¨˜])\s?([A-Za-z])")


def fix_accents(text: str) -> str:
    text = re.sub(r"([´`ˆ¨])\s?ı", r"\1i", text)              # î, ï écrits avec un i sans point
    text = LOOSE_ACCENT.sub(lambda m: unicodedata.normalize("NFC", m.group(2) + SPACING_ACCENTS[m.group(1)]), text)
    cedilla = lambda m: "ç" if "c" in m.group(0) else "Ç"   # noqa: E731 (cédille avant ou après le c)
    return re.sub(r"¸\s?[cC]|[cC]\s?¸", cedilla, text)


def clean(text: str) -> str:
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text or "")      # mots coupés en fin de ligne
    text = fix_accents(text.replace(" ", " ").replace("\x00", ""))
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def repeated_lines(pages: list[tuple[int | None, str]], min_pages: int = 3, share: float = 0.3) -> set[str]:
    """En-têtes et pieds de page : lignes identiques présentes sur une bonne partie des pages."""
    if len(pages) < min_pages:
        return set()
    counts: dict[str, int] = {}
    for _, raw in pages:
        for line in {re.sub(r"\d+", "#", line.strip()) for line in clean(raw).splitlines() if line.strip()}:
            counts[line] = counts.get(line, 0) + 1
    return {line for line, n in counts.items() if n >= max(min_pages, share * len(pages))}


MD_HEADING = re.compile(r"^#{1,6}\s+(.+)$")
SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


def heading_text(line: str) -> str | None:
    """Titre de section (« # Chapitre 2 … » en Markdown, « 3.1 Estimation », « CHAPITRE 2 »), sinon None."""
    md = MD_HEADING.match(line)
    if md:
        return md.group(1).strip("# ").strip()[:120] or None
    return line[:120] if is_heading(line) else None


def _sentences(line: str, max_words: int) -> list[list[str]]:
    """Une ligne (parfois un paragraphe entier) découpée en phrases, chacune limitée à max_words mots."""
    out = []
    for sentence in SENTENCE_END.split(line):
        words = sentence.split()
        out += [words[i:i + max_words] for i in range(0, len(words), max_words)]
    return out


def is_heading(line: str) -> bool:
    words = line.split()
    if not (3 <= len(line) <= 90) or len(words) > 12 or line.endswith((".", ",", ";")):
        return False
    if HEADING_RE.match(line):
        return True
    # titre en majuscules : de vrais mots (pas des étiquettes de figure comme « P U A T V » ou « A1 A2 A3 »)
    real_words = [w for w in words if sum(c.isalpha() for c in w) >= 3]
    return line.isupper() and len(real_words) >= max(1, len(words) // 2) and sum(len(w) for w in real_words) >= 6


SLIDE_WORDS = 150   # moins de mots par page en moyenne : diaporama (une diapositive = un passage, titré)


def is_slide_deck(pages: list[tuple[int | None, str]]) -> bool:
    paged = [t for p, t in pages if p is not None]
    if len(paged) < 3:
        return False
    counts = sorted(len(t.split()) for t in paged)
    return counts[len(counts) // 2] < SLIDE_WORDS


def slide_title(lines: list[str]) -> str:
    """Titre d'une diapositive : sa première ligne, si elle est courte et ne finit pas comme une phrase."""
    if not lines:
        return ""
    first = MD_HEADING.sub(r"\1", lines[0]).strip()
    return first[:120] if len(first.split()) <= 14 and not first.endswith((".", ",", ";")) else ""


def chunk_pages(pages: list[tuple[int | None, str]], target: int = TARGET_WORDS, max_words: int = MAX_WORDS,
                overlap: int = OVERLAP_WORDS) -> list[Chunk]:
    chunks: list[Chunk] = []
    words: list[str] = []
    fresh = 0               # mots ajoutés depuis le dernier passage (hors chevauchement)
    start_page: int | None = None
    heading = ""

    def flush(keep_overlap: bool, page):
        nonlocal words, start_page, fresh
        if fresh == 0:      # il ne reste que le chevauchement, déjà dans le passage précédent
            words = words if keep_overlap else []
            return
        if fresh < MIN_WORDS and chunks and chunks[-1].heading == heading:
            chunks[-1].text += " " + " ".join(words[-fresh:])
        else:
            chunks.append(Chunk(len(chunks), start_page, heading, " ".join(words)))
        words = words[-overlap:] if keep_overlap else []
        fresh = 0
        start_page = page

    noise = repeated_lines(pages)
    slides = is_slide_deck(pages)
    for page, raw in pages:
        lines = [line.strip() for line in clean(raw).splitlines()
                 if line.strip() and re.sub(r"\d+", "#", line.strip()) not in noise]  # sans en-têtes ni pieds de page
        if slides:   # diaporama : chaque diapositive commence un passage, titré par sa première ligne
            flush(False, page)
            title = slide_title(lines)
            if title:
                heading, lines = title, lines[1:]
            start_page = page
        for line in lines:
            title = None if slides else heading_text(line)
            if title:
                flush(False, page)
                heading = title
                start_page = page
                continue
            for sentence in _sentences(line, max_words):
                if len(words) + len(sentence) > max_words and fresh:
                    flush(True, page)   # la phrase ne tient plus : nouveau passage
                if not words:
                    start_page = page
                words.extend(sentence)
                fresh += len(sentence)
                if len(words) >= target and sentence[-1].endswith((".", "?", "!", ":")):
                    flush(True, page)
    flush(False, None)
    # passages identiques (diapositive de plan répétée à chaque partie…) : on garde le premier
    seen, unique = set(), []
    for c in chunks:
        key = " ".join(c.text.lower().split())
        if key not in seen:
            seen.add(key)
            unique.append(Chunk(len(unique), c.page, c.heading, c.text))
    return unique
