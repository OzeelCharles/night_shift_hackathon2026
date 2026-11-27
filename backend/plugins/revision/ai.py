"""Génération par Claude : QCM, exercices et notebooks tirés des supports de cours.

Claude lit les PDF eux-mêmes (formules comprises), pas le texte extrait, que l'extraction abîme
(σ devient ε, Σ devient !, mots collés). Les mathématiques sont rendues en LaTeX ($…$, $$…$$),
affichées dans les pages par KaTeX.

Deux fournisseurs, choisis d'après la clé trouvée (voir api_key) :
- Claude (paquet `anthropic`) : lit les PDF eux-mêmes ;
- Groq (clé « gsk_… », API compatible OpenAI, appelée avec requests) : ne lit que le texte extrait des supports.
La clé n'est jamais dans le code ni dans git. Sinon, l'atelier garde ses règles simples (generator.py). Toute erreur remonte en AIError.
"""
from __future__ import annotations

import base64
import json
import os
import random

from flask import current_app

MAX_PDF_BYTES = 24 * 1024 * 1024  # la requête est limitée à 32 Mo, base64 compris
SYSTEM = (
    "Tu es un professeur exigeant qui prépare des révisions à partir des supports de cours fournis. "
    "Lis-les en entier, formules comprises, et appuie-toi uniquement sur leur contenu et leurs notations. "
    "Écris en français. Toute expression mathématique est en LaTeX : $…$ en ligne, $$…$$ pour une formule "
    "isolée (par exemple $f(x) = \\frac{1}{\\sigma\\sqrt{2\\pi}} e^{-\\frac{(x-\\mu)^2}{2\\sigma^2}}$). "
    "N'utilise jamais de caractères Unicode mathématiques à la place du LaTeX."
)
LEVELS = {
    "facile": "facile : notions et formules centrales du cours, application directe",
    "moyen": "moyen : maîtrise des formules et des résultats, petits calculs, conditions d'application",
    "difficile": "difficile : résultats fins, hypothèses exactes, pièges de calcul, liens entre chapitres",
}


class AIError(Exception):
    """Génération impossible (pas de clé, réseau, refus, réponse inexploitable)."""


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
# Offre gratuite de Groq : 8 000 jetons par minute, entrée et sortie comprises (≈ 3 caractères par jeton).
GROQ_MAX_CHARS = 9_000  # extrait du cours envoyé, réparti entre les supports
GROQ_MAX_TOKENS = 3_500  # longueur maximale de la réponse


def available() -> bool:
    """Désactivé par défaut pendant les tests (REVISION_AI=True pour l'activer)."""
    if not current_app.config.get("REVISION_AI", not current_app.testing):
        return False
    if provider() == "groq":
        return True
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(api_key())


def _read_key(config_name: str, filename: str) -> str:
    key = current_app.config.get(config_name) or os.environ.get(config_name, "")
    path = os.path.join(current_app.instance_path, filename)
    if not key and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    return key


def api_key() -> str:
    """Clé de l'API, dans l'ordre : variable d'environnement ANTHROPIC_API_KEY, puis fichier
    instance/anthropic_api_key (le dossier instance/ est ignoré par git, comme la clé secrète du site)."""
    return _read_key("ANTHROPIC_API_KEY", "anthropic_api_key")


def groq_key() -> str:
    """GROQ_API_KEY (variable ou instance/groq_api_key), ou une clé « gsk_ » rangée à la place de celle de Claude."""
    key = _read_key("GROQ_API_KEY", "groq_api_key")
    if not key and api_key().startswith("gsk_"):
        key = api_key()
    return key


def provider() -> str:
    """« groq » si une clé Groq est disponible, sinon « claude »."""
    return "groq" if groq_key() else "claude"


def name() -> str:
    """Nom du fournisseur affiché dans les messages."""
    return "Groq" if provider() == "groq" else "Claude"


# ---------------------------------------------------------------- supports envoyés à Claude
def course_blocks(docs: list, texts: dict[str, str]) -> list[dict]:
    """Un bloc « document » par support : le PDF lui-même, sinon son texte extrait (diapositives, Word…)."""
    blocks, seen, budget = [], set(), MAX_PDF_BYTES
    text_only = provider() == "groq"  # Groq ne lit pas les PDF : texte extrait seulement
    for d in docs:
        if d["filename"] in seen:  # même fichier déposé deux fois
            continue
        seen.add(d["filename"])
        path = d["stored_path"]
        if not text_only and path.lower().endswith(".pdf") and os.path.isfile(path) and os.path.getsize(path) <= budget:
            with open(path, "rb") as f:
                data = f.read()
            budget -= len(data)
            source = {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode()}
        elif texts.get(f"doc:{d['id']}"):
            source = {"type": "text", "media_type": "text/plain", "data": texts[f"doc:{d['id']}"]}
        else:
            continue
        blocks.append({"type": "document", "source": source, "title": d["filename"]})
    if not blocks:
        raise AIError("Aucun support lisible pour cette matière.")
    blocks[-1]["cache_control"] = {"type": "ephemeral"}  # les supports sont relus à chaque génération
    return blocks


def _ask(course: list[dict], task: str, schema: dict) -> dict:
    if provider() == "groq":
        return _ask_groq(course, task, schema)
    return _ask_claude(course, task, schema)


def _system_certs_session():
    """Session requests qui vérifie les certificats avec le magasin du système plutôt que certifi :
    un antivirus qui inspecte le TLS (Norton, Kaspersky…) n'ajoute sa racine qu'au magasin du système."""
    import ssl

    import requests
    from requests.adapters import HTTPAdapter

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = ssl.create_default_context()
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", _Adapter())
    return session


def _ask_groq(course: list[dict], task: str, schema: dict) -> dict:
    """Mode JSON de Groq : le schéma est donné dans la consigne, la réponse est un objet JSON."""
    import requests

    docs = [b for b in course if b["source"]["type"] == "text"]
    share = current_app.config.get("REVISION_GROQ_MAX_CHARS", GROQ_MAX_CHARS) // max(len(docs), 1)
    text = "\n\n".join(f"=== Support : {b['title']} (extrait) ===\n{' '.join(b['source']['data'].split())[:share]}"
                       for b in docs)
    prompt = (f"Supports de cours :\n{text}\n\n=== Consigne ===\n{task}\n\n"
              "Les retours à la ligne du texte sont de simples sauts de ligne (pas de \\\\ hors des formules).\n"
              "Sois concis : la réponse entière doit rester courte (moins de 1 500 mots, code compris).\n"
              "Réponds uniquement par un objet JSON conforme à ce schéma JSON (toutes les clés sont obligatoires) :\n"
              f"{json.dumps(schema, ensure_ascii=False)}")
    try:
        r = _system_certs_session().post(GROQ_URL, timeout=120, headers={"Authorization": f"Bearer {groq_key()}"}, json={
            "model": current_app.config.get("REVISION_GROQ_MODEL", GROQ_MODEL),
            "temperature": 0.4,
            "max_tokens": current_app.config.get("REVISION_GROQ_MAX_TOKENS", GROQ_MAX_TOKENS),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        })
    except requests.RequestException as e:
        raise AIError("Groq ne répond pas (connexion impossible).") from e
    if r.status_code in (413, 429):
        current_app.logger.warning("Atelier de révision : quota Groq (%s)", r.text[:300])
        raise AIError("Quota de Groq atteint : réessaie dans une minute ou demande moins d'éléments.")
    if r.status_code == 400 and "json_validate_failed" in r.text:
        raise AIError("Groq a renvoyé un JSON invalide (réponse sans doute trop longue) : réessaie.")
    if r.status_code != 200:
        current_app.logger.warning("Atelier de révision : erreur Groq %s (%s)", r.status_code, r.text[:300])
        raise AIError(f"Groq a renvoyé une erreur ({r.status_code}).")
    try:
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError) as e:
        raise AIError("Réponse de Groq illisible.") from e
    missing = [k for k in schema["required"] if k not in out]
    if missing:
        raise AIError(f"Réponse de Groq incomplète (manque : {', '.join(missing)}).")
    return out


def _ask_claude(course: list[dict], task: str, schema: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key())
    try:
        with client.beta.messages.stream(
            model=current_app.config.get("REVISION_AI_MODEL", "claude-opus-5-5"),
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # en cas de refus, Claude relance la demande sur le modèle de secours conseillé
            thinking={"type": "adaptive"},
            output_config={"effort": current_app.config.get("REVISION_AI_EFFORT", "high"),
                           "format": {"type": "json_schema", "schema": schema}},
            system=SYSTEM,
            messages=[{"role": "user", "content": course + [{"type": "text", "text": task}]}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIConnectionError as e:
        raise AIError("Claude ne répond pas (connexion impossible).") from e
    except anthropic.RateLimitError as e:
        raise AIError("Trop de demandes envoyées à Claude : réessaie dans une minute.") from e
    except anthropic.APIStatusError as e:
        current_app.logger.warning("Atelier de révision : erreur Claude %s (%s)", e.status_code, e.message)
        raise AIError(f"Claude a renvoyé une erreur ({e.status_code}).") from e
    if message.stop_reason == "refusal":
        raise AIError("Claude a refusé de traiter ces supports.")
    if message.stop_reason == "max_tokens":
        raise AIError("La réponse de Claude a été coupée : demande moins d'éléments.")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("Réponse de Claude illisible.") from e


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR = {"type": "string"}


def _scope(chapters: list[str], chapter: str, difficulty: str) -> str:
    where = f"le chapitre « {chapter} »" if chapter else "l'ensemble du cours"
    listing = "\n".join(f"- {c}" for c in chapters) or "- (pas de chapitres déclarés)"
    return (f"Porte sur {where}. Niveau {LEVELS[difficulty]}.\n"
            f"Chapitres de la matière (recopie exactement l'un d'eux dans le champ chapter) :\n{listing}")


# ---------------------------------------------------------------- QCM
def quiz(course, chapters: list[str], chapter: str, difficulty: str, n: int, n_options: int,
         rng: random.Random) -> list[dict]:
    """Questions au format de generator.build_quiz : {type, chapter, prompt, quote, options, answer, explanation}."""
    task = (
        f"Rédige un QCM de {n} questions, chacune avec exactement {n_options} propositions dont une seule juste.\n"
        f"{_scope(chapters, chapter, difficulty)}\n"
        "Exigences :\n"
        "- Teste le vrai contenu du cours : définitions exactes, formules (densités, lois, estimateurs, "
        "noyaux, théorèmes et leurs hypothèses), interprétations, petits calculs.\n"
        "- Chaque question se comprend seule ; les notations sont celles du cours.\n"
        "- Les mauvaises propositions sont plausibles mais fausses sans ambiguïté (signe, exposant, facteur, "
        "hypothèse manquante, confusion classique) ; jamais « toutes les réponses ».\n"
        "- Mets la bonne réponse en première position dans options (l'ordre sera mélangé ensuite).\n"
        "- quote : formule ou énoncé utile à la question (vide si inutile).\n"
        "- explanation : pourquoi la bonne réponse est juste, avec la formule correcte et l'endroit du cours."
    )
    schema = _obj({"questions": {"type": "array", "items": _obj({
        "chapter": STR, "prompt": STR, "quote": STR, "options": {"type": "array", "items": STR}, "explanation": STR,
    })}})
    out = []
    for q in _ask(course, task, schema)["questions"]:
        options = [o.strip() for o in q["options"] if o.strip()]
        if len(options) < 2 or len(set(options)) != len(options):
            continue
        right = options[0]
        rng.shuffle(options)
        out.append({"type": "ia", "chapter": q["chapter"] if q["chapter"] in chapters else (chapter or "Vue d'ensemble"),
                    "prompt": q["prompt"], "quote": q["quote"], "options": options,
                    "answer": options.index(right), "explanation": q["explanation"]})
    return out[:n]


# ---------------------------------------------------------------- exercices
def exercises(course, chapters: list[str], chapter: str, difficulty: str, n: int = 3) -> list[dict]:
    """[{title, chapter, statement, solution}] : exercices inédits fidèles au cours, avec corrigé détaillé."""
    task = (
        f"Rédige {n} exercices inédits avec leur corrigé détaillé.\n{_scope(chapters, chapter, difficulty)}\n"
        "Chaque énoncé est complet (données, questions numérotées) et se résout avec les résultats du cours. "
        "Le corrigé détaille chaque étape de calcul. title : titre court sans numéro."
    )
    schema = _obj({"exercises": {"type": "array", "items": _obj(
        {"title": STR, "chapter": STR, "statement": STR, "solution": STR})}})
    return [e for e in _ask(course, task, schema)["exercises"] if e["statement"].strip()][:n]


# ---------------------------------------------------------------- notebooks
def notebook(course, subject_name: str, chapters: list[str], chapter: str, difficulty: str) -> dict:
    """Notebook Jupyter (nbformat 4) : énoncés, cellules de code à compléter, corrigés regroupés à la fin."""
    task = (
        f"Prépare un notebook Jupyter d'exercices pratiques en Python sur « {subject_name} ».\n"
        f"{_scope(chapters, chapter, difficulty)}\n"
        f"{'2 exercices courts' if provider() == 'groq' else '3 à 5 exercices'} qui font implémenter et expérimenter les méthodes du cours (numpy, scipy, matplotlib, "
        "scikit-learn si utile), sur des données simulées pour que tout s'exécute sans fichier externe.\n"
        "- intro : objectifs et rappels de formules en Markdown + LaTeX.\n"
        "- setup_code : imports et génération des données.\n"
        "- instructions : énoncé en Markdown + LaTeX ; starter_code : code à trous marqués # TODO ; "
        "solution_code : code complet et exécutable."
    )
    schema = _obj({"title": STR, "intro": STR, "setup_code": STR, "exercises": {"type": "array", "items": _obj(
        {"title": STR, "instructions": STR, "starter_code": STR, "solution_code": STR})}})
    nb = _ask(course, task, schema)
    if not nb["exercises"]:
        raise AIError("Claude n'a proposé aucun exercice pour ce notebook.")
    return nb


def to_ipynb(nb: dict) -> str:
    def cell(kind, text):
        c = {"cell_type": kind, "metadata": {}, "source": text.strip()}
        return c | ({"execution_count": None, "outputs": []} if kind == "code" else {})

    cells = [cell("markdown", f"# {nb['title']}\n\n{nb['intro']}"), cell("code", nb["setup_code"])]
    for i, e in enumerate(nb["exercises"], 1):
        cells += [cell("markdown", f"## Exercice {i}. {e['title']}\n\n{e['instructions']}"),
                  cell("code", e["starter_code"])]
    cells.append(cell("markdown", "---\n# Corrigés\n\nÀ n'ouvrir qu'après avoir cherché."))
    for i, e in enumerate(nb["exercises"], 1):
        cells += [cell("markdown", f"### Corrigé de l'exercice {i}"), cell("code", e["solution_code"])]
    return json.dumps({"cells": cells, "nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
        "language_info": {"name": "python"}}}, ensure_ascii=False, indent=1)
