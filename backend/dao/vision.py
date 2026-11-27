"""Lecture de photos par le modèle de vision de Groq : emploi du temps (agenda), notes de cours (supports).

Sert à lire une photo d'emploi du temps (agenda) ou de notes de cours (supports). La clé n'est jamais
dans le code ni dans git : GROQ_API_KEY dans le fichier .env (voir .env.example) ; restent acceptés,
pour les installations existantes, les fichiers instance/groq_api_key et instance/anthropic_api_key (clé « gsk_… »).
Toute erreur remonte en VisionError.
"""
from __future__ import annotations

import base64
import io

from flask import current_app

from . import groq

MODEL = "qwen/qwen3.8-27b"  # seul modèle de Groq qui lit les images (modifiable par VISION_MODEL)
MAX_IMAGE_BYTES = 3 * 1024 * 1024

VisionError = groq.GroqError


def available() -> bool:
    """Désactivé par défaut pendant les tests (VISION_AI=True pour l'activer)."""
    return groq.enabled("VISION_AI")


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
    image_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return groq.chat([{"role": "user", "content": [{"type": "text", "text": prompt},
                                                   {"type": "image_url", "image_url": {"url": image_url}}]}],
                     model=current_app.config.get("VISION_MODEL", MODEL), temperature=0.1,
                     max_tokens=max_tokens, as_json=as_json)
