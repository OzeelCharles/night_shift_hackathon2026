"""Mon agenda : import de l'emploi du temps, créneaux, rattachements."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from backend.business.text import bounded_int
from backend.core.features import Feature, NavItem
from backend.core.security import login_required, safe_next
from backend.services import agenda_service

from . import flash_report

bp = Blueprint("agenda", __name__)


def _back():
    return redirect(url_for("agenda.index"))


@bp.route("/agenda")
@login_required
def index():
    return render_template("calendar.html", **agenda_service.page(g.user["id"]))


@bp.route("/agenda/import", methods=("POST",))
@login_required
def import_ics():
    f = request.files.get("ics")
    if not f or not f.filename.lower().endswith(".ics"):
        flash("Choisis un fichier .ics (export de Google Agenda, Outlook, ENT...).", "error")
        return _back()
    flash_report(agenda_service.import_ics(g.user, f.read().decode("utf-8", errors="ignore"),
                                           sync_mode=bool(request.form.get("replace")),
                                           create_missing=bool(request.form.get("create_missing"))))
    return _back()


@bp.route("/agenda/photo", methods=("POST",))
@login_required
def import_photo():
    f = request.files.get("photo")
    if not f or not (f.mimetype or "").startswith("image/"):
        flash("Choisis une photo de ton emploi du temps (JPEG, PNG…).", "error")
        return _back()
    weeks = bounded_int(request.form.get("repeat_weeks"), 1, 1, 52)
    flash_report(agenda_service.import_photo(g.user, f.read(), weeks, bool(request.form.get("create_missing"))))
    return _back()


@bp.route("/agenda/ajouter", methods=("POST",))
@login_required
def add():
    flash_report(agenda_service.add_slot(g.user, request.form))
    return _back()


@bp.route("/agenda/<int:event_id>/matiere", methods=("POST",))
@login_required
def link(event_id):
    flash_report(agenda_service.link(g.user, event_id, request.form.get("subject", "")))
    return _back()


@bp.route("/agenda/<int:event_id>/supprimer", methods=("POST",))
@login_required
def delete(event_id):
    flash_report(agenda_service.delete_event(g.user, event_id))
    return redirect(safe_next(request.form.get("next"), url_for("agenda.index")))


@bp.route("/seances/<int:session_id>/supprimer", methods=("POST",))
@login_required
def delete_revision(session_id):
    flash_report(agenda_service.delete_revision(g.user, session_id))
    return redirect(safe_next(request.form.get("next"), url_for("agenda.index")))


@bp.route("/agenda/vider", methods=("POST",))
@login_required
def clear():
    flash_report(agenda_service.clear(g.user))
    return _back()


feature = Feature("agenda", blueprints=[bp], nav=[NavItem("agenda.index", "Mon agenda", order=30)])
