"""Inscription, connexion, déconnexion, profil."""
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from backend.core import events
from backend.core.features import Feature
from backend.core.security import login_required, safe_next
from backend.services import auth_service

from . import flash_errors, flash_report

bp = Blueprint("auth", __name__)


@bp.route("/inscription", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        uid, errors = auth_service.register(request.form)
        if errors:
            flash_errors(errors)
        else:
            session.clear()
            session["user_id"] = uid
            flash("Bienvenue ! Règle tes horaires et ta méthode de travail, puis ajoute une matière.", "success")
            return redirect(url_for("main.preferences"))
    return render_template("auth/register.html")


@bp.route("/connexion", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        user, error = auth_service.authenticate(request.form.get("email", ""), request.form.get("password", ""))
        if error:
            flash(error, "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(safe_next(request.args.get("next"), url_for("main.dashboard")))
    return render_template("auth/login.html")


@bp.route("/deconnexion", methods=("POST",))
def logout():
    if session.get("user_id") is not None:
        events.emit(events.USER_LOGGED_OUT, user_id=session["user_id"])  # ex. l'assistant efface l'historique
    session.clear()
    flash("Tu es déconnecté.", "info")
    return redirect(url_for("main.index"))


@bp.route("/profil", methods=("GET", "POST"))
@login_required
def profile():
    if request.method == "POST":
        report, errors = auth_service.update_profile(g.user, request.form)
        if errors:
            flash_errors(errors)
            return render_template("auth/profile.html", u=request.form)
        flash_report(report)
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", u=g.user)


feature = Feature("comptes", blueprints=[bp])
