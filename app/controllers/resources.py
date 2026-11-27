"""Ressources d'une matière : nouvelle recherche, ressources gardées, liens personnels.
Cette fonctionnalité réagit aussi aux événements (matière créée, profil modifié)."""
from flask import Blueprint, flash, g, redirect, request, url_for

from app.core import events
from app.core.features import Feature
from app.core.security import login_required
from app.services import resource_service, subject_service

from . import flash_report

bp = Blueprint("resources", __name__)


def _detail(subject_id, anchor):
    return redirect(url_for("subjects.detail", subject_id=subject_id) + anchor)


@bp.route("/matieres/<int:subject_id>/ressources", methods=("POST",))
@login_required
def refresh(subject_id):
    subject_service.get(subject_id, g.user["id"])
    if resource_service.refresh_async(g.user["id"], [subject_id]):
        flash("Recherche de nouvelles ressources lancée : elles s'affichent dans quelques secondes.", "info")
    else:
        flash("La recherche sur internet est désactivée sur ce serveur.", "error")
    return _detail(subject_id, "#proposees")


@bp.route("/ressources/<int:resource_id>/garder", methods=("POST",))
@login_required
def keep(resource_id):
    subject_id, report = resource_service.keep(g.user["id"], resource_id)
    flash_report(report)
    return _detail(subject_id, "#mes-ressources")


@bp.route("/ressources/<int:resource_id>/retirer", methods=("POST",))
@login_required
def remove(resource_id):
    subject_id, report = resource_service.remove(g.user["id"], resource_id)
    flash_report(report)
    return _detail(subject_id, "#mes-ressources")


@bp.route("/matieres/<int:subject_id>/ressources/ajouter", methods=("POST",))
@login_required
def add(subject_id):
    flash_report(resource_service.add_link(g.user["id"], subject_id, request.form.get("title", ""),
                                           request.form.get("url", "")))
    return _detail(subject_id, "#mes-ressources")


feature = Feature("ressources", blueprints=[bp], subscriptions=[
    (events.SUBJECT_CREATED, resource_service.on_subjects_created),
    (events.PROFILE_CHANGED, resource_service.on_profile_changed),
])
