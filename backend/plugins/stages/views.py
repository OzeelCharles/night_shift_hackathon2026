"""Routes de la recherche de stage."""
from flask import Blueprint, Response, g, redirect, render_template, request, url_for

from backend.controllers import flash_report
from backend.core.security import login_required

from . import service

bp = Blueprint("stages", __name__, template_folder="templates")


def _back(anchor=""):
    return redirect(url_for("stages.index") + anchor)


@bp.route("/stages")
@login_required
def index():
    return render_template("stages/index.html", **service.page(g.user))


@bp.route("/stages/profil", methods=("POST",))
@login_required
def profile():
    flash_report(service.save_profile(g.user, request.form))
    return _back("#profil")


@bp.route("/stages/postes", methods=("POST",))
@login_required
def add_wish():
    flash_report(service.add_wish(g.user, request.form))
    return _back("#postes")


@bp.route("/stages/postes/<int:wish_id>/supprimer", methods=("POST",))
@login_required
def delete_wish(wish_id):
    flash_report(service.delete_wish(g.user, wish_id))
    return _back("#postes")


@bp.route("/stages/offres", methods=("POST",))
@login_required
def add_offer():
    flash_report(service.add_offer(g.user, request.form))
    return _back("#offres")


@bp.route("/stages/offres/<int:offer_id>/statut", methods=("POST",))
@login_required
def offer_status(offer_id):
    flash_report(service.set_status(g.user, offer_id, request.form.get("status", "")))
    return _back("#offres")


@bp.route("/stages/offres/<int:offer_id>/supprimer", methods=("POST",))
@login_required
def delete_offer(offer_id):
    flash_report(service.delete_offer(g.user, offer_id))
    return _back("#offres")


@bp.route("/stages/lettres", methods=("POST",))
@login_required
def generate():
    letter_id, report = service.generate_letter(g.user, request.form)
    flash_report(report)
    return redirect(url_for("stages.letter", letter_id=letter_id)) if letter_id else _back("#lettre")


@bp.route("/stages/lettres/<int:letter_id>", methods=("GET", "POST"))
@login_required
def letter(letter_id):
    if request.method == "POST":
        flash_report(service.save_letter(g.user, letter_id, request.form.get("content")))
        return redirect(url_for("stages.letter", letter_id=letter_id))
    return render_template("stages/letter.html", letter=service.letter(g.user, letter_id),
                           tones=service.letters.TONES, languages=service.letters.LANGUAGES)


@bp.route("/stages/lettres/<int:letter_id>/telecharger")
@login_required
def download(letter_id):
    lt = service.letter(g.user, letter_id)
    name = "lettre-motivation-" + "".join(c if c.isalnum() else "-" for c in (lt["company"] or lt["title"]))[:50]
    return Response(lt["content"], mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name.strip("-").lower()}.txt"'})


@bp.route("/stages/lettres/<int:letter_id>/supprimer", methods=("POST",))
@login_required
def delete_letter(letter_id):
    flash_report(service.delete_letter(g.user, letter_id))
    return _back("#lettres")
