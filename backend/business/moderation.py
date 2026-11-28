"""Filtre de mots interdits (SEC-05) : règles pures, sans accès aux fichiers ni à Flask.

La liste des termes vient de backend/data/mots_interdits.txt (lue par dao/wordlists.py) et se prépare une fois
avec `compile_terms`. La détection résiste à la casse, aux accents, aux substitutions courantes (@→a, 0→o,
1→i, 3→e, 4→a, €→e, $→s…), aux lettres répétées (« connnnard ») et aux séparateurs glissés entre les
lettres (« c.o.n », « e n c u l é », « p-u-t-e »), sans refuser les mots qui contiennent un terme par hasard
(« Concarneau », « analyse », « Scunthorpe », « salopette », « computer »).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Substitutions de caractères (avant la suppression des accents) puis des chiffres (dans les mots)
_SYMBOLS = str.maketrans({"@": "a", "€": "e", "$": "s", "!": "i", "|": "i", "£": "l", "¢": "c", "§": "s"})
_DIGITS = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g"})
_DIGITS_ALT = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g"})
_REPEATS = re.compile(r"(.)\1+")
MESSAGE = "Ce texte contient un terme interdit (insulte, propos haineux ou contenu sexuel) : choisis-en un autre."


@dataclass(frozen=True)
class Terms:
    words: frozenset  # termes à reconnaître comme mot entier (forme repliée)
    inner: frozenset  # termes reconnus aussi à l'intérieur d'un mot (forme repliée)


def _fold(text: str) -> str:
    """Minuscules, symboles substitués, sans accents."""
    text = str(text or "").lower().replace("|_|", "u").translate(_SYMBOLS)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _squeeze(word: str) -> str:
    """Lettres répétées ramenées à une seule (« connnnard » et « connard » → « conard »)."""
    return _REPEATS.sub(r"\1", word)


def _key(term: str) -> str:
    return _squeeze(re.sub(r"[^a-z]", "", _fold(term)))


def compile_terms(lines) -> Terms:
    words, inner = set(), set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        target = inner if line.startswith("*") else words
        key = _key(line.lstrip("*"))
        if key:
            target.add(key)
    return Terms(frozenset(words | inner), frozenset(inner))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _fold(text)) if t]


def _candidates(tokens: list[str]) -> set[str]:
    """Mots du texte, plus les lettres isolées recollées (« c o n » → « con ») et les paires de mots voisins
    (« ta gueule »)."""
    out = set(tokens)
    run: list[str] = []
    for t in tokens + [""]:
        if len(t) == 1:
            run.append(t)
            continue
        if len(run) > 1:
            out.add("".join(run))
        run = []
    out |= {a + b for a, b in zip(tokens, tokens[1:])}
    return out


def _forms(word: str) -> set[str]:
    forms = set()
    for table in (_DIGITS, _DIGITS_ALT):
        w = re.sub(r"[^a-z]", "", word.translate(table))
        if w:
            w = _squeeze(w)
            forms.add(w)
            if len(w) > 3 and w[-1] in "sx":  # pluriel
                forms.add(w[:-1])
    return forms


def contains_banned(text: str, terms: Terms) -> bool:
    """Vrai si le texte contient un terme interdit (mot entier, ou terme « intérieur » n'importe où)."""
    tokens = _tokens(text)
    if not tokens:
        return False
    for candidate in _candidates(tokens):
        if _forms(candidate) & terms.words:
            return True
    whole = set()
    for table in (_DIGITS, _DIGITS_ALT):
        whole.add(_squeeze(re.sub(r"[^a-z]", "", "".join(tokens).translate(table))))
    return any(term in w for w in whole for term in terms.inner)


def errors(text: str, terms: Terms, message: str = MESSAGE) -> list[str]:
    """Message clair, qui ne répète pas le terme refusé."""
    return [message] if contains_banned(text, terms) else []
