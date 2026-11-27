"""Planning de la semaine, recalcul, séances faites, export .ics."""
from datetime import date

from flask import Blueprint, Response, flash, g, redirect, render_template, request, url_for

from app.business.text import parse_date
from app.core.features import Feature, NavItem
from app.core.security import login_required, safe_next
from app.services import planning_service

from . import flash_report

bp = Blueprint("planning", __name__)


@bp.route("/planning")
@login_required
def week():
    ref = parse_date(request.args.get("semaine")) or date.today()
    return render_template("plan.html", **planning_service.week(g.user["id"], ref))


@bp.route("/planning/generer", methods=("POST",))
@login_required
def generate():
    if not planning_service.has_subjects(g.user["id"]):
        flash("Ajoute d'abord une matière.", "error")
        return redirect(url_for("subjects.new"))
    flash_report(planning_service.update(g.user["id"], "C'est fait."))
    return redirect(safe_next(request.form.get("next"), url_for("planning.week")))


@bp.route("/seances/<int:session_id>/fait", methods=("POST",))
@login_required
def toggle(session_id):
    planning_service.toggle(session_id, g.user["id"])
    return redirect(safe_next(request.form.get("next"), url_for("planning.week")))


@bp.route("/planning/export.ics")
@login_required
def export():
    return Response(planning_service.export_ics(g.user["id"]), mimetype="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=planning-revisions.ics"})


feature = Feature("planning", blueprints=[bp], nav=[NavItem("planning.week", "Planning", order=20)])
