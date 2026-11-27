"""API JSON des suggestions en direct (utilisées par les formulaires)."""
from flask import Blueprint, g, jsonify, request

from backend.core.features import Feature
from backend.core.security import login_required
from backend.services import suggestion_service

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/suggestions/matieres")
@login_required
def subject_suggestions():
    return jsonify(suggestion_service.subjects_from_agenda(g.user["id"], request.args.get("q", "")[:100]))


@bp.route("/agenda/rattachement")
@login_required
def slot_link():
    return jsonify(suggestion_service.slot_link(g.user["id"], request.args.get("titre", "").strip()[:200],
                                                request.args.get("type", "cours")))


@bp.route("/ecoles")
def schools():
    """Accessible sans compte : sert aussi à l'inscription."""
    return jsonify(suggestion_service.schools(request.args.get("q", "")[:80], request.args.get("niveau")))


feature = Feature("suggestions", blueprints=[bp])
