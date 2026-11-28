"""Vérificateur de CV : règles pures (sans Flask, sans base, sans réseau).

- normalisation des textes (minuscules, accents retirés) ;
- mots-clés d'une offre (mots et expressions de 2 ou 3 mots, mots vides exclus, petit lexique de compétences) ;
- correspondance CV / offre et score sur 100 (calcul local, ce n'est pas le score d'un vrai logiciel de tri) ;
- lisibilité par un logiciel de recrutement (ATS) : texte extractible, rubriques standard, coordonnées,
  mise en page en colonnes ou en tableaux, longueur ; chaque alerte vient avec un conseil concret.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from backend.business.extractor import STOPWORDS

# ---------------------------------------------------------------- normalisation


def normalize(text: str) -> str:
    """Minuscules, accents retirés (« Expériences » = « experiences »)."""
    text = str(text or "").replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE")
    text = re.sub("[‐-―−]", "-", text)   # tirets typographiques (« Langues – anglais »)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#]*")
WORD_RE = re.compile(r"[^\W_][\w+#]*")


def words(text: str) -> list[tuple[str, str]]:
    """(mot normalisé, mot tel qu'écrit) ; « C++ » et « C# » gardent leur forme utile."""
    out = []
    for m in WORD_RE.finditer(str(text or "")):
        for norm in TOKEN_RE.findall(normalize(m.group(0))):
            out.append((norm, m.group(0)))
    return out


def tokens(text: str) -> list[str]:
    """Mots normalisés."""
    return [n for n, _ in words(text)]


def stem(word: str) -> str:
    """Racine très simple : le pluriel ne doit pas faire manquer un mot-clé (statistique / statistiques)."""
    if len(word) > 4 and word[-1] in "sx" and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


# Mots vides : ceux de l'extracteur, complétés pour le français, l'anglais et le vocabulaire des offres
_EXTRA_STOP = """
le la les l un une des de du d au aux a et ou en dans par pour sur avec sans ne se sa son ses ce cet
il elle on nous vous ils elles je tu me te lui y qu que qui quoi dont ou est sont etre avoir sera
the a an and or of to in for on at by with as is are be will we you our your their it its this that
from not can may must per via plus tres bien bon bonne
stage stages stagiaire stagiaires alternance alternant apprentissage apprenti poste postes offre offres
mission missions profil profils candidat candidate candidats candidature entreprise societe equipe equipes
rejoindre rejoignez recherchons recherche cherchons souhaitez participer participerez assurer serez
sein cadre notre nos votre vos leur leurs ainsi afin etc mois annee annees duree debut date lieu
travailler travail h f hf fh cdi cdd job role team company position internship intern join looking
description requise requis souhaite souhaitee appreciee apprecie atout idealement minimum niveau
""".split()
STOP = {normalize(w) for w in STOPWORDS} | set(_EXTRA_STOP)
# Petits mots qui peuvent relier les termes d'une expression : « gestion de projet », « analyse des données »
LINKS = {"de", "des", "du", "d", "en", "la", "l", "et", "of", "and"}

# Lexique de compétences reconnues même citées une seule fois (outils, langages, langues, logiciels, méthodes)
# (écrits tels qu'on les affiche)
LEXICON = [s for s in """
Python|Java|JavaScript|TypeScript|SQL|NoSQL|PHP|Ruby|Rust|Kotlin|Swift|C++|C#|HTML|CSS|React|Angular|Vue|
Django|Flask|Spring|Git|GitHub|GitLab|Docker|Kubernetes|Linux|AWS|Azure|GCP|API|Matlab|SAS|SPSS|
Stata|Excel|Word|PowerPoint|Outlook|Pack Office|Microsoft Office|Google Sheets|VBA|Power BI|Looker|Qlik|
machine learning|deep learning|intelligence artificielle|data science|analyse de données|
statistiques|big data|cybersécurité|DevOps|cloud|
Figma|Canva|Photoshop|Illustrator|InDesign|Premiere Pro|After Effects|suite Adobe|Blender|SketchUp|AutoCAD|
Revit|SolidWorks|CATIA|ArchiCAD|QGIS|ArcGIS|
SAP|Salesforce|HubSpot|CRM|ERP|Cegid|QuickBooks|WordPress|Shopify|Google Analytics|SEO|SEA|
Jira|Trello|agile|Scrum|Kanban|gestion de projet|conduite de projet|Lean|Six Sigma|
comptabilité|contrôle de gestion|fiscalité|audit|marketing digital|réseaux sociaux|
community management|relation client|prospection|négociation|rédaction|veille|
anglais|espagnol|allemand|italien|portugais|chinois|arabe|japonais|russe|
English|Spanish|German|French|Italian|Chinese|TOEIC|TOEFL|IELTS|
permis B|PSC1|BAFA|HACCP
""".replace("\n", "").split("|") if s.strip()]
LEXICON_TOKENS = [tuple(stem(t) for t in tokens(s)) for s in LEXICON]

MAX_KEYWORDS = 15
MIN_RELIABLE = 5   # en dessous, l'offre est trop pauvre (intitulé seul) pour un score fiable


def _is_content(word: str) -> bool:
    return len(word) >= 3 and word not in STOP and not word.isdigit()


def _contains(seq: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return n > 0 and any(seq[i:i + n] == needle for i in range(len(seq) - n + 1))


# ---------------------------------------------------------------- mots-clés de l'offre


def extract_keywords(description: str, title: str = "", limit: int = MAX_KEYWORDS) -> list[str]:
    """Mots-clés d'une offre, du plus au moins important, de façon déterministe.

    Retenus : les compétences du lexique citées dans l'offre, les expressions de 2 ou 3 mots répétées
    (les mots de l'intitulé comptent double), puis les mots répétés. Mots vides exclus."""
    title_words, body_words = words(title), words(description)
    title_toks = [stem(n) for n, _ in title_words]
    seq = tuple(title_toks + [stem(n) for n, _ in body_words])
    surfaces = [s for _, s in title_words + body_words]
    title_set = set(title_toks)
    first: dict[str, int] = {}
    scores: Counter = Counter()
    display: dict[str, str] = {}

    def add(key: str, score: float, pos: int) -> None:
        scores[key] += score
        first.setdefault(key, pos)

    # 1. lexique : une seule mention suffit
    for label, lex in zip(LEXICON, LEXICON_TOKENS):
        for i in range(len(seq) - len(lex) + 1):
            if seq[i:i + len(lex)] == lex:
                add(" ".join(lex), 10 + len(lex), i)
                display[" ".join(lex)] = label
                break
    lexicon_keys = set(scores)

    # 2. expressions de 2 ou 3 mots (extrémités porteuses de sens, petits mots de liaison au milieu)
    phrases: Counter = Counter()
    phrase_pos: dict[str, int] = {}
    for n in (2, 3):
        for i in range(len(seq) - n + 1):
            gram = seq[i:i + n]
            if not (_is_content(gram[0]) and _is_content(gram[-1])):
                continue
            if any(not _is_content(w) and w not in LINKS for w in gram[1:-1]):
                continue
            if n == 3 and gram[1] not in LINKS and not _is_content(gram[1]):
                continue
            key = " ".join(gram)
            phrases[key] += 2 if i < len(title_toks) else 1
            phrase_pos.setdefault(key, i)
    for key, count in phrases.items():
        if count >= 2 and key not in lexicon_keys:
            add(key, 4 + count, phrase_pos[key])

    # 3. mots isolés répétés
    counts = Counter(w for w in seq if _is_content(w) and len(w) >= 4)
    for w in title_set:
        if w in counts:
            counts[w] += 1
    for i, w in enumerate(seq):
        if counts.get(w, 0) >= 2 and w not in first:
            add(w, counts[w], i)

    ranked = sorted(scores, key=lambda k: (-scores[k], first[k], k))
    out: list[str] = []
    for key in ranked:
        parts = tuple(key.split())
        # un mot déjà couvert par une expression retenue (ou l'inverse) ne compte pas deux fois
        if any(_contains(tuple(o.split()), parts) or _contains(parts, tuple(o.split())) for o in out):
            continue
        out.append(key)
        if len(out) >= limit:
            break
    return [display.get(k) or _surface(surfaces, first[k], len(k.split())) for k in out]


def _surface(surfaces: list[str], pos: int, n: int) -> str:
    """Le mot-clé tel qu'il est écrit dans l'offre (« Power BI », « Analyse de données »), en minuscules
    sauf les sigles et noms d'outils écrits avec des majuscules."""
    parts = surfaces[pos:pos + n]
    return " ".join(p if (p[1:] and not p[1:].islower()) else p.lower() for p in parts)


# ---------------------------------------------------------------- correspondance CV / offre


@dataclass
class Match:
    score: int
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def reliable(self) -> bool:
        return len(self.keywords) >= MIN_RELIABLE


def match_keywords(cv_text: str, keywords: list[str]) -> Match:
    cv = tuple(stem(t) for t in tokens(cv_text))
    found, missing = [], []
    for k in keywords:
        (found if _contains(cv, tuple(stem(t) for t in tokens(k))) else missing).append(k)
    score = round(100 * len(found) / len(keywords)) if keywords else 0
    return Match(score=int(score), found=found, missing=missing, keywords=list(keywords))


def match(cv_text: str, description: str, title: str = "") -> Match:
    """Score entier de 0 à 100, mots-clés de l'offre trouvés et manquants dans le CV."""
    return match_keywords(cv_text, extract_keywords(description, title))


def match_advice(m: Match) -> list[str]:
    tips = []
    if not m.reliable:
        tips.append("Ajoute le texte de l'offre pour un score fiable : l'intitulé seul donne trop peu de mots-clés.")
    if m.missing:
        tips.append("Ajoute-les seulement si tu as vraiment cette compétence : un recruteur vérifiera en entretien.")
        tips.append("Reprends les mots exacts de l'offre (« gestion de projet » plutôt que « pilotage d'activités ») "
                    "dans tes rubriques Compétences et Expériences.")
    elif m.keywords:
        tips.append("Ton CV reprend tous les mots-clés repérés : relis-le pour vérifier qu'ils sont bien mis en valeur.")
    return tips


# ---------------------------------------------------------------- rubriques


SECTIONS = {
    "formation": "Formation",
    "experiences": "Expériences",
    "competences": "Compétences",
    "langues": "Langues",
}
# (expression de début de ligne, rubrique) : la première qui correspond l'emporte
# (« Compétences linguistiques » annonce les langues, d'où « langues » en premier)
_HEADINGS = [
    (r"langues?( etrangeres?| vivantes?| parlees?)?|languages?( skills)?|competences linguistiques|"
     r"linguistiques?", "langues"),
    (r"parcours professionnel|experiences?( professionnelles?)?|emplois?|stages? et emplois|"
     r"work experience|professional experience|experience|employment|career", "experiences"),
    (r"formations?|education|etudes|diplomes?|cursus|scolarite|parcours( academique| scolaire)?|"
     r"academic background|qualifications", "formation"),
    (r"competences?|savoir[- ]faire|skills|hard skills|soft skills|outils|informatique|"
     r"connaissances|aptitudes|technical skills", "competences"),
]
_HEADING_RES = [(re.compile(rf"^[\W_]*({p})\b"), s) for p, s in _HEADINGS]
_ANY_HEADING_RE = re.compile(r"\b(" + "|".join(p for p, _ in _HEADINGS) + r")\b")
_SPLIT_RE = re.compile(r" {3,}|\t+")
# Après un titre en début de ligne, ce qui suit est le contenu de la rubrique : « Langues : anglais (B2) »,
# « Langues - anglais courant », « Langues | anglais », « Langues (anglais, espagnol) »
_SEPARATORS = (":", "-", "|", "/", "(", "=", ">")
# Une langue citée avec un niveau compte comme rubrique Langues, même rangée sous « Compétences »
_LANG_RE = re.compile(r"\b(anglais|espagnol|allemand|italien|portugais|chinois|mandarin|arabe|japonais|russe|"
                      r"neerlandais|coreen|turc|polonais|francais|english|spanish|german|italian|french|"
                      r"chinese|arabic)\b")
_LEVEL_RE = re.compile(r"\b([abc][12]|courant|bilingue|natif|native|maternelle|intermediaire|avance|notions|"
                       r"scolaire|professionnel|fluent|toeic|toefl|ielts|cambridge|dele|goethe|dalf|delf|hsk)\b")


def _heading_of(line: str) -> str | None:
    """Rubrique annoncée par une ligne : titre court (« EXPÉRIENCES PROFESSIONNELLES ») ou « Langues : … »."""
    norm = normalize(line).strip()
    if not norm:
        return None
    for rx, section in _HEADING_RES:
        m = rx.match(norm)
        if m:
            rest = norm[m.end():].strip()
            if (len(norm.split()) <= 5 and len(norm) <= 45) or rest.startswith(_SEPARATORS):
                return section
            return None
    return None


def _headings_in(line: str) -> set[str]:
    """Toutes les rubriques annoncées par une ligne : titre seul, titre suivi de son contenu, titres
    juxtaposés en colonnes (« FORMATION      LANGUES ») ou combinés (« Compétences et langues »)."""
    found = {_heading_of(seg) for seg in _SPLIT_RE.split(line.strip()) if seg.strip()} - {None}
    norm = normalize(line).strip()
    leftover = re.sub(r"\b(et|and)\b|[\W_]+", "", _ANY_HEADING_RE.sub("", norm))
    if found and len(norm.split()) <= 6 and not leftover:
        found |= {_heading_of(m.group(0)) for m in _ANY_HEADING_RE.finditer(norm)} - {None}
    return found


def detect_sections(text: str) -> set[str]:
    """Rubriques standard présentes (titres de ligne ou sous-rubriques, synonymes français et anglais)."""
    found: set[str] = set()
    for line in str(text or "").splitlines():
        found |= _headings_in(line)
        if "langues" not in found:
            norm = normalize(line)
            if _LANG_RE.search(norm) and _LEVEL_RE.search(norm):
                found.add("langues")
    return found


def _is_mixed(line: str) -> bool:
    segments = [s for s in _SPLIT_RE.split(line.strip()) if s.strip()]
    if len(segments) >= 2 and len({_heading_of(s) for s in segments} - {None}) >= 2:
        return True
    norm = normalize(line)
    # « Compétences et langues » est un titre combiné, pas deux colonnes
    if len(norm.split()) > 6 or re.search(r"\b(et|and)\b|[&/,+]", norm):
        return False
    found = {_heading_of(m.group(0)) for m in _ANY_HEADING_RE.finditer(norm)} - {None}
    leftover = re.sub(r"[\W_]+", "", _ANY_HEADING_RE.sub("", norm))
    return len(found) >= 2 and not leftover


def mixed_heading_lines(text: str) -> int:
    """Lignes qui ne portent que des titres de rubriques (« FORMATION      EXPÉRIENCES ») : signe de colonnes."""
    return sum(1 for line in str(text or "").splitlines() if line.strip() and _is_mixed(line))


# ---------------------------------------------------------------- coordonnées


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RES = [
    re.compile(r"(?<!\d)(?:\+33\s?\(?0?\)?\s?|0033\s?|0)[1-9](?:[\s.\-]?\d{2}){4}(?!\d)"),      # France
    re.compile(r"(?<![\w+])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4}(?!\d)"),      # international
]


def has_email(text: str) -> bool:
    return bool(EMAIL_RE.search(str(text or "")))


def has_phone(text: str) -> bool:
    return any(rx.search(str(text or "")) for rx in PHONE_RES)


# ---------------------------------------------------------------- mise en page


# Grand espace au milieu d'une ligne (deux colonnes mises côte à côte par l'extraction). Les tabulations seules
# (dates alignées à droite dans Word) ne comptent pas : un logiciel de tri les lit sans peine.
GAP_RE = re.compile(r"\S {5,}\S")


def layout_signals(text: str, tables: int = 0) -> dict:
    """Indices de colonnes ou de tableaux (le texte extrait ne donne pas la géométrie : heuristique prudente)."""
    lines = [l for l in str(text or "").splitlines() if l.strip()]
    short = sum(1 for l in lines if len(l.split()) <= 2)
    gaps = sum(1 for l in lines if GAP_RE.search(l.strip()))
    mixed = mixed_heading_lines(text)
    short_ratio = short / len(lines) if lines else 0.0
    columns = gaps >= 3 or mixed >= 1 or (len(lines) >= 20 and short_ratio > 0.6)
    return {"tables": int(tables or 0), "lines": len(lines), "short_ratio": round(short_ratio, 2),
            "gap_lines": gaps, "mixed_headings": mixed, "columns": columns}


# ---------------------------------------------------------------- alertes ATS


GRAVITY = {"haute": 0, "moyenne": 1, "basse": 2}
GRAVITY_LABELS = {"haute": "Bloquant", "moyenne": "Important", "basse": "À surveiller"}
MIN_CHARS_PER_PAGE = 200
MIN_WORDS = 150
MAX_PAGES = 2
_SECTION_TIPS = {
    "formation": "Ajoute une rubrique « Formation » (diplôme en cours, établissement, années) : c'est la première "
                 "que lit un recruteur de stagiaires.",
    "experiences": "Ajoute une rubrique « Expériences » : stages, jobs d'été, associations et projets comptent, "
                   "avec des verbes d'action.",
    "competences": "Ajoute une rubrique « Compétences » qui reprend les outils et savoir-faire attendus par tes offres.",
    "langues": "Ajoute une rubrique « Langues » avec ton niveau (B2, C1…) ou ta certification (TOEIC, IELTS).",
}


def _alert(code: str, gravity: str, title: str, advice: str) -> dict:
    return {"code": code, "gravity": gravity, "label": GRAVITY_LABELS[gravity], "title": title, "advice": advice}


def ats_alerts(text: str, pages: int | None = 1, tables: int = 0) -> list[dict]:
    """Alertes de lisibilité, de la plus à la moins grave ; chacune avec un conseil concret."""
    text = str(text or "")
    pages = max(int(pages or 1), 1)
    words = len(text.split())
    alerts = []
    if len(text.strip()) / pages < MIN_CHARS_PER_PAGE:
        alerts.append(_alert(
            "extraction", "haute", "Texte presque impossible à lire pour un logiciel (CV scanné ou en image ?)",
            "Refais un PDF depuis Word ou LibreOffice (« Enregistrer sous » ou « Exporter en PDF ») au lieu d'un "
            "scan, d'une photo ou d'une image."))
    if words >= 30:   # sur un texte vide, les autres alertes n'apprendraient rien
        signals = layout_signals(text, tables)
        if signals["tables"] or signals["columns"]:
            why = []
            if signals["tables"]:
                why.append(f"{signals['tables']} tableau{'x' if signals['tables'] > 1 else ''}")
            if signals["columns"]:
                why.append("texte qui semble en colonnes")
            alerts.append(_alert(
                "mise_en_page", "haute", f"Mise en page difficile à lire pour un logiciel ({', '.join(why)})",
                "Passe à une seule colonne, sans tableau ni zone de texte : les logiciels de tri lisent de gauche "
                "à droite et mélangent les colonnes."))
        present = detect_sections(text)
        for key, label in SECTIONS.items():
            if key not in present:
                alerts.append(_alert(f"rubrique_{key}", "moyenne", f"Rubrique « {label} » introuvable",
                                     _SECTION_TIPS[key]))
        if not has_email(text):
            alerts.append(_alert("email", "haute", "Adresse e-mail introuvable",
                                 "Écris ton e-mail en toutes lettres en haut du CV (pas seulement dans une icône "
                                 "ou une image)."))
        if not has_phone(text):
            alerts.append(_alert("telephone", "moyenne", "Numéro de téléphone introuvable",
                                 "Ajoute ton numéro au format 06 12 34 56 78 ou +33 6 12 34 56 78, en texte."))
        if words < MIN_WORDS:
            alerts.append(_alert("court", "moyenne", f"CV très court ({words} mots)",
                                 "Détaille tes expériences et projets : ce que tu as fait, avec quels outils, et "
                                 "le résultat. Vise au moins 250 mots."))
    if pages > MAX_PAGES:
        alerts.append(_alert("long", "basse", f"CV long ({pages} pages)",
                             "Pour un stage, tiens en une page, deux au plus : garde ce qui sert le poste visé."))
    return sorted(alerts, key=lambda a: GRAVITY[a["gravity"]])
