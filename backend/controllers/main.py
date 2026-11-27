"""Accueil, tableau de bord du jour, préférences de travail."""
from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required
from backend.services import planning_service, preference_service

from . import flash_report

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """Page d'accueil de Rivezli : seulement le nom de l'application (frontend/templates/home.html)."""
    return render_template("home.html")


@bp.route("/encadrement")
def encadrement():
    """Entrée « Encadrement » de la page de départ : ouvre l'espace du planificateur, directement
    sur l'onglet « Aujourd'hui ». Un visiteur (sans compte) voit d'abord sa présentation."""
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@bp.route("/tableau-de-bord")
@login_required
def dashboard():
    return render_template("dashboard.html", date_fromiso=date.fromisoformat,
                           **planning_service.dashboard(g.user["id"]))


@bp.route("/preferences", methods=("GET", "POST"))
@login_required
def preferences():
    uid = g.user["id"]
    if request.method == "POST":
        error = preference_service.save(uid, request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("main.preferences"))
        flash_report(planning_service.update(uid, "Préférences enregistrées."))
        return redirect(url_for("planning.week"))
    _, row = preference_service.load(uid)
    return render_template("preferences.html", p=row, rest=preference_service.rest_days_of(row))


feature = Feature("tableau-de-bord", blueprints=[bp],
                  nav=[NavItem("main.encadrement", "Encadrement", order=1, section="site", opens="encadrement"),
                       NavItem("main.dashboard", "Aujourd'hui", order=10),
                       NavItem("main.preferences", "Mes réglages", order=40)])
