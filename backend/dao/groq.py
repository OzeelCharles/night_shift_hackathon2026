"""Groq (API compatible OpenAI) : le seul point d'appel de l'application, pour tous ses usages.

Lecture de photos (agenda, supports), lettres de motivation (stages), QCM et exercices (révision) passent
par chat(). La clé n'est jamais dans le code ni dans git : GROQ_API_KEY (variable d'environnement ou
fichier instance/groq_api_key), ou une clé « gsk_… » rangée dans instance/anthropic_api_key.
Toute erreur remonte en GroqError, avec un message prêt à afficher.
"""
from __future__ import annotations

import json
import os
import re

import requests
from flask import current_app

from . import web

URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqError(Exception):
    """Appel impossible (pas de clé, réseau, quota, réponse inexploitable)."""


def read_key(config_name: str, filename: str) -> str:
    """Clé lue dans la configuration, puis la variable d'environnement, puis instance/<filename>."""
    key = current_app.config.get(config_name) or os.environ.get(config_name, "")
    path = os.path.join(current_app.instance_path, filename)
    if not key and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    return key


def api_key() -> str:
    key = read_key("GROQ_API_KEY", "groq_api_key")
    if not key:
        other = read_key("ANTHROPIC_API_KEY", "anthropic_api_key")
        key = other if other.startswith("gsk_") else ""
    return key


def enabled(flag: str) -> bool:
    """Usage activé (option de configuration `flag`, désactivée par défaut pendant les tests) et clé présente."""
    return bool(current_app.config.get(flag, not current_app.testing)) and bool(api_key())


def chat(messages: list[dict], *, model: str, temperature: float = 0.4, max_tokens: int = 3000,
         as_json: bool = False):
    """Envoie la conversation ; renvoie le texte de la réponse, ou l'objet JSON si as_json."""
    key = api_key()
    if not key:
        raise GroqError("Aucune clé Groq : range-la dans instance/anthropic_api_key ou instance/groq_api_key.")
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": messages}
    if as_json:
        body["response_format"] = {"type": "json_object"}
    try:
        r = web.secure_session().post(URL, timeout=120, headers={"Authorization": f"Bearer {key}"}, json=body)
    except requests.RequestException as e:
        raise GroqError("Groq ne répond pas (connexion impossible).") from e
    if r.status_code in (413, 429):
        current_app.logger.warning("Groq : quota atteint (%s)", r.text[:300])
        raise GroqError("Quota de Groq atteint : réessaie dans une minute.")
    if r.status_code == 400 and "json_validate_failed" in r.text:
        raise GroqError("Groq a renvoyé un JSON invalide (réponse sans doute trop longue) : réessaie.")
    if r.status_code != 200:
        current_app.logger.warning("Groq : erreur %s (%s)", r.status_code, r.text[:300])
        raise GroqError(f"Groq a renvoyé une erreur ({r.status_code}).")
    try:
        content = r.json()["choices"][0]["message"]["content"] or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()  # raisonnement qui fuit
        return json.loads(content) if as_json else content
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise GroqError("Réponse de Groq illisible.") from e
