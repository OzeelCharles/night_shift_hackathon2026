"""Règles pures de l'atelier de révision : découpage du cours, fiches, QCM, notation, exercices.

Aucune dépendance à Flask, à la base ou au réseau : tout part du texte des supports de cours.
Pas de modèle d'IA à installer : on repère définitions, théorèmes, formules et phrases-clés
avec des règles simples, puis on fabrique des questions (texte à trous, notion à retrouver,
vrai/faux, chapitre) dont les distracteurs viennent du cours lui-même.
"""
from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter

from backend.business.extractor import STOPWORDS

DIFFICULTIES = {"facile": "Facile", "moyen": "Moyen", "difficile": "Difficile"}
DIFFICULTY_HELP = {
    "facile": "Je découvre : fiches détaillées, 8 questions à 3 choix, surtout du vrai/faux et des définitions.",
    "moyen": "Je connais les bases : 10 questions à 4 choix, textes à trous et définitions.",
    "difficile": "Je vise l'excellence : 15 questions à 5 choix proches, fiches condensées, "
                 "une mauvaise réponse retire 0,25 point.",
}
SETTINGS = {
    "facile": {"questions": 8, "options": 3, "penalty": 0.0, "essentials": 8,
               "weights": {"vf": 3, "definition": 3, "chapitre": 2, "cloze": 1}},
    "moyen": {"questions": 10, "options": 4, "penalty": 0.0, "essentials": 6,
              "weights": {"definition": 3, "cloze": 3, "vf": 2, "chapitre": 1}},
    "difficile": {"questions": 15, "options": 5, "penalty": 0.25, "essentials": 4,
                  "weights": {"cloze": 4, "definition": 3, "vf": 1}},
}
GENERAL = "Vue d'ensemble"
# Mots de structure du document, jamais intéressants comme réponse ou distracteur
NOISE = set("""exercice exercices correction corrige corrigé corrigés solution solutions question questions
montrer calculer justifier deduire déduire suivante suivant suivantes valeurs valeur egale égale""".split())
BLANK = "……"


def suggested_difficulty(comfort: int) -> str:
    """Proposition par défaut selon l'aisance déclarée sur la matière (1 à 5)."""
    return "facile" if comfort <= 2 else "moyen" if comfort == 3 else "difficile"


def harder(difficulty: str, step: int) -> str | None:
    order = list(DIFFICULTIES)
    i = order.index(difficulty) + step
    return order[i] if 0 <= i < len(order) else None


# ---------------------------------------------------------------- texte
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _significant(s: str) -> set[str]:
    return {w for w in norm(s).split() if len(w) >= 4 and w not in STOPWORDS}


def sentences(text: str) -> list[str]:
    """Phrases exploitables. Les lignes des PDF et des diapositives sont recollées en blocs."""
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text or "")
    blocks, cur = [], []
    for line in text.splitlines():
        line = line.strip()
        bullet = re.match(r"^[•▪●◦*–-]\s+", line)
        if not line or bullet:
            if cur:
                blocks.append(" ".join(cur))
            cur = []
            line = line[bullet.end():] if bullet else ""
            if not line:
                continue
        if "=" in line or EX_HEAD.match(line):  # formule ou énoncé : jamais recollé au texte voisin
            if cur:
                blocks.append(" ".join(cur))
            blocks.append(line)
            cur = []
            continue
        cur.append(line)
        if line.endswith((".", "!", "?")):
            blocks.append(" ".join(cur))
            cur = []
    if cur:
        blocks.append(" ".join(cur))
    out = []
    for block in blocks:
        for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý«(])", re.sub(r"\s+", " ", block)):
            s = s.strip(" •*–-")
            letters = sum(c.isalpha() for c in s)
            if 30 <= len(s) <= 320 and letters >= 0.6 * len(s) and "http" not in s:
                out.append(s)
    return list(dict.fromkeys(out))


def keywords(text: str, n: int = 15) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]{4,}", (text or "").lower())
    counts = Counter(w for w in words if w not in STOPWORDS and w not in NOISE and norm(w) not in STOPWORDS)
    return [w for w, c in counts.most_common(n * 2) if c >= 2][:n] or [w for w, _ in counts.most_common(n)]


# ---------------------------------------------------------------- chapitres
def _assign(text: str, topics: list[str]) -> str:
    words = _significant(text)
    best, score = GENERAL, 0
    for t in topics:
        s = len(words & _significant(t))
        if s > score:
            best, score = t, s
    return best


def chapters_of(text: str, topics: list[str]) -> dict[str, str]:
    """Texte de chaque chapitre. On repère d'abord les titres dans le texte ; à défaut, chaque phrase
    est rangée dans le chapitre dont elle partage le plus de mots."""
    text = strip_exercises(text)
    topics = [t for t in topics if "=" not in t]  # une formule prise pour un titre n'est pas un chapitre
    keys =[(norm(t), t) for t in topics if len(norm(t)) >= 3]
    parts: dict[str, list[str]] = {GENERAL: []}
    current, found = GENERAL, set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        n = norm(line)
        heading = None
        if len(line) <= 100:
            for key, topic in keys:
                if n == key or (n.endswith(key) and len(n) - len(key) <= 14) or \
                        (n.startswith(key) and len(n) - len(key) <= 4):
                    heading = topic
                    break
        if heading:
            current = heading
            found.add(heading)
            continue
        parts.setdefault(current, []).append(line)
    if len(found) < 2 and topics:
        parts = {GENERAL: []}
        for s in sentences(text):
            parts.setdefault(_assign(s, topics), []).append(s)
    ordered = {t: "\n".join(parts[t]) for t in topics if parts.get(t)}
    if parts.get(GENERAL):
        ordered[GENERAL] = "\n".join(parts[GENERAL])
    return {k: v for k, v in ordered.items() if len(v) >= 80}


# ---------------------------------------------------------------- notions du cours
LABEL_RE = re.compile(
    r"^(D[ée]finition|Th[ée]or[èe]me|Propri[ée]t[ée]|Lemme|Corollaire|Proposition|Formule|Loi|R[èe]gle|"
    r"Principe|M[ée]thode|Remarque)s?\b\s*(?:\d+(?:\.\d+)*)?\s*(?:\(([^)]{2,60})\))?\s*[:.–-]?\s*(.{10,})$",
    re.IGNORECASE)
IS_RE = re.compile(
    r"^(?:(?:Un|Une|Le|La|Les|Des)\s+|L['’])?(?P<term>[A-Za-zÀ-ÿ][\wÀ-ÿ'’ -]{2,45}?)\s+"
    r"(?:est|sont|désigne|désignent|correspond à|représente|représentent|se définit comme|consiste à|"
    r"permet de|is|are|refers to)\s+(?P<rest>.{15,})$")
CALL_RE = re.compile(
    r"\b[Oo]n (?:appelle|nomme|définit|dit qu['’]?(?:un|une|e)?)\s+(?:(?:un|une|le|la|les|des)\s+|l['’])?"
    r"(?P<term>[A-Za-zÀ-ÿ][\wÀ-ÿ'’ -]{2,40}?)\s*(?:,|:|\btou(?:t|te|s)\b|\bla\b|\ble\b|\bun\b|\bune\b|"
    r"\bsi\b|\blorsque\b|\bquand\b|\bl['’])")
BAD_START = {"il", "ils", "elle", "elles", "ce", "cet", "cette", "ces", "cela", "ceci", "on", "qui", "c", "ca",
             "nous", "vous", "it", "this", "that", "there", "they", "dans", "pour", "par", "avec", "mais", "ainsi",
             "alors", "si", "en", "sur", "son", "sa", "ses", "leur", "notre", "tout", "toute", "chaque", "the"}


def _good_term(term: str) -> bool:
    words = norm(term).split()
    return (1 <= len(words) <= 6 and words[0] not in BAD_START and not any(w.isdigit() for w in words)
            and any(len(w) >= 4 and w not in STOPWORDS for w in words))


def notions(sents: list[str]) -> list[dict]:
    """Définitions, théorèmes, propriétés : {term, text, kind}. term peut être vide (énoncé sans nom)."""
    out, seen = [], set()
    for s in sents:
        item = None
        m = LABEL_RE.match(s)
        if m:
            kind = m.group(1).capitalize()
            item = {"term": (m.group(2) or "").strip(), "text": m.group(3).strip(), "kind": kind}
        elif (m := CALL_RE.search(s)) and _good_term(m.group("term")):
            item = {"term": m.group("term").strip(), "text": s, "kind": "Définition"}
        elif (m := IS_RE.match(s)) and _good_term(m.group("term")):
            item = {"term": m.group("term").strip(), "text": s, "kind": "Définition"}
        if item:
            key = norm(item["term"]) or norm(item["text"])[:60]
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def formulas(text: str, limit: int = 8) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if 5 <= len(line) <= 120 and "=" in line and "http" not in line and re.search(r"[A-Za-zα-ωΑ-Ω]", line) \
                and sum(c in "=+-*/^()²³√∑∫≤≥<>|" or c.isdigit() for c in line) >= 2:
            out.append(line)
    return list(dict.fromkeys(out))[:limit]


def key_sentences(sents: list[str], kws: list[str], n: int, exclude: set[str] = frozenset()) -> list[str]:
    weight = {k: len(kws) - i for i, k in enumerate(kws)}

    def score(s):
        words = re.findall(r"[A-Za-zÀ-ÿ\-]+", s.lower())
        return sum(weight.get(w, 0) for w in words) / (len(words) ** 0.5 or 1)

    best = sorted((s for s in sents if s not in exclude), key=score, reverse=True)[:n]
    return [s for s in sents if s in best]  # ordre du cours


# ---------------------------------------------------------------- fiches
def build_sheet(chapter: str, text: str, difficulty: str) -> dict:
    sents = sentences(text)
    found = notions(sents)
    kws = keywords(text, 12)
    defs = [d for d in found if d["kind"] == "Définition" and d["term"]]
    props = [d for d in found if d not in defs]
    used = {d["text"] for d in found}
    return {
        "chapter": chapter,
        "definitions": defs[:12],
        "properties": props[:8],
        "formulas": formulas(text),
        "essentials": key_sentences(sents, kws, SETTINGS[difficulty]["essentials"], used),
        "keywords": kws[:10],
        "recall": difficulty == "difficile",  # fiches en mode « cartes à retourner »
    }


def build_sheets(chapters: dict[str, str], difficulty_of) -> list[dict]:
    """difficulty_of(chapitre) -> niveau choisi pour ce chapitre."""
    sheets = [build_sheet(ch, txt, difficulty_of(ch)) for ch, txt in chapters.items()]
    return [s for s in sheets if s["definitions"] or s["properties"] or s["essentials"] or s["formulas"]]


# ---------------------------------------------------------------- QCM
def _mask(text: str, term: str) -> str | None:
    pattern = re.compile(r"(?<![\wÀ-ÿ])" + re.escape(term) + r"(?![\wÀ-ÿ])", re.IGNORECASE)
    return pattern.sub(BLANK, text) if pattern.search(text) else None


def _distractors(answer: str, pool: list[str], k: int, rng: random.Random, close: list[str] = ()) -> list[str] | None:
    """k mauvaises réponses. `close` (même chapitre, forme voisine) est privilégié en difficile."""
    taken = {norm(answer)}
    picked = []
    for group in (list(close), list(pool)):
        rng.shuffle(group)
        for p in group:
            if norm(p) not in taken and norm(answer) not in norm(p) and norm(p) not in norm(answer):
                taken.add(norm(p))
                picked.append(p)
            if len(picked) == k:
                return picked
    return None


def _similar(word: str, pool: list[str]) -> list[str]:
    """Mots de même terminaison et de longueur proche : distracteurs plus trompeurs."""
    return sorted(pool, key=lambda w: (w[-3:] != word[-3:], abs(len(w) - len(word))))[:8]


def _question(qtype, chapter, prompt, quote, answer, wrong, explanation, rng, fixed=False):
    options = [answer] + wrong
    if not fixed:
        rng.shuffle(options)
    return {"type": qtype, "chapter": chapter, "prompt": prompt, "quote": quote, "options": options,
            "answer": options.index(answer), "explanation": explanation}


def build_quiz(chapters: dict[str, str], difficulty: str, seed: int, only: str | None = None) -> list[dict]:
    """QCM tiré du cours. `only` limite les questions à un chapitre (les distracteurs restent globaux)."""
    rng = random.Random(seed)
    cfg = SETTINGS[difficulty]
    k = cfg["options"] - 1
    hard = difficulty == "difficile"
    data = {}
    for ch, txt in chapters.items():
        sents = sentences(txt)
        data[ch] = {"sents": sents, "notions": [n for n in notions(sents) if n["term"]], "kws": keywords(txt, 15),
                    "norm": norm(txt)}
    all_terms = list(dict.fromkeys(n["term"] for d in data.values() for n in d["notions"]))
    all_kws = list(dict.fromkeys(w for d in data.values() for w in d["kws"]))
    targets = [only] if only in data else list(data)

    makers: dict[str, list] = {t: [] for t in cfg["weights"]}
    for ch in targets:
        d = data[ch]
        close_terms = [n["term"] for n in d["notions"]]
        for n in d["notions"]:
            masked = _mask(n["text"], n["term"])
            if masked and "definition" in makers:
                makers["definition"].append(("definition", ch, n, masked, close_terms))
            if masked and "vf" in makers:
                makers["vf"].append(("vf", ch, n, close_terms))
        if "cloze" in makers:
            for s in key_sentences(d["sents"], d["kws"], 25):
                inside = [w for w in d["kws"] if _mask(s, w)]
                if inside:
                    makers["cloze"].append(("cloze", ch, s, inside[-1] if hard else inside[0], d["kws"]))
        if "chapitre" in makers and len(data) >= 3:
            others = [o["norm"] for c, o in data.items() if c != ch]
            for term in [n["term"] for n in d["notions"]] + d["kws"][:3]:
                if not any(f" {norm(term)} " in f" {o} " for o in others):
                    makers["chapitre"].append(("chapitre", ch, term))
    for lst in makers.values():
        rng.shuffle(lst)

    questions, used = [], set()
    weights = dict(cfg["weights"])
    while len(questions) < cfg["questions"] and any(makers[t] for t in weights):
        qtype = rng.choices([t for t in weights if makers[t]], [weights[t] for t in weights if makers[t]])[0]
        item = makers[qtype].pop()
        q = None
        if qtype == "definition":
            _, ch, n, masked, close = item
            wrong = _distractors(n["term"], all_terms + all_kws, k, rng, close if hard else ())
            if wrong and n["text"] not in used:
                q = _question("definition", ch, "Quelle notion complète cet énoncé ?", masked, n["term"], wrong,
                              n["text"], rng)
                used.add(n["text"])
        elif qtype == "cloze":
            _, ch, s, word, local = item
            pool = [w for w in all_kws if not _mask(s, w)]
            wrong = _distractors(word, pool, k, rng, _similar(word, [w for w in local if not _mask(s, w)])
                                 if hard else ())
            if wrong and s not in used:
                q = _question("cloze", ch, "Quel mot manque dans cette phrase du cours ?", _mask(s, word), word,
                              wrong, s, rng)
                used.add(s)
        elif qtype == "vf":
            _, ch, n, close = item
            if n["text"] in used:
                continue
            swap = _distractors(n["term"], all_terms, 1, rng, close if hard else ())
            if rng.random() < 0.5 or not swap:
                q = _question("vf", ch, "Vrai ou faux ?", n["text"], "Vrai", ["Faux"], "Énoncé exact du cours.",
                              rng, fixed=True)
            else:
                false = _mask(n["text"], n["term"]).replace(BLANK, swap[0])
                q = _question("vf", ch, "Vrai ou faux ?", false, "Faux", ["Vrai"],
                              f"L'énoncé exact est : « {n['text']} »", rng, fixed=True)
                q["options"], q["answer"] = ["Vrai", "Faux"], 1
            used.add(n["text"])
        elif qtype == "chapitre":
            _, ch, term = item
            wrong = _distractors(ch, [c for c in data if c not in (ch, GENERAL)], min(k, len(data) - 1), rng)
            if wrong and term not in used:
                q = _question("chapitre", ch, f"Dans quel chapitre la notion « {term} » est-elle abordée ?", "",
                              ch, wrong, f"« {term} » est vue dans le chapitre « {ch} ».", rng)
                used.add(term)
        if q:
            questions.append(q)
    return questions


def grade(questions: list[dict], answers: list[int | None], difficulty: str) -> dict:
    """Note sur 20. En difficile, une mauvaise réponse retire SETTINGS['penalty'] point (jamais sous 0)."""
    penalty = SETTINGS[difficulty]["penalty"]
    correct = wrong = 0
    by_chapter: dict[str, list[int]] = {}
    for q, a in zip(questions, answers):
        ok = a == q["answer"]
        correct += ok
        wrong += a is not None and not ok
        stats = by_chapter.setdefault(q["chapter"], [0, 0])
        stats[0] += ok
        stats[1] += 1
    total = len(questions) or 1
    score = round(max(0.0, correct - penalty * wrong) / total * 20, 2)
    return {"correct": correct, "wrong": wrong, "blank": len(questions) - correct - wrong, "score": score,
            "by_chapter": by_chapter}


def trend(scores: list[float]) -> str | None:
    """Conseil de niveau d'après les 3 dernières notes au même niveau."""
    last = scores[-3:]
    if len(last) == 3 and min(last) >= 16:
        return "up"
    if len(last) == 3 and max(last) <= 8:
        return "down"
    return None


# ---------------------------------------------------------------- exercices
# « Exercice 3. Calculer… », « Exercice : », « EXERCICE II » ; l'énoncé peut suivre sur la même ligne
EX_HEAD = re.compile(r"^\s*(?:Exercice|Exercise|Exo|Probl[èe]me)\s*(?:n\s*°\s*)?(?:(\d+|[IVX]+)\b\s*[.:)–-]?|[:.–-]|$)"
                     r"\s*(.*)$", re.IGNORECASE)
SOL_HEAD = re.compile(r"^\s*(?:Corrig[ée]|Correction|Solution|[ÉE]l[ée]ments? de (?:correction|r[ée]ponse))s?\b"
                      r"(?:.*?(?:exercice|ex\.?)\s*(\d+))?", re.IGNORECASE)
SECTION_HEAD = re.compile(r"^(?:chapitre|chapter|partie|part|section|le[cç]on|module|th[eè]me)\s+[\dIVX]", re.IGNORECASE)
HARD_WORDS = ("démontrer", "montrer que", "en déduire", "prouver", "justifier", "généraliser", "récurrence",
              "convergence", "étudier", "discuter", "prove", "show that")
EASY_WORDS = ("calculer", "donner", "citer", "compléter", "rappeler", "appliquer", "vrai ou faux", "définir",
              "recopier", "cocher", "compute")


def estimate_difficulty(statement: str) -> str:
    s = statement.lower()
    score = sum(2 for w in HARD_WORDS if w in s) - sum(1 for w in EASY_WORDS if w in s) + len(s) / 700
    return "facile" if score <= 0.5 else "moyen" if score <= 3 else "difficile"


def extract_exercises(text: str, limit: int = 25) -> list[dict]:
    """Énoncés « Exercice n » (et leur corrigé s'il est repérable) : {title, number, statement, solution}."""
    exercises: list[dict] = []
    current, target = None, "statement"
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        head = EX_HEAD.match(stripped)
        sol = SOL_HEAD.match(stripped) if len(stripped) <= 100 else None
        if sol:
            num = sol.group(1)
            match = next((e for e in exercises if num and e["number"] == num), None)
            current, target = (match or current), "solution"
            continue
        if head:
            num = head.group(1) or ""
            current = {"number": num, "title": f"Exercice {num}".strip(), "statement": head.group(2) + "\n",
                       "solution": ""}
            exercises.append(current)
            target = "statement"
            continue
        if SECTION_HEAD.match(stripped) and len(stripped) <= 100:
            current = None  # nouveau chapitre : l'énoncé précédent est terminé
            continue
        if current is not None and len(current[target]) < 2500:
            current[target] += stripped + "\n"
    out = []
    for e in exercises:
        e["statement"], e["solution"] = e["statement"].strip(), e["solution"].strip()
        if len(e["statement"]) >= 40:
            out.append(e)
    return out[:limit]


def strip_exercises(text: str) -> str:
    """Le cours sans ses énoncés ni corrigés (ils n'ont rien à faire dans une fiche ou un QCM)."""
    kept, inside = [], False
    for line in (text or "").splitlines():
        s = line.strip()
        if s and (EX_HEAD.match(s) or (len(s) <= 100 and SOL_HEAD.match(s))):
            inside = True
        elif s and len(s) <= 100 and SECTION_HEAD.match(s):
            inside = False
        if not inside:
            kept.append(line)
    return "\n".join(kept)


def assign_chapter(text: str, topics: list[str]) -> str:
    return _assign(text, topics)


def fingerprint(statement: str) -> str:
    return norm(statement)[:160]
