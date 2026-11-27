"""Lettres de motivation : rédigées par Groq d'après le profil et l'offre, sinon par un modèle à compléter.

Valable pour tout cursus : le domaine, la formation et le niveau de l'étudiant sont donnés tels quels, et la
consigne demande d'en reprendre les codes (vocabulaire, attentes du secteur). Rien n'est inventé : ce qui
manque est laissé entre crochets, à compléter.
"""
from __future__ import annotations

import re
from datetime import date

from flask import current_app

from backend.business.accounts import LEVELS, age
from backend.dao import vision

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
TONES = {"sobre": "sobre et professionnel", "dynamique": "dynamique et enthousiaste, sans familiarité",
         "creatif": "personnel et créatif, avec une accroche originale"}
LANGUAGES = {"fr": "français", "en": "anglais"}
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")
SYSTEM = (
    "Tu es conseiller en insertion professionnelle et tu aides des étudiants de toutes filières (santé, droit, "
    "commerce, ingénierie, arts, lettres, sciences, social, hôtellerie, BTP, agriculture…) à rédiger leurs "
    "lettres de motivation de stage ou d'alternance. Tu adaptes le vocabulaire, le ton et les attentes au "
    "secteur visé. Tu n'inventes jamais d'expérience, de diplôme, de chiffre ni de fait sur l'entreprise : "
    "ce qui manque est laissé entre crochets, par exemple [nom du tuteur], pour que l'étudiant le complète."
)


class LetterError(Exception):
    """Rédaction impossible (pas de clé, réseau, quota, réponse vide)."""


def available() -> bool:
    return bool(current_app.config.get("STAGES_AI", not current_app.testing)) and bool(vision.api_key())


def today_fr(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def account(user, key: str) -> str:
    """Valeur d'une colonne du compte ('' si absente) : le compte est une ligne SQLite ou un dict."""
    try:
        value = user[key]
    except (KeyError, IndexError):
        return ""
    return str(value or "").strip()


def candidate(user) -> dict:
    """Ce que le compte dit de l'étudiant, prêt à écrire (vide si non renseigné)."""
    years = age(account(user, "birth_date"), date.today())
    contact = ", ".join(x for x in (account(user, "home_city"), account(user, "phone"), account(user, "email"),
                                    account(user, "website")) if x)
    return {
        "name": account(user, "full_name") or account(user, "username"),
        "age": f"{years} ans" if years else "",
        "contact": contact,
        "home_city": account(user, "home_city"),
        "level": LEVELS.get(account(user, "level"), ""),
        "school": account(user, "school"),
        "study_city": account(user, "study_city"),
        "field": account(user, "field"),
        "program": account(user, "program"),
        "languages": account(user, "languages"),
        "license": {"oui": "permis B", "en_cours": "permis B en cours"}.get(account(user, "driving_license"), ""),
    }


def _context(profile: dict, user, target: dict) -> str:
    c = candidate(user)
    school = c["school"] + (f" ({c['study_city']})" if c["study_city"] and c["study_city"] not in c["school"] else "")
    lines = [
        ("Nom", c["name"]), ("Âge", c["age"]), ("Coordonnées", c["contact"]),
        ("Niveau d'études", c["level"]), ("Établissement", school),
        ("Domaine d'études", c["field"]), ("Formation", c["program"]), ("Langues", c["languages"]),
        ("Permis", c["license"]),
        ("Compétences", profile.get("skills")), ("Expériences (stages, jobs, projets, associations)",
                                                 profile.get("experiences")),
        ("Qualités", profile.get("qualities")),
        ("Durée de stage souhaitée", profile.get("duration")), ("Début possible", profile.get("start")),
        ("Poste visé", target.get("title")), ("Entreprise", target.get("company") or "non précisée "
                                                          "(candidature spontanée possible)"),
        ("Lieu du poste", target.get("location") or c["home_city"]),
    ]
    out = "\n".join(f"- {k} : {v}" for k, v in lines if v)
    if target.get("description"):
        out += f"\n\nTexte de l'offre :\n{' '.join(target['description'].split())[:5000]}"
    return out


def prompt(profile: dict, user, target: dict, tone: str, language: str) -> str:
    return (
        f"Rédige une lettre de motivation en {LANGUAGES.get(language, 'français')} pour cette candidature.\n\n"
        f"{_context(profile, user, target)}\n\n"
        "Consignes :\n"
        f"- Ton {TONES.get(tone, TONES['sobre'])} ; 250 à 380 mots pour le corps de la lettre.\n"
        "- Mise en forme d'une lettre : coordonnées de l'étudiant, destinataire, lieu et date "
        f"(« {today_fr()} »), objet, formule d'appel, corps, formule de politesse, signature.\n"
        "- Structure : pourquoi cette entreprise ou ce secteur, ce que l'étudiant apporte (compétences et "
        "expériences du profil reliées aux missions de l'offre), ce qu'il veut apprendre, disponibilités "
        "(durée, date de début), demande d'entretien.\n"
        "- Le permis, l'âge ou la ville ne se mentionnent que s'ils comptent pour ce poste (déplacements, "
        "terrain, proximité) ; les langues, si l'offre ou le secteur s'y prête.\n"
        "- Reprends les codes du domaine d'études et du poste visé (un stage en soins, en cabinet d'avocats, "
        "en studio de design ou en bureau d'études ne s'écrit pas pareil).\n"
        "- Réponds uniquement par la lettre, en texte brut, sans Markdown ni commentaire."
    )


def write(profile: dict, user, target: dict, tone: str = "sobre", language: str = "fr") -> str:
    """Lettre rédigée par Groq (texte brut)."""
    if not vision.api_key():
        raise LetterError("aucune clé Groq sur ce serveur")
    try:
        r = vision.session().post(URL, timeout=120, headers={"Authorization": f"Bearer {vision.api_key()}"}, json={
            "model": current_app.config.get("STAGES_MODEL", MODEL),
            "temperature": 0.7,
            "max_completion_tokens": 3000,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt(profile, user, target, tone, language)}],
        })
    except Exception as e:  # réseau, certificats
        raise LetterError("Groq ne répond pas") from e
    if r.status_code in (413, 429):
        raise LetterError("quota de Groq atteint, réessaie dans une minute")
    if r.status_code != 200:
        current_app.logger.warning("Stages : erreur Groq %s (%s)", r.status_code, r.text[:300])
        raise LetterError(f"Groq a renvoyé une erreur ({r.status_code})")
    try:
        text = r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError) as e:
        raise LetterError("réponse de Groq illisible") from e
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # au cas où un peu de Markdown passerait
    text = "\n".join(line.rstrip() for line in text.splitlines())  # espaces de fin (sauts de ligne Markdown)
    if len(text) < 200:
        raise LetterError("réponse de Groq trop courte")
    return text


def template(profile: dict, user, target: dict) -> str:
    """Lettre type, sans IA : la trame est remplie avec le profil, le reste est entre crochets."""
    def v(key, fallback):
        return (profile.get(key) or "").strip() or fallback

    c = candidate(user)
    name = c["name"]
    program = c["program"] or ("[ta formation]" + (f" ({c['level']})" if c["level"] else ""))
    school = c["school"] or "[ton établissement]"
    company = target.get("company") or "[nom de l'entreprise]"
    title = target.get("title") or "[poste visé]"
    skills = v("skills", "[deux ou trois compétences utiles pour ce poste]")
    exp = v("experiences", "[une expérience : stage, job, projet ou engagement associatif]")
    qualities = v("qualities", "[deux qualités, illustrées par un exemple]")
    field = c["field"] or "[ton domaine]"
    duration, start = v("duration", "[durée]"), v("start", "[date de début]")
    place = target.get("location") or c["home_city"] or "[ville]"
    extras = " ".join(x for x in (f"Je parle {c['languages']}." if c["languages"] else "",
                                  "Titulaire du permis B, je suis mobile." if c["license"] == "permis B" else "") if x)
    return f"""{name}
{c["contact"] or "[adresse, téléphone, e-mail]"}

{company}
[Service, nom du ou de la responsable]

{place}, le {today_fr()}

Objet : candidature pour un stage de {title}

Madame, Monsieur,

Actuellement en {program} à {school}, je souhaite mettre en pratique ma formation en {field} au sein de {company}. \
Le poste de {title} correspond précisément au projet professionnel que je construis : [ce qui t'attire dans \
l'entreprise, son secteur, ses projets ou ses valeurs].

Ma formation m'a permis d'acquérir {skills}. {exp[0].upper() + exp[1:]} m'a appris à [ce que cette expérience \
t'a apporté]. Je suis {qualities}, des atouts que je mettrai au service de vos équipes pour [une mission de l'offre].{" " + extras if extras else ""}

Ce stage serait pour moi l'occasion de [ce que tu veux apprendre] aux côtés de professionnels expérimentés. \
Je suis disponible pour une durée de {duration} à partir de {start}.

Je serais heureux·se de vous exposer plus en détail ma motivation lors d'un entretien. Je vous prie d'agréer, \
Madame, Monsieur, l'expression de mes salutations distinguées.

{name}
"""
