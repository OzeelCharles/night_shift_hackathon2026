"""Assistant : bulle de discussion flottante, présente sur toutes les pages d'un utilisateur connecté."""
import json

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from backend.core import events
from backend.core.features import Feature
from backend.core.security import login_required
from backend.core.tools import Action
from backend.services import assistant_service, rag_service, reschedule_service
from backend.services.assistant_tools import TOOLS

bp = Blueprint("assistant", __name__, url_prefix="/assistant")


@bp.route("/conversation")
@login_required
def conversation():
    return jsonify(assistant_service.conversation(g.user))


@bp.route("/messages", methods=("POST",))
@login_required
def send():
    """JSON {text, page, subject_id}, ou formulaire multipart avec en plus un fichier « file » (photo ou PDF)."""
    data = request.get_json(silent=True) or request.form
    result = assistant_service.send(g.user, str(data.get("text", "")), str(data.get("page", "")),
                                    data.get("subject_id"), request.files.get("file"))
    return jsonify(result), (200 if "message" in result else 422)


@bp.route("/messages/flux", methods=("POST",))
@login_required
def send_stream():
    """Même question, réponse diffusée au fil de l'écriture (Server-Sent Events : une ligne « data: {json} »
    par événement : status, delta, done ou error)."""
    data = request.get_json(silent=True) or request.form
    upload = assistant_service.prepare_stream(request.files.get("file"))   # avant la diffusion (voir la fonction)
    events_ = assistant_service.send_events(g.user, str(data.get("text", "")), str(data.get("page", "")),
                                            data.get("subject_id"), upload)

    def sse():
        for event in events_:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(sse()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/actions/<int:action_id>", methods=("POST",))
@login_required
def apply_action(action_id):
    """L'étudiant valide une option proposée par l'assistant (ex. rattraper une séance) : JSON {choice}."""
    data = request.get_json(silent=True) or request.form
    result = assistant_service.apply_action(g.user, action_id, str(data.get("choice", "")))
    return jsonify(result), (200 if "message" in result else 422)


@bp.route("/actions/<int:action_id>/annuler", methods=("POST",))
@login_required
def cancel_action(action_id):
    result = assistant_service.cancel_action(g.user, action_id)
    return jsonify(result), (200 if "message" in result else 422)


@bp.route("/nouvelle", methods=("POST",))
@login_required
def new():
    assistant_service.new_conversation(g.user)
    return jsonify(assistant_service.conversation(g.user))


def setup(app) -> None:
    assistant_service.on_startup(app)   # historique de la session précédente effacé
    rag_service.index_all_async(app)    # supports déjà déposés indexés en arrière-plan


feature = Feature("assistant", blueprints=[bp], widgets=["assistant/bubble.html"], tools=TOOLS, setup=setup,
                  actions=[Action(reschedule_service.KIND, reschedule_service.apply,
                                  "D'accord, je ne change rien à ton planning.")],
                  subscriptions=[(events.USER_LOGGED_OUT, assistant_service.on_logout),  # historique éphémère
                                 (events.DOCUMENTS_ADDED, rag_service.index_async)])     # supports indexés (RAG)
