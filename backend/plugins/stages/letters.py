"""Lettres de motivation : rédigées par Groq d'après le compte, le profil candidat et l'offre, sinon par une
lettre type remplie avec les mêmes informations.

Les deux suivent la même trame (TRAME), tirée de vraies lettres de stage qui ont fait leurs preuves :
en-tête, accroche (formation, période, projet professionnel), intérêt pour l'entreprise, formation,
projets, langues et personnalité, clôture. Seuls la structure et le registre en sont repris, jamais le
contenu. Valable pour tout cursus : le domaine et la formation de l'étudiant sont donnés tels quels.
Rien n'est inventé : ce qui manque est laissé entre crochets, à compléter.
"""
from __future__ import annotations

import re
from datetime import date

from flask import current_app

from backend.business.accounts import LEVELS, age
from backend.dao import groq

MODEL = "openai/gpt-oss-120b"
TONES = {"sobre": "sobre et professionnel", "dynamique": "dynamique et enthousiaste, sans familiarité",
         "creatif": "personnel et créatif, avec une accroche originale"}
LANGUAGES = {"fr": "français", "en": "anglais"}
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")
SYSTEM = (
    "Tu es conseiller en insertion professionnelle et tu aides des étudiants de toutes filières (santé, droit, "
    "commerce, ingénierie, arts, lettres, sciences, social, hôtellerie, BTP, agriculture…) à rédiger leurs "
    "lettres de motivation de stage ou d'alternance. Tu adaptes le vocabulaire et les attentes au secteur "
    "visé. Tu n'inventes jamais d'expérience, de diplôme, de projet, de chiffre ni de fait sur l'entreprise : "
    "ce qui manque est laissé entre crochets, par exemple [nom du tuteur], pour que l'étudiant le complète."
)
# Trame des lettres de référence : structure, enchaînements et formules, sans aucun contenu personnel.
TRAME = """\
En-tête, une information par ligne : nom de l'étudiant ; ville ; téléphone et e-mail ; ligne vide ; nom de \
l'entreprise ; son adresse si elle est connue ; ligne vide ; « À <ville>, le <date> » ; \
« Objet : Stage - <intitulé exact du poste> » ; « Madame, Monsieur, ».
Paragraphe 1, l'accroche : « Actuellement en <formation> à <établissement> (<ville>)[, avec une spécialisation \
en …], je recherche un stage en <domaine> <période>, afin de mettre en pratique mes connaissances et \
d'acquérir une expérience concrète au sein de votre entreprise. » Puis : intégrer une équipe de <entreprise> \
constituerait une étape significative dans le projet de devenir <métier visé> et permettrait de découvrir \
le fonctionnement d'une équipe <métier> au sein d'une telle structure.
Paragraphe 2, l'intérêt pour l'entreprise : « Mon intérêt pour <entreprise> est né … » avec une raison \
concrète donnée par le profil ou l'offre (les missions décrites) ; sinon une indication entre crochets, \
comme [comment tu as connu l'entreprise]. Puis « Au-delà de cet aspect, … » pour ce qui attire tout \
particulièrement.
Paragraphe 3, la formation : « Ma formation … m'a permis d'acquérir de solides compétences en … », ce que \
l'étudiant maîtrisera à l'issue de l'année, puis « Tout cela complète … » avec l'expérience déjà acquise \
(stage, job), telle que le profil la décrit.
Paragraphe 4, les projets : « J'ai pu mobiliser ces compétences à travers plusieurs projets significatifs \
(détaillés dans mon CV) : … », puis « Actuellement, je travaille sur … » seulement si le profil mentionne \
un projet en cours.
Paragraphe 5, langues et personnalité : le niveau de langue (et sa certification), puis « De nature …, \
j'apprécie … », puis « Enfin, j'ai à cœur de faire mes preuves au sein d'une structure stimulante qui \
saura me lancer des défis. »
Paragraphe 6, la clôture, presque mot pour mot : « Ce serait un plaisir de vous présenter plus en détail \
ma candidature lors d'un entretien ; je vous remercie par avance de l'attention portée à ma démarche. »
Formule finale : « Veuillez agréer, Madame, Monsieur, l'expression de mes salutations distinguées. », puis le nom.
Registre : soutenu, à la première personne, phrases longues et bien liées (« En effet », « Au-delà de cet \
aspect », « Enfin »), aucune liste à puces, 400 à 480 mots pour le corps de la lettre."""

LetterError = groq.GroqError
MAX_LETTER_CHARS = 12_000  # même limite que l'édition d'une lettre


def available() -> bool:
    return groq.enabled("STAGES_AI")


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
    reach = ", ".join(x for x in (account(user, "phone"), account(user, "email")) if x)
    return {
        "name": account(user, "full_name") or account(user, "username"),
        "age": f"{years} ans" if years else "",
        "reach": reach,
        "contact": ", ".join(x for x in (account(user, "home_city"), reach, account(user, "website")) if x),
        "home_city": account(user, "home_city"),
        "website": account(user, "website"),
        "level": LEVELS.get(account(user, "level"), ""),
        "school": account(user, "school"),
        "study_city": account(user, "study_city"),
        "field": account(user, "field"),
        "program": account(user, "program"),
        "languages": account(user, "languages"),
        "license": {"oui": "permis B", "en_cours": "permis B en cours"}.get(account(user, "driving_license"), ""),
    }


def _lower_first(text: str) -> str:
    """« Droit » devient « droit », mais un sigle comme « BTP » ou « SNCF » reste tel quel."""
    return text[0].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def _period(profile: dict) -> str:
    start, duration = (profile.get("start") or "").strip(), (profile.get("duration") or "").strip()
    if start and duration:
        return f"à partir de {start}, pour une durée de {duration}"
    if start:
        return f"à partir de {start}"
    if duration:
        return f"d'une durée de {duration}"
    return "[période : de mois à mois]"


def _context(profile: dict, user, target: dict) -> str:
    c = candidate(user)
    school = c["school"] + (f" ({c['study_city']})" if c["study_city"] and c["study_city"] not in c["school"] else "")
    lines = [
        ("Nom", c["name"]), ("Âge", c["age"]), ("Ville", c["home_city"]), ("Téléphone et e-mail", c["reach"]),
        ("Site ou LinkedIn", c["website"]),
        ("Niveau d'études", c["level"]), ("Établissement", school),
        ("Domaine d'études", c["field"]), ("Formation", c["program"]), ("Langues", c["languages"]),
        ("Permis", c["license"]), ("Métier visé", profile.get("goal")),
        ("Compétences", profile.get("skills")),
        ("Expériences (stages, jobs, associations)", profile.get("experiences")),
        ("Projets marquants", profile.get("projects")), ("Qualités", profile.get("qualities")),
        ("Période de stage", _period(profile) if profile.get("start") or profile.get("duration") else ""),
        ("Poste visé", target.get("title")), ("Entreprise", target.get("company") or "non précisée "
                                                          "(candidature spontanée possible)"),
        ("Lieu du poste", target.get("location")),
    ]
    out = "\n".join(f"- {k} : {v}" for k, v in lines if v)
    if target.get("description"):
        out += f"\n\nTexte de l'offre :\n{' '.join(target['description'].split())[:5000]}"
    return out


def prompt(profile: dict, user, target: dict, tone: str, language: str) -> str:
    return (
        f"Rédige une lettre de motivation en {LANGUAGES.get(language, 'français')} pour cette candidature.\n\n"
        f"{_context(profile, user, target)}\n\n"
        f"Suis exactement cette trame, celle de lettres qui ont fait leurs preuves (traduis-la si la lettre "
        f"n'est pas en français) :\n{TRAME}\n\n"
        "Consignes :\n"
        f"- Ton {TONES.get(tone, TONES['sobre'])}, dans le registre de la trame. Date du jour : {today_fr()}.\n"
        "- N'invente aucun fait : ni rencontre, conférence ou événement, ni tâche accomplie pendant une "
        "expérience, ni projet, ni chiffre que le profil ou l'offre ne donne pas. Une information manquante "
        "devient une indication entre crochets, par exemple [comment tu as connu le cabinet] ou "
        "[ce que tu as fait pendant ce stage].\n"
        "- Relie les compétences, expériences et projets du profil aux missions de l'offre.\n"
        "- Le permis, l'âge ou la ville ne se mentionnent que s'ils comptent pour ce poste (déplacements, "
        "terrain, proximité).\n"
        "- Le genre de l'étudiant n'est pas connu : évite les accords qui le supposent (« ravi », « conscient », "
        "« élève-ingénieur ») en tournant la phrase autrement.\n"
        "- Reprends les codes du domaine d'études et du poste visé (un stage en soins, en cabinet d'avocats, "
        "en studio de design ou en bureau d'études ne s'écrit pas pareil).\n"
        "- Réponds uniquement par la lettre, en texte brut, sans Markdown ni commentaire."
    )


def write(profile: dict, user, target: dict, tone: str = "sobre", language: str = "fr") -> str:
    """Lettre rédigée par Groq (texte brut). Lève LetterError si Groq échoue."""
    text = groq.chat([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt(profile, user, target, tone, language)}],
                     model=current_app.config.get("STAGES_MODEL", MODEL), temperature=0.4, max_tokens=3000)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # au cas où un peu de Markdown passerait
    text = "\n".join(line.rstrip() for line in text.splitlines())  # espaces de fin (sauts de ligne Markdown)
    if len(text) < 200:
        raise LetterError("réponse de Groq trop courte")
    if len(text) > MAX_LETTER_CHARS:  # SEC-08 : réponse hors consigne, rejetée (lettre type à la place)
        raise LetterError("réponse de Groq anormalement longue")
    return text


def template(profile: dict, user, target: dict) -> str:
    """Lettre type, sans IA : la trame est remplie avec le compte et le profil, le reste est entre crochets."""
    def v(key, fallback):
        return (profile.get(key) or "").strip() or fallback

    c = candidate(user)
    company = target.get("company") or "[nom de l'entreprise]"
    title = target.get("title") or "[poste visé]"
    program = c["program"] or c["level"] or "[ta formation]"
    school = c["school"] or "[ton établissement]"
    if c["study_city"] and c["study_city"] not in school:
        school += f" ({c['study_city']})"
    domain = _lower_first(c["field"]) if c["field"] else "[domaine du stage]"
    goal = v("goal", "[métier visé]")
    exp = v("experiences", "")
    projects = v("projects", "[deux ou trois projets marquants, de cours, personnels ou associatifs]")
    languages = f"Je parle {c['languages']}." if c["languages"] else "[Ton niveau de langue, et sa certification.]"
    license = " Titulaire du permis B, je peux me déplacer facilement." if c["license"] == "permis B" else ""
    city = c["home_city"] or c["study_city"] or "[ville]"
    header = "\n".join(x for x in (c["name"], c["home_city"], c["reach"] or "[téléphone, e-mail]") if x)
    return f"""{header}

{company}
[Adresse de l'entreprise]

À {city}, le {today_fr()}

Objet : Stage - {title}

Madame, Monsieur,

Actuellement en {program} à {school}, je recherche un stage en {domain} {_period(profile)}, afin de \
mettre en pratique mes connaissances et d'acquérir une expérience concrète au sein de votre entreprise. Intégrer \
une équipe de {company} constituerait une étape significative dans mon projet de devenir {goal} et me \
permettrait de découvrir le fonctionnement d'une équipe au sein d'une structure comme la vôtre.

Mon intérêt pour {company} est né [d'une rencontre, d'un projet, de son secteur : ce qui t'a donné envie de \
postuler]. En effet, [ce que ce poste t'apporterait concrètement]. Au-delà de cet aspect, c'est \
[ce qui t'attire tout particulièrement : ses projets, ses valeurs, sa réputation] qui me motive à rejoindre vos \
équipes.

Ma formation m'a permis d'acquérir de solides compétences en {v("skills", "[deux ou trois compétences utiles pour ce poste]")}. \
{f"Tout cela complète mon expérience : {_lower_first(exp).rstrip('.')}." if exp else "[Ajoute une expérience (stage, job, engagement associatif) et ce qu'elle t'a appris.]"}

J'ai pu mobiliser ces compétences à travers plusieurs projets significatifs (détaillés dans mon CV) : {projects}.

{languages}{license} De nature {v("qualities", "[deux qualités]")}, j'apprécie [ce qui te correspond dans le \
travail]. Enfin, j'ai à cœur de faire mes preuves au sein d'une structure stimulante qui saura me lancer des défis.

Ce serait un plaisir de vous présenter plus en détail ma candidature lors d'un entretien ; je vous remercie par \
avance de l'attention portée à ma démarche.

Veuillez agréer, Madame, Monsieur, l'expression de mes salutations distinguées.

{c["name"]}
"""
