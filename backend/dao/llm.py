"""Modèle de langage de l'assistant : Claude (Anthropic) si une clé est configurée, sinon Groq (offre gratuite).

Fournisseur (ASSISTANT_PROVIDER, « auto » par défaut) :
- « anthropic » : Claude Opus 5.5 via le SDK officiel `anthropic`, réponse diffusée au fil de l'écriture
  (streaming), effort « low » pour une conversation rapide (ASSISTANT_CLAUDE_MODEL, ASSISTANT_EFFORT) ;
  résumés de l'historique par Claude Haiku 5.5 (ASSISTANT_CLAUDE_LIGHT_MODEL). Lit les photos directement.
  Clé : ANTHROPIC_API_KEY dans le fichier .env.
- « groq » : openai/gpt-oss-120b (résumés : gpt-oss-20b), API compatible OpenAI appelée avec requests, sans
  streaming. Clé : GROQ_API_KEY dans le fichier .env.

`run_turn` mène un tour complet (le modèle répond ou appelle des outils, jusqu'à la réponse finale) et produit
des événements : ("delta", texte), ("tool", nom de l'outil), ("done", réponse finale). Toute erreur remonte
en LLMError avec un message lisible.
"""
from __future__ import annotations

import base64
import os
import re
import time
from typing import Callable, Iterator

import requests
from flask import current_app

from backend.business.assistant import parse_arguments

from . import groq, web


def groq_key() -> str:
    """Clé Groq (client commun de l'application, dao/groq.py)."""
    return groq.api_key()


def _session():
    return web.secure_session()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
LIGHT_MODEL = "openai/gpt-oss-20b"
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_LIGHT_MODEL = "claude-haiku-5-5"
TRUNCATED = "\n\n*(Réponse coupée car trop longue : demande-moi la suite.)*"

Event = tuple[str, str]


class LLMError(Exception):
    """Réponse impossible (pas de clé, réseau, quota, réponse inexploitable)."""


class RateLimited(LLMError):
    """Limite par minute atteinte ; `wait` : secondes à attendre avant de réessayer."""

    def __init__(self, message: str, wait: float):
        super().__init__(message)
        self.wait = wait


class ToolCallFailed(LLMError):
    """Le modèle a produit un appel d'outil mal formé (refusé par Groq) : on peut réessayer."""


GROQ_TPM = 8000          # offre gratuite : jetons par minute, question ET longueur de réponse demandée comprises
GROQ_MAX_ANSWER = 3000   # longueur de réponse demandée au plus (le raisonnement du modèle en fait partie)
GROQ_MIN_ANSWER = 700
MAX_AUTO_WAIT = 25       # au-delà, on n'attend pas : l'étudiant réessaiera


def groq_answer_budget(messages: list[dict], tools: list[dict] | None) -> int:
    """Longueur de réponse à demander pour que question + réponse tiennent dans la limite par minute
    (environ 3 caractères par jeton pour du français avec du JSON)."""
    import json
    prompt = len(json.dumps(messages, ensure_ascii=False)) + len(json.dumps(tools or [], ensure_ascii=False))
    return max(GROQ_MIN_ANSWER, min(GROQ_MAX_ANSWER, GROQ_TPM - 300 - prompt // 3))


# ---------------------------------------------------------------- choix du fournisseur
def anthropic_key() -> str:
    key = current_app.config.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    path = os.path.join(current_app.instance_path, "anthropic_api_key")
    if not key and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    return "" if key.startswith("gsk_") else key   # une clé Groq rangée au mauvais endroit


def provider() -> str:
    """« anthropic », « groq », ou « » si aucune clé n'est configurée."""
    wanted = current_app.config.get("ASSISTANT_PROVIDER", "auto")
    if wanted in ("anthropic", "groq"):
        return wanted if (anthropic_key() if wanted == "anthropic" else groq_key()) else ""
    if anthropic_key():
        return "anthropic"
    return "groq" if groq_key() else ""


def available() -> bool:
    """Désactivé par défaut pendant les tests (ASSISTANT_AI=True pour l'activer)."""
    return bool(current_app.config.get("ASSISTANT_AI", not current_app.testing)) and bool(provider())


def name() -> str:
    return {"anthropic": "Claude", "groq": "Groq"}.get(provider(), "")


def reads_images() -> bool:
    """Le modèle de conversation lit-il les photos lui-même (sinon : transcription préalable) ?"""
    return provider() == "anthropic"


def run_turn(system: str, history: list[dict], question: dict, tools: list[dict],
             run_tool: Callable[[str, dict], str], max_rounds: int = 4) -> Iterator[Event]:
    """Un tour de conversation. question : {"text": str, "image": {"data": bytes, "mime": str} | None} ;
    tools : [{"name", "description", "parameters"}] ; run_tool(nom, arguments) -> résultat (texte)."""
    if provider() == "anthropic":
        return _claude_turn(system, history, question, tools, run_tool, max_rounds)
    return _groq_turn(system, history, question, tools, run_tool, max_rounds)


def summarize(messages: list[dict]) -> str:
    """Résumé de l'historique par le modèle léger. messages : [{"role": "system"|"user", "content": str}]."""
    if provider() == "anthropic":
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n".join(m["content"] for m in messages if m["role"] != "system")
        try:
            msg = _client().messages.create(
                model=current_app.config.get("ASSISTANT_CLAUDE_LIGHT_MODEL", CLAUDE_LIGHT_MODEL),
                max_tokens=2000, output_config={"effort": "low"}, system=system,
                messages=[{"role": "user", "content": user}])
        except Exception as e:
            raise LLMError("Résumé impossible.") from e
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    return chat(messages, light=True, max_tokens=800)["content"]


# ---------------------------------------------------------------- Claude (Anthropic)
_clients: dict[str, object] = {}


def _client():
    import anthropic
    key = anthropic_key()
    if key not in _clients:
        _clients[key] = anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)
    return _clients[key]


def _claude_error(e: Exception) -> LLMError:
    import anthropic
    current_app.logger.warning("Assistant : erreur Claude (%s)", e)
    if isinstance(e, anthropic.AuthenticationError):
        return LLMError("Clé Anthropic refusée : vérifie ANTHROPIC_API_KEY dans le fichier .env.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return LLMError("Cette clé Anthropic n'a pas accès à ce modèle.")
    if isinstance(e, anthropic.RateLimitError):
        return LLMError("Trop de demandes envoyées à Claude : réessaie dans une minute.")
    if isinstance(e, anthropic.BadRequestError):
        return LLMError(f"Claude a refusé la requête : {getattr(e, 'message', '')[:200]}")
    if isinstance(e, anthropic.APIStatusError):
        return LLMError(f"Claude a renvoyé une erreur ({e.status_code}) : réessaie.")
    if isinstance(e, anthropic.APIConnectionError):
        return LLMError("Claude ne répond pas (connexion impossible).")
    return LLMError("Réponse de Claude impossible.")


def _claude_turn(system, history, question, tools, run_tool, max_rounds) -> Iterator[Event]:
    import anthropic
    cfg = current_app.config
    content = []
    if question.get("image"):
        img = question["image"]
        content.append({"type": "image", "source": {"type": "base64", "media_type": img["mime"],
                                                    "data": base64.standard_b64encode(img["data"]).decode()}})
    content.append({"type": "text", "text": question["text"]})
    messages = [{"role": m["role"], "content": m["content"]} for m in history if m["content"]]
    while messages and messages[0]["role"] != "user":   # une conversation commence par l'étudiant
        messages.pop(0)
    messages.append({"role": "user", "content": content})
    tool_defs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"],
                  "eager_input_streaming": True} for t in tools]
    params = {
        "model": cfg.get("ASSISTANT_CLAUDE_MODEL", CLAUDE_MODEL),
        "max_tokens": cfg.get("ASSISTANT_MAX_TOKENS", 16000),
        # consignes stables mises en cache : moins de temps et de coût à chaque question
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": cfg.get("ASSISTANT_EFFORT", "low")},   # conversation : réponse rapide
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",   # refus d'un filtre de sécurité : relancé sur le modèle de secours conseillé
    }
    if tool_defs:
        params["tools"] = tool_defs
    for _ in range(max_rounds + 1):
        try:
            with _client().beta.messages.stream(messages=messages, **params) as stream:
                for event in stream:
                    if event.type == "text":
                        yield "delta", event.text
                msg = stream.get_final_message()
        except ValueError as e:   # arguments d'outil illisibles
            raise LLMError("L'assistant s'est emmêlé dans sa recherche : reformule ta question.") from e
        except anthropic.AnthropicError as e:
            raise _claude_error(e) from e
        if msg.stop_reason == "refusal":
            raise LLMError("Claude a refusé de répondre à cette question.")
        if msg.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": msg.content})
            continue
        calls = [b for b in msg.content if b.type == "tool_use"]
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not calls:
            yield "done", text + (TRUNCATED if msg.stop_reason == "max_tokens" else "")
            return
        if msg.stop_reason == "max_tokens":   # appel d'outil coupé : on ne l'exécute pas
            yield "done", (text or "Je n'ai pas pu terminer ma recherche.") + TRUNCATED
            return
        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for block in calls:
            yield "tool", block.name
            args = block.input if isinstance(block.input, dict) else {}
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": run_tool(block.name, args)})
        messages.append({"role": "user", "content": results})
    yield "done", "Je n'ai pas réussi à conclure ma recherche : reformule ta question."


# ---------------------------------------------------------------- Groq (offre gratuite)
def _post(body: dict) -> requests.Response:
    try:
        return _session().post(GROQ_URL, timeout=90, headers={"Authorization": f"Bearer {groq_key()}"}, json=body)
    except requests.RequestException as e:
        raise LLMError("Groq ne répond pas (connexion impossible).") from e


def chat(messages: list[dict], tools: list[dict] | None = None, light: bool = False,
         max_tokens: int = 4096) -> dict:
    """Groq : envoie la conversation ; renvoie {"content": str, "tool_calls": [...], "truncated": bool}.

    max_tokens compte aussi le raisonnement du modèle : trop bas, la réponse est coupée."""
    if not groq_key():
        raise LLMError("Aucune clé Groq : colle-la après GROQ_API_KEY= dans le fichier .env.")
    cfg = current_app.config
    body = {
        "model": cfg.get("ASSISTANT_LIGHT_MODEL", LIGHT_MODEL) if light else cfg.get("ASSISTANT_MODEL", MODEL),
        "messages": messages,
        "temperature": 0.3,
        "max_completion_tokens": max_tokens,
    }
    effort = cfg.get("ASSISTANT_REASONING_EFFORT", "low")  # raisonnement court : moins de jetons consommés
    if effort:
        body["reasoning_effort"] = effort
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    r = _post(body)
    if r.status_code == 400 and "reasoning_effort" in r.text:  # modèle qui ne connaît pas ce réglage
        body.pop("reasoning_effort")
        r = _post(body)
    if r.status_code in (413, 429):
        current_app.logger.warning("Assistant : quota Groq (%s)", r.text[:300])
        wait = r.headers.get("retry-after") or ""
        seconds = float(wait) if wait.replace(".", "", 1).isdigit() else 60.0
        raise RateLimited(f"L'assistant est très demandé (limite gratuite de Groq atteinte) : réessaie dans "
                          f"{int(seconds) + 1} s.", seconds)
    if r.status_code == 400 and "tool_use_failed" in r.text:
        current_app.logger.warning("Assistant : appel d'outil mal formé (%s)", r.text[:300])
        raise ToolCallFailed("L'assistant s'est emmêlé dans sa recherche : reformule ta question.")
    if r.status_code != 200:
        current_app.logger.warning("Assistant : erreur Groq %s (%s)", r.status_code, r.text[:300])
        raise LLMError(f"Groq a renvoyé une erreur ({r.status_code}).")
    try:
        choice = r.json()["choices"][0]
        message = choice["message"]
    except (ValueError, KeyError, IndexError) as e:
        raise LLMError("Réponse de Groq illisible.") from e
    content = re.sub(r"<think>.*?</think>", "", message.get("content") or "", flags=re.S).strip()
    return {"content": content, "tool_calls": message.get("tool_calls") or [],
            "truncated": choice.get("finish_reason") == "length"}


def _groq_turn(system, history, question, tools, run_tool, max_rounds) -> Iterator[Event]:
    messages = [{"role": "system", "content": system}] + list(history) + [{"role": "user", "content": question["text"]}]
    schemas = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                 "parameters": t["parameters"]}} for t in tools]
    for round_ in range(max_rounds + 1):
        offered = schemas if (schemas and round_ < max_rounds) else None
        retried_tool = False
        for attempt in range(3):
            try:
                reply = chat(messages, tools=offered, max_tokens=groq_answer_budget(messages, offered))
                break
            except RateLimited as e:   # limite par minute : on attend quelques secondes et on reprend
                if attempt == 2 or e.wait > MAX_AUTO_WAIT:
                    raise
                yield "status", f"Limite gratuite de Groq : je reprends dans {int(e.wait) + 1} s…"
                time.sleep(e.wait + 0.5)
            except ToolCallFailed:     # appel d'outil mal formé : une seconde chance
                if retried_tool or attempt == 2:
                    raise
                retried_tool = True
        if not reply["tool_calls"]:
            text = reply["content"] + (TRUNCATED if reply.get("truncated") else "")
            yield "delta", text
            yield "done", text
            return
        messages.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]})
        for call in reply["tool_calls"]:
            fn = call.get("function") or {}
            yield "tool", fn.get("name", "")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": run_tool(fn.get("name", ""), parse_arguments(fn.get("arguments")))})
    yield "done", "Je n'ai pas réussi à conclure ma recherche : reformule ta question."
