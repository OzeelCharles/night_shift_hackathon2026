"""Espace « Campus » : affluence de la bibliothèque de l'école et des restaurants Crous alentour, menus du jour."""
from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required, safe_next
from backend.services import campus_service as service
from backend.services.campus_tools import CAMPUS_TOOLS

from . import flash_report

bp = Blueprint("campus", __name__, url_prefix="/campus")


@bp.route("/")
@login_required
def index():
    return redirect(url_for("campus.library"))


@bp.route("/bibliotheque")
@login_required
def library():
    return render_template("campus/library.html", **service.bu_page(g.user))


@bp.route("/restos")
@login_required
def restos():
    return render_template("campus/restos.html", **service.restos_page(g.user, request.args.get("ville", "")))


@bp.route("/restos/apercu")
@login_required
def agenda_preview():
    """Fragment chargé par le panneau de l'agenda (après la page, pour ne pas la ralentir)."""
    return render_template("campus/_agenda_preview.html", **service.agenda_preview(g.user))


@bp.route("/restos/<int:code>")
@login_required
def resto(code):
    return render_template("campus/resto.html", **service.resto_page(g.user, code))


@bp.route("/restos/<int:code>/menu.json")
@login_required
def resto_menu(code):
    return jsonify(service.menu_preview(code))


@bp.route("/signaler", methods=("POST",))
@login_required
def report():
    flash_report(service.report(g.user, request.form))
    return redirect(safe_next(request.form.get("back"), url_for("campus.library")))


feature = Feature("campus", blueprints=[bp], nav=[
    NavItem("campus.index", "Campus", order=4, section="site", opens="campus"),
    NavItem("campus.library", "Bibliothèque", order=10, section="campus"),
    NavItem("campus.restos", "Restos U", order=20, section="campus"),
], panels={"agenda": ["campus/agenda_panel.html"]},  # les restos U du jour dans Encadrement › Mon agenda
   tools=CAMPUS_TOOLS)  # l'assistant répond sur la BU et les restos U
