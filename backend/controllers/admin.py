"""Espace administrateur : CRUD des utilisateurs, matières."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import admin_required, start_session
from backend.services import admin_service

from . import flash_errors

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def index():
    return render_template("admin/index.html", **admin_service.overview(request.args.get("q", "")[:100].strip()))


@bp.route("/utilisateurs/nouveau", methods=("GET", "POST"))
@admin_required
def user_new():
    if request.method == "POST":
        errors = admin_service.create_user(request.form)
        if errors:
            flash_errors(errors)
            return render_template("admin/user_form.html", u=request.form, new=True)
        flash(f"Utilisateur {request.form.get('email', '').strip().lower()} créé.", "success")
        return redirect(url_for("admin.index"))
    return render_template("admin/user_form.html", u={"role": "user", "is_active": 1}, new=True)


@bp.route("/utilisateurs/<int:user_id>", methods=("GET", "POST"))
@admin_required
def user_edit(user_id):
    u = admin_service.get_user(user_id)
    if request.method == "POST":
        errors = admin_service.update_user(user_id, request.form, g.user["id"])
        if errors:
            flash_errors(errors)
            return render_template("admin/user_form.html", u={**dict(u), **request.form.to_dict()}, new=False,
                                   user_id=user_id)
        if user_id == g.user["id"]:
            start_session(user_id)  # son propre mot de passe changé : nouvelle session, les autres tombent
        flash("Utilisateur mis à jour.", "success")
        return redirect(url_for("admin.index"))
    return render_template("admin/user_form.html", u=u, new=False, user_id=user_id,
                           subjects=admin_service.user_subjects(user_id))


@bp.route("/utilisateurs/<int:user_id>/supprimer", methods=("POST",))
@admin_required
def user_delete(user_id):
    email = admin_service.get_user(user_id)["email"]
    error = admin_service.delete_user(user_id, g.user["id"])
    if error:
        flash(error, "error")
    else:
        flash(f"Utilisateur {email} supprimé avec toutes ses données.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/matieres")
@admin_required
def subjects():
    return render_template("admin/subjects.html", subjects=admin_service.all_subjects())


@bp.route("/matieres/<int:subject_id>/supprimer", methods=("POST",))
@admin_required
def subject_delete(subject_id):
    admin_service.delete_subject(subject_id)
    flash("Matière supprimée.", "success")
    nxt = request.form.get("next", "")
    return redirect(nxt if nxt.startswith("/admin") else url_for("admin.subjects"))  # retour dans l'admin uniquement


feature = Feature("administration", blueprints=[bp],
                  nav=[NavItem("admin.index", "Administration", order=90, admin=True)])
