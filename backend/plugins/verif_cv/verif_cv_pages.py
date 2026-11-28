"""Routes du vérificateur de CV (espace « Stages »)."""
from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from backend.controllers import flash_report
from backend.core.security import login_required

from . import verif_cv_service as service

bp = Blueprint("verif_cv", __name__, template_folder="templates")


def _offer_arg(value):
    value = str(value or "").strip()
    return int(value) if value.isdigit() else None


@bp.route("/stages/cv", methods=("GET", "POST"))
@login_required
def page():
    if request.method == "POST":
        flash_report(service.upload(g.user, request.files.get("cv")))
        return redirect(url_for("verif_cv.page") + "#lisibilite")
    return render_template("verif_cv/verif_cv_page.html", **service.page(g.user, _offer_arg(request.args.get("offre"))))


@bp.route("/stages/cv/supprimer", methods=("POST",))
@login_required
def delete():
    flash_report(service.delete(g.user))
    return redirect(url_for("verif_cv.page"))


@bp.route("/stages/cv/conseils-ia", methods=("POST",))
@login_required
def advice():
    offer_id = _offer_arg(request.form.get("offer_id"))
    if offer_id is None:
        abort(404)
    text, report = service.ai_advice(g.user, offer_id)
    flash_report(report)
    if text is None:
        return redirect(url_for("verif_cv.page", offre=offer_id) + "#offre")
    return render_template("verif_cv/verif_cv_page.html", **dict(service.page(g.user, offer_id), advice=text))
