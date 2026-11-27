"""Lecture de photos par un modèle de vision de Groq (API compatible OpenAI, appelée avec requests).

Sert à lire une photo d'emploi du temps (agenda) ou de notes de cours (supports). La clé n'est jamais
dans le code ni dans git : GROQ_API_KEY dans le fichier .env (voir .env.example) ; restent acceptés,
pour les installations existantes, les fichiers instance/groq_api_key et instance/anthropic_api_key (clé « gsk_… »).
Toute erreur remonte en VisionError.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import ssl

import requests
from flask import current_app
from requests.adapters import HTTPAdapter

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "qwen/qwen3.8-27b"  # seul modèle de Groq qui lit les images (modifiable par VISION_MODEL)
MAX_IMAGE_BYTES = 3 * 1024 * 1024  # Groq refuse les images de plus de 4 Mo une fois en base64
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}


class VisionError(Exception):
    """Lecture impossible (pas de clé, réseau, quota, réponse inexploitable)."""


def _read_key(config_name: str, filename: str) -> str:
    key = current_app.config.get(config_name) or os.environ.get(config_name, "")
    path = os.path.join(current_app.instance_path, filename)
    if not key and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    return key


def api_key() -> str:
    key = _read_key("GROQ_API_KEY", "groq_api_key")
    if not key:
        other = _read_key("ANTHROPIC_API_KEY", "anthropic_api_key")
        key = other if other.startswith("gsk_") else ""
    return key


def available() -> bool:
    """Désactivé par défaut pendant les tests (VISION_AI=True pour l'activer)."""
    return bool(current_app.config.get("VISION_AI", not current_app.testing)) and bool(api_key())


def _shrink(data: bytes) -> tuple[bytes, str]:
    """Image en JPEG d'au plus ~3 Mo et 2 000 px de côté (photos de téléphone trop lourdes, HEIC…)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, "image/jpeg"
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:  # format illisible
        raise VisionError("Image illisible : envoie une photo JPEG ou PNG.") from e
    img.thumbnail((2000, 2000))
    for quality in (85, 70, 55):
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality)
        if out.tell() <= MAX_IMAGE_BYTES:
            return out.getvalue(), "image/jpeg"
    img.thumbnail((1200, 1200))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=60)
    return out.getvalue(), "image/jpeg"


def shrink(data: bytes) -> tuple[bytes, str]:
    """Photo prête à envoyer à un modèle : (JPEG d'au plus ~3 Mo et 2 000 px, type MIME)."""
    return _shrink(data)


def _session() -> requests.Session:
    """Certificats vérifiés avec le magasin du système (antivirus qui inspectent le TLS)."""
    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = ssl.create_default_context()
            return super().init_poolmanager(*args, **kwargs)

    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


def ask(image: bytes, prompt: str, as_json: bool = True, max_tokens: int = 4000):
    """Envoie la photo et la consigne ; renvoie l'objet JSON (as_json) ou le texte de la réponse."""
    if not api_key():
        raise VisionError("Aucune clé Groq : colle-la après GROQ_API_KEY= dans le fichier .env.")
    data, mime = _shrink(image)
    body = {
        "model": current_app.config.get("VISION_MODEL", MODEL),
        "temperature": 0.1,
        "max_completion_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}},
        ]}],
    }
    if as_json:
        body["response_format"] = {"type": "json_object"}
    try:
        r = _session().post(URL, timeout=120, headers={"Authorization": f"Bearer {api_key()}"}, json=body)
    except requests.RequestException as e:
        raise VisionError("Groq ne répond pas (connexion impossible).") from e
    if r.status_code in (413, 429):
        current_app.logger.warning("Vision : quota Groq (%s)", r.text[:300])
        raise VisionError("Quota de Groq atteint : réessaie dans une minute.")
    if r.status_code != 200:
        current_app.logger.warning("Vision : erreur Groq %s (%s)", r.status_code, r.text[:300])
        raise VisionError(f"Groq a renvoyé une erreur ({r.status_code}).")
    try:
        content = r.json()["choices"][0]["message"]["content"] or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)  # raisonnement de Qwen, s'il fuit
        return json.loads(content) if as_json else content.strip()
    except (ValueError, KeyError, IndexError) as e:
        raise VisionError("Réponse de Groq illisible.") from e
