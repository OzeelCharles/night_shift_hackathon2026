"""Compteur de caféine : boissons énergisantes et café, avec repères de prévention."""
from flask import Blueprint, g, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required
from backend.services import caffeine_service
from backend.services.campus_tools import CAFFEINE_TOOLS

from . import flash_report

bp = Blueprint("caffeine", __name__)


@bp.route("/cafeine")
@login_required
def index():
    return render_template("caffeine.html", **caffeine_service.overview(g.user))


@bp.route("/cafeine/ajouter", methods=("POST",))
@login_required
def add():
    flash_report(caffeine_service.add(g.user, request.form))
    return redirect(url_for("caffeine.index"))


@bp.route("/cafeine/sommeil", methods=("POST",))
@login_required
def sleep():
    flash_report(caffeine_service.save_sleep(g.user["id"], request.form))
    return redirect(url_for("caffeine.index"))


@bp.route("/cafeine/<int:intake_id>/supprimer", methods=("POST",))
@login_required
def remove(intake_id):
    flash_report(caffeine_service.remove(g.user["id"], intake_id))
    return redirect(url_for("caffeine.index"))


feature = Feature("cafeine", blueprints=[bp], nav=[NavItem("caffeine.index", "Caféine", order=3, section="site", opens="cafeine")],
                  tools=CAFFEINE_TOOLS)  # l'assistant répond sur la consommation de caféine
