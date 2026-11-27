"""Rapprochement des noms de cours par similarité cosinus.

« Proba », « CM Probabilités - Amphi B » et « probabilites » désignent la même matière.
Méthode :
1. normalisation : minuscules, sans accents, abréviations développées (maths -> mathematiques),
   mots parasites retirés (cm, td, amphi, salle, numéros de salle…) ;
2. similarité cosinus sur les sacs de mots, où deux mots comptent comme identiques s'ils ont
   la même racine (singulier/pluriel) ou si l'un abrège l'autre (« cell » -> « cellulaire ») ;
3. similarité cosinus sur les trigrammes de caractères, retenue seulement si très élevée
   (fautes de frappe), car elle confond sinon « statistique » et « statique » ;
4. garde-fou : « Analyse 1 » et « Analyse 2 » ne sont jamais fusionnés (numéros différents).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

# Calibré sur des cas réels : les vrais doublons sont à 1,0 ; « Physique » ~ « Physique chimie »
# (nom inclus dans un autre) est à 0,71 et reste séparé par prudence.
THRESHOLD = 0.75

ABBREVIATIONS = {
    "maths": "mathematiques", "math": "mathematiques", "proba": "probabilites", "probas": "probabilites",
    "stat": "statistiques", "stats": "statistiques", "info": "informatique", "eco": "economie",
    "philo": "philosophie", "bio": "biologie", "algo": "algorithmique", "geo": "geographie",
    "hist": "histoire", "phys": "physique", "chim": "chimie", "svt": "sciences vie terre",
    "pc": "physique chimie", "ses": "sciences economiques sociales", "hg": "histoire geographie",
    "ang": "anglais", "esp": "espagnol", "all": "allemand", "fr": "francais", "compta": "comptabilite",
    "mecaflu": "mecanique fluides", "elec": "electronique", "prog": "programmation",
    "bdd": "bases donnees", "ml": "machine learning", "ia": "intelligence artificielle",
    "optim": "optimisation", "econometrie": "econometrie", "socio": "sociologie", "psycho": "psychologie",
}
NOISE = {
    "cm", "td", "tp", "tds", "tps", "cours", "amphi", "amphitheatre", "salle", "groupe", "gr", "grp",
    "seance", "revision", "revisions", "controle", "partiel", "examen", "exam", "ds", "de", "des",
    "du", "la", "le", "les", "l", "d", "et", "en", "a", "au", "aux", "pour", "sur", "un", "une",
    "intro", "introduction", "chapitre", "chap", "magistral", "dirige", "diriges", "pratique", "pratiques",
}
DISPLAY_NOISE = {"cm", "td", "tp", "amphi", "salle", "groupe", "gr", "grp", "cours", "séance", "seance"}
ROOM = re.compile(r"^[a-z]{0,3}\d{2,4}[a-z]?$")  # B204, 112, s3a…


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    words = []
    for w in re.findall(r"[a-z]+|\d+", s):
        if ROOM.match(w) and not w.isdigit():
            continue
        w = ABBREVIATIONS.get(w, w)
        words.extend(t for t in w.split() if t not in NOISE and (len(t) > 1 or t.isdigit()))
    return " ".join(words)


def _numbers(norm: str) -> set[str]:
    return {w for w in norm.split() if w.isdigit()}


def _trigrams(norm: str) -> Counter:
    grams = Counter()
    for w in norm.split():
        if w.isdigit():
            continue
        padded = f"  {w} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


def _stem(w: str) -> str:
    """Racine grossière : singulier/pluriel et e final confondus (probabilites -> probabilit)."""
    if len(w) > 3 and w[-1] in "sx":
        w = w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 4 and long_.startswith(short)  # abréviation : « cell » -> « cellulaire »


def similarity(a: str, b: str) -> float:
    """Similarité cosinus entre deux noms de cours, dans [0, 1]."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if _numbers(na) and _numbers(nb) and _numbers(na) != _numbers(nb):
        return 0.0  # Analyse 1 ≠ Analyse 2
    wa = [_stem(w) for w in na.split() if not w.isdigit()]
    wb = [_stem(w) for w in nb.split() if not w.isdigit()]
    if not wa or not wb:
        return 1.0 if na == nb else 0.0
    # Cosinus « doux » sur les sacs de mots : deux mots comptent comme identiques
    # s'ils ont la même racine ou si l'un abrège l'autre.
    matched = sum(1 for x in wa if any(_same_word(x, y) for y in wb))
    matched_b = sum(1 for y in wb if any(_same_word(x, y) for x in wa))
    words_cos = math.sqrt(matched * matched_b) / math.sqrt(len(wa) * len(wb))
    # Cosinus sur trigrammes de caractères : ne sert qu'à rattraper les fautes de frappe,
    # d'où un seuil élevé (sinon « statistique » ~ « statique »).
    tri_cos = cosine(_trigrams(" ".join(wa)), _trigrams(" ".join(wb)))
    return round(max(words_cos, tri_cos if tri_cos >= 0.9 else 0.0), 3)


def best_match(name: str, candidates, key=lambda c: c["name"], threshold: float = THRESHOLD):
    """Retourne (candidat, score) le plus proche au-dessus du seuil, sinon (None, meilleur score)."""
    best, best_score = None, 0.0
    for c in candidates:
        s = similarity(name, key(c))
        if s > best_score:
            best, best_score = c, s
    return (best, best_score) if best_score >= threshold else (None, best_score)


def clean_title(title: str) -> str:
    """Nom de matière lisible à partir d'un intitulé d'agenda (« CM Probabilités - B204 » -> « Probabilités »)."""
    parts = re.split(r"\s+[-–|/]\s+|\s*\(.*?\)\s*", title or "")
    words = [w for w in (parts[0] if parts else title).split()
             if w.lower().strip(".:") not in DISPLAY_NOISE and not ROOM.match(w.lower())]
    while words and words[0].lower() in ("de", "du", "des", "d'", "la", "le", "les", "l'"):
        words = words[1:]  # « Cours de statistique » -> « statistique », pas « de statistique »
    out = " ".join(words).strip(" -–:")
    return (out[:1].upper() + out[1:]) if out else (title or "").strip()
