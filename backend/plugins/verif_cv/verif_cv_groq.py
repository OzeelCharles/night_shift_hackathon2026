"""Conseils de rédaction du CV par Groq (option).

Rien n'est envoyé sans un clic explicite de l'étudiant sur « Demander des conseils de rédaction à l'IA ».
Option de configuration VERIF_CV_AI (désactivée par défaut pendant les tests, comme STAGES_AI) ; sans clé
d'API, la fonction est simplement indisponible et tout le reste marche.
"""
from __future__ import annotations

import re

from flask import current_app

from backend.dao import groq

MODEL = "openai/gpt-oss-120b"
AdviceError = groq.GroqError
SYSTEM = (
    "Tu es conseiller en insertion professionnelle. Tu aides un étudiant à rendre son CV lisible par les "
    "logiciels de tri des candidatures (ATS) et mieux accordé à une offre de stage. Tu n'inventes jamais "
    "d'expérience, de diplôme, de compétence, de chiffre ni de date : ce qui manque est laissé entre crochets, "
    "par exemple [outil utilisé] ou [résultat obtenu], pour que l'étudiant le complète ou le retire."
)


def available() -> bool:
    return groq.enabled("VERIF_CV_AI")


def prompt(cv_text: str, offer: dict, missing: list[str]) -> str:
    target = " — ".join(x for x in (offer.get("title"), offer.get("company")) if x)
    return (
        f"Offre visée : {target}\n\n"
        f"Texte de l'offre :\n{' '.join((offer.get('description') or '').split())[:5000] or '(non fourni)'}\n\n"
        f"Mots-clés de l'offre absents du CV : {', '.join(missing) or 'aucun'}\n\n"
        f"Texte extrait du CV :\n{cv_text.strip()[:8000]}\n\n"
        "Consignes :\n"
        "- Propose d'abord, si besoin, des titres de rubriques standard (Formation, Expériences, Compétences, "
        "Langues) à la place de titres originaux.\n"
        "- Puis 3 à 6 phrases ou puces prêtes à reprendre, qui intègrent les mots-clés manquants seulement là où "
        "le CV montre déjà une expérience proche. Sinon, écris la phrase avec des crochets : [si tu as utilisé "
        "Power BI : …].\n"
        "- Rappelle en une ligne de n'ajouter que ce que l'étudiant sait vraiment faire.\n"
        "- Réponds en français, en texte brut, sans Markdown, 250 mots au plus."
    )


def advise(cv_text: str, offer: dict, missing: list[str]) -> str:
    """Conseils rédigés par Groq (texte brut). Lève AdviceError si Groq échoue."""
    text = groq.chat([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt(cv_text, offer, missing)}],
                     model=current_app.config.get("VERIF_CV_MODEL", MODEL), temperature=0.3, max_tokens=1500)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", str(text or ""))
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(text) < 40:
        raise AdviceError("réponse de Groq trop courte")
    return text
