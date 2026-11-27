"""Espace « Dossier scolaire » : les notes de l'utilisateur, visibles par lui seul."""
from flask import Blueprint, g, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required
from backend.services import dossier_service

from . import flash_report

bp = Blueprint("dossier", __name__, url_prefix="/dossier")


@bp.route("/")
@login_required
def index():
    return render_template("dossier/index.html", **dossier_service.page(g.user["id"]))


@bp.route("/notes", methods=("POST",))
@login_required
def add():
    flash_report(dossier_service.add(g.user["id"], request.form))
    return redirect(url_for("dossier.index") + "#matieres")


@bp.route("/notes/<int:grade_id>/supprimer", methods=("POST",))
@login_required
def delete(grade_id):
    flash_report(dossier_service.delete(g.user["id"], grade_id))
    return redirect(url_for("dossier.index") + "#matieres")


feature = Feature("dossier", blueprints=[bp], nav=[
    NavItem("dossier.index", "Dossier scolaire", order=2, section="site", opens="dossier"),
    NavItem("dossier.index", "Mes notes", order=10, section="dossier"),
])
