"""Assistant (chatbot) : conversation avec historique, appels d'outils, réponses enregistrées.

Déroulé d'une question :
1. on reprend la conversation en cours : résumé des anciens échanges + derniers messages tels quels ;
2. le modèle répond, ou appelle des outils (planning, notes…) dont les résultats lui sont renvoyés ;
3. la question et la réponse finale (avec ses graphiques) sont enregistrées.

Aucune écriture en base avant la fin : la base n'est pas bloquée pendant que Groq répond.

Historique éphémère : une conversation appartient à la session de connexion. Il est effacé à la déconnexion,
et à la connexion suivante quand le navigateur a été fermé (le cookie de session disparaît avec lui).
Un nouveau document (photo ou PDF) envoyé alors que la conversation en contient déjà un repart d'une
conversation vide : l'assistant ne mélange pas deux documents.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import tempfile
from datetime import date

from flask import current_app, session
from werkzeug.datastructures import FileStorage

from backend.business import accounts, extractor, retrieval
from backend.business import assistant as rules
from backend.dao import actions as actions_dao
from backend.dao import chat as dao
from backend.dao import llm, vision
from backend.dao import subjects as subjects_dao
from backend.dao.db import commit
from backend.services.common import owned

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_ARGUMENT_CHARS = 1000   # ex. la description du stage recherché
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif"}

NOT_CONFIGURED = ("L'assistant n'est pas encore branché : colle une clé Anthropic (ANTHROPIC_API_KEY=) ou Groq "
                  "(GROQ_API_KEY=, gratuite sur console.groq.com) dans le fichier .env, puis relance le serveur.")


def _session_key() -> str:
    """Identifiant de la session de connexion (dans le cookie de session, effacé à la déconnexion)."""
    if "chat_key" not in session:
        session["chat_key"] = secrets.token_hex(12)
    return session["chat_key"]


def prepare_stream(file=None):
    """À appeler avant de diffuser une réponse :
    - le cookie de session part avec les en-têtes : l'identifiant de session doit exister avant le premier événement ;
    - le serveur ferme les fichiers envoyés dès que la route a rendu sa réponse : la pièce jointe est copiée en
      mémoire maintenant, sinon sa lecture pendant la diffusion échoue (« I/O operation on closed file »).
    Renvoie la pièce jointe utilisable pendant la diffusion (ou None)."""
    _session_key()
    if file is None or not file.filename:
        return None
    return FileStorage(stream=io.BytesIO(file.read()), filename=file.filename, content_type=file.mimetype)


IDLE_MINUTES = 120   # conversation inactive depuis 2 h : considérée comme terminée


def _current(user):
    """Conversation en cours ; efface au passage celles des sessions terminées ou inactives."""
    key = _session_key()
    dao.purge_other_sessions(user["id"], key)
    dao.purge_idle(user["id"], current_app.config.get("ASSISTANT_IDLE_MINUTES", IDLE_MINUTES))
    return dao.current(user["id"], key)


def on_logout(user_id, **_):
    dao.delete_all(user_id)


def on_startup(app) -> None:
    """Démarrage du serveur (= réouverture de l'application) : l'historique de la session précédente est effacé,
    même si le navigateur a gardé son cookie de session."""
    with app.app_context():
        dao.delete_everyone()
        commit()


def _with_status(user, actions: list[dict]) -> list[dict]:
    """Propositions avec leur état actuel (en attente, faite, annulée) : les boutons d'une proposition
    déjà traitée ne sont plus affichés."""
    states = actions_dao.statuses(user["id"], [a["id"] for a in actions])
    return [a | states.get(a["id"], {"status": "expired", "choice": ""}) for a in actions]


def _public(m: dict, user=None) -> dict:
    a = m["meta"].get("attachment")
    actions = m["meta"].get("actions", [])
    return {"id": m["id"], "role": m["role"], "content": m["content"], "charts": m["meta"].get("charts", []),
            "sources": m["meta"].get("sources", []), "links": m["meta"].get("links", []),
            "actions": _with_status(user, actions) if (actions and user) else actions,
            "attachment": {"name": a["name"], "kind": a["kind"]} if a else None}


def _pending(user, action_id: int):
    """(proposition, son Action) ou (None, message d'erreur). Seul son auteur peut la traiter, une seule fois."""
    row = owned(actions_dao.get_owned(action_id, user["id"]))  # SEC-02 : 404 si elle n'est pas à l'utilisateur
    handler = current_app.extensions.get("assistant_actions", {}).get(row["kind"])
    if handler is None:
        return None, "Proposition introuvable."
    if row["status"] != "pending":
        return None, "Cette proposition a déjà été traitée."
    return handler, None


def apply_action(user, action_id: int, choice: str) -> dict:
    """L'étudiant valide une option : elle est appliquée, et la confirmation s'ajoute à la conversation."""
    handler, error = _pending(user, action_id)
    if error:
        return {"error": error}
    text, error = handler.apply(user, action_id, choice)
    if error:
        return {"error": error}
    return {"message": _record(user, text)}


def cancel_action(user, action_id: int) -> dict:
    handler, error = _pending(user, action_id)
    if error:
        return {"error": error}
    actions_dao.close(action_id, user["id"], "cancelled")
    return {"message": _record(user, handler.cancelled)}


def _record(user, text: str) -> dict:
    """Message de l'assistant ajouté à la conversation en cours (hors réponse du modèle)."""
    conv = _current(user)
    conv_id = conv["id"] if conv else dao.create(user["id"], _session_key())
    message_id = dao.add_message(conv_id, "assistant", text)
    dao.touch(conv_id, user["id"])
    return {"id": message_id, "role": "assistant", "content": text, "charts": [], "sources": [], "links": [],
            "actions": [],
            "attachment": None}


def read_attachment(file) -> tuple[dict | None, str | None]:
    """Lit une photo ou un PDF (texte extrait).

    Photo : envoyée telle quelle à Claude, qui la lit lui-même ; avec Groq, recopiée d'abord par son modèle
    de vision. Renvoie ({name, kind, text, image?}, None) ou (None, message d'erreur). Rien n'est conservé.
    """
    name = os.path.basename(file.filename or "document")[:120]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    data = file.read()
    if not data:
        return None, f"« {name} » est vide."
    if ext in IMAGE_EXTENSIONS or (file.mimetype or "") in vision.IMAGE_TYPES:
        if llm.reads_images():
            try:
                image, mime = vision.shrink(data)
            except vision.VisionError as e:
                return None, str(e)
            return {"name": name, "kind": "image", "text": "", "image": {"data": image, "mime": mime}}, None
        if not vision.available():
            return None, "La lecture de photos n'est pas disponible : il manque la clé Groq (fichier .env)."
        try:
            text = vision.ask(data, rules.VISION_PROMPT, as_json=False, max_tokens=2500)
        except vision.VisionError as e:
            return None, f"Photo non lue : {e}"
        if len(text) < 10 or text.strip().upper().startswith("ILLISIBLE"):
            return None, "Je n'arrive pas à lire cette photo : reprends-la plus nette, bien éclairée et de face."
        return {"name": name, "kind": "image", "text": text}, None
    if ext == "pdf" or file.mimetype == "application/pdf":
        if len(data) > MAX_PDF_BYTES:
            return None, "PDF trop lourd (15 Mo au plus)."
        fd, path = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            text, _ = extractor.extract(path)
        finally:
            os.remove(path)
        if len((text or "").strip()) < 40:
            return None, ("Ce PDF ne contient pas de texte lisible (document scanné ?) : "
                          "envoie plutôt une photo des pages.")
        return {"name": name, "kind": "pdf", "text": text}, None
    return None, "Formats acceptés : une photo (JPEG, PNG, HEIC…) ou un PDF."


def conversation(user) -> dict:
    conv = _current(user)
    messages = dao.messages(conv["id"], user["id"]) if conv else []
    return {"available": llm.available(), "notice": "",
            "provider": {"anthropic": "Anthropic (Claude)", "groq": "Groq"}.get(llm.provider(), ""),
            "messages": [_public(m, user) for m in messages]}


def new_conversation(user) -> None:
    """Nouvelle conversation : la conversation en cours est effacée (l'historique n'est pas archivé)."""
    conv = _current(user)
    if conv is not None:
        dao.delete(conv["id"], user["id"])


def _page(user, page: str, subject_id) -> str:
    if str(subject_id or "").isdigit():
        s = subjects_dao.get_owned(int(subject_id), user["id"])
        if s:
            return f"la page de sa matière « {s['name']} »"
    page = " ".join((page or "").split())[:80]
    return f"la page « {page} »" if page else ""


def _profile(user) -> dict:
    return {"username": user["username"], "level_label": accounts.LEVELS.get(user["level"] or "", ""),
            "school": user["school"] or "", "subjects": [s["name"] for s in subjects_dao.list_for_user(user["id"])]}


def _run_tool(user, tools: dict, name: str, arguments: dict,
              source_offset: int = 0) -> tuple[str, dict | None, list[dict], dict | None, list[dict]]:
    """(texte pour le modèle, graphique, sources, modification proposée à valider par l'étudiant, liens)."""
    tool = tools.get(name)
    if tool is None:
        return json.dumps({"erreur": f"Outil inconnu : {name}."}, ensure_ascii=False), None, [], None, []
    allowed = tool.parameters.get("properties", {})
    args = {k: str(v)[:MAX_ARGUMENT_CHARS] for k, v in (arguments or {}).items()   # null = non renseigné
            if k in allowed and v is not None and str(v).strip()}
    try:
        result = tool.run(user, **args)
    except Exception:  # un outil en panne ne doit pas couper la conversation
        current_app.logger.exception("Assistant : l'outil %s a échoué", tool.name)
        result = {"erreur": "Cette information est momentanément indisponible."}
    result = dict(result or {})
    action = result.pop("action", None)   # proposition affichée avec des boutons, pas envoyée au modèle
    if action:
        result["boutons"] = "affichés sous ta réponse : l'étudiant choisit lui-même une option"
    links = result.pop("links", None) or []   # liens cliquables affichés tels quels sous la réponse
    if links:
        result["liens"] = "affichés sous ta réponse (" + ", ".join(g["title"] for g in links) + \
                          ") : ne recopie pas les adresses, invite l'étudiant à cliquer"
    return (*rules.split_result(result, source_offset), action, links)


def send(user, text: str, page: str = "", subject_id=None, file=None) -> dict:
    """Pose une question et attend la réponse complète : {"message": …, "question": …, "reset": …}
    ou {"error": message lisible}."""
    for event in send_events(user, text, page, subject_id, file):
        if event["type"] == "error":
            return {"error": event["error"]}
        if event["type"] == "done":
            return {k: v for k, v in event.items() if k != "type"}
    return {"error": "Réponse impossible."}


def send_events(user, text: str, page: str = "", subject_id=None, file=None):
    """Pose une question (avec une photo ou un PDF joint, facultatif) et produit des événements :
    {"type": "status", "text"} (ce que fait l'assistant), {"type": "delta", "text"} (réponse en cours
    d'écriture), puis {"type": "done", "message", "question", "reset"} ou {"type": "error", "error"}."""
    text = rules.clean_user_text(text)
    has_file = file is not None and bool(file.filename)
    if not text and not has_file:
        yield {"type": "error", "error": "Écris ta question."}
        return
    if not llm.available():
        yield {"type": "error", "error": NOT_CONFIGURED}
        return
    attachment = None
    if has_file:
        yield {"type": "status", "text": "Je lis ton document…"}
        attachment, error = read_attachment(file)
        if error:
            yield {"type": "error", "error": error}
            return
        text = text or rules.DEFAULT_ATTACHMENT_QUESTION
    limit = rules.attachment_limit(llm.provider())
    question = text + (rules.attachment_block(attachment["name"], attachment["kind"], attachment["text"], limit)
                       if attachment else "")
    uid = user["id"]
    conv = _current(user)
    history = dao.messages(conv["id"], uid) if conv else []
    summary, upto = (conv["summary"], conv["summarized_upto"]) if conv else ("", 0)
    # Deuxième document : on repart d'une conversation vide (l'ancienne est effacée à l'enregistrement)
    reset = bool(attachment) and any(m["meta"].get("attachment") for m in history)
    if reset:
        history, summary, upto = [], "", 0
    old, recent = rules.split_history(history, upto)
    new_summary = None
    if old:
        try:
            new_summary = llm.summarize(rules.summary_request(summary, old))
        except llm.LLMError:
            new_summary = None  # le résumé sera retenté à la prochaine question
        if new_summary:
            summary = new_summary

    system = rules.system_prompt(_profile(user), date.today(), _page(user, page, subject_id))
    if summary:
        system += "\n\n" + rules.summary_block(summary)["content"]
    history_messages = [{"role": m["role"], "content": rules.with_attachment(m)} for m in recent]
    tools = current_app.extensions.get("tools", {})
    tool_list = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools.values()]
    charts, sources, actions, links = [], [], [], []

    def run_tool(name: str, arguments: dict) -> str:
        content, chart, found, action, found_links = _run_tool(user, tools, name, arguments, len(sources))
        if chart:
            charts.append(chart)
        if action:
            actions.append(action)
        sources.extend(found)
        links.extend(g for g in found_links if g not in links)
        return content

    answer = ""
    try:
        for kind, value in llm.run_turn(system, history_messages,
                                        {"text": question, "image": (attachment or {}).get("image")},
                                        tool_list, run_tool, rules.MAX_TOOL_ROUNDS):
            if kind == "delta":
                yield {"type": "delta", "text": value}
            elif kind == "tool":
                yield {"type": "status", "text": rules.TOOL_STATUS.get(value, "Je cherche…")}
            elif kind == "status":   # ex. attente imposée par la limite gratuite de Groq
                yield {"type": "status", "text": value}
            elif kind == "done":
                answer = value
    except llm.LLMError as e:
        yield {"type": "error", "error": str(e)}
        return
    answer = answer or "Je n'ai pas réussi à répondre : reformule ta question."
    if sources:  # citations vérifiées : un numéro sans source est retiré
        answer, sources = retrieval.filter_citations(answer, sources)

    # Enregistrement (une seule transaction, à la fin)
    if conv is not None and reset:
        dao.delete(conv["id"], uid)
        conv = None
    if conv is None:
        conv_id = dao.create(uid, _session_key())
    else:
        conv_id = conv["id"]
        if new_summary:
            dao.set_summary(conv_id, uid, new_summary, old[-1]["id"])
    meta = {"attachment": {"name": attachment["name"], "kind": attachment["kind"],
                           "excerpt": attachment["text"][:rules.HISTORY_ATTACHMENT_CHARS]}} if attachment else {}
    user_id_msg = dao.add_message(conv_id, "user", text, meta)
    answer_id = dao.add_message(conv_id, "assistant", answer,
                                {"charts": charts, "sources": sources, "actions": actions, "links": links})
    dao.touch(conv_id, uid, title=rules.title_from(text))
    commit()  # réponse diffusée : la requête se termine après l'envoi du dernier événement
    yield {"type": "done", "reset": reset,
           "message": {"id": answer_id, "role": "assistant", "content": answer, "charts": charts,
                       "sources": sources, "links": links, "actions": _with_status(user, actions),
                       "attachment": None},
           "question": {"id": user_id_msg, "role": "user", "content": text, "charts": [],
                        "attachment": {"name": attachment["name"], "kind": attachment["kind"]} if attachment else None}}
