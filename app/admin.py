"""Espace administrateur : CRUD complet sur les utilisateurs, lecture/suppression des matières."""
import os

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .auth import admin_required, create_user, validate_account
from .db import get_db

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _get_user(user_id):
    u = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if u is None:
        abort(404)
    return u


def _admin_count():
    return get_db().execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0]


@bp.route("/")
@admin_required
def index():
    db = get_db()
    q = request.args.get("q", "").strip()
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM subjects s WHERE s.user_id = u.id) AS n_subjects,"
           " (SELECT COUNT(*) FROM sessions se WHERE se.user_id = u.id) AS n_sessions FROM users u")
    args = ()
    if q:
        sql += " WHERE u.email LIKE ? OR u.username LIKE ?"
        args = (f"%{q}%", f"%{q}%")
    users = db.execute(sql + " ORDER BY u.created_at DESC", args).fetchall()
    stats = {
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "subjects": db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
        "documents": db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "done": db.execute("SELECT COUNT(*) FROM sessions WHERE done = 1").fetchone()[0],
    }
    return render_template("admin/index.html", users=users, stats=stats, q=q)


@bp.route("/utilisateurs/nouveau", methods=("GET", "POST"))
@admin_required
def user_new():
    if request.method == "POST":
        f = request.form
        email, username = f.get("email", "").strip().lower(), f.get("username", "").strip()
        role = "admin" if f.get("role") == "admin" else "user"
        errors = validate_account(email, username, f.get("password", ""))
        if not errors and get_db().execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("Cette adresse est déjà utilisée.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/user_form.html", u=f, new=True)
        create_user(email, username, f["password"], role)
        flash(f"Utilisateur {email} créé.", "success")
        return redirect(url_for("admin.index"))
    return render_template("admin/user_form.html", u={"role": "user", "is_active": 1}, new=True)


@bp.route("/utilisateurs/<int:user_id>", methods=("GET", "POST"))
@admin_required
def user_edit(user_id):
    u = _get_user(user_id)
    db = get_db()
    if request.method == "POST":
        f = request.form
        email, username = f.get("email", "").strip().lower(), f.get("username", "").strip()
        password = f.get("password", "")
        role = "admin" if f.get("role") == "admin" else "user"
        active = 1 if f.get("is_active") else 0
        errors = validate_account(email, username, password, require_password=False)
        if db.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user_id)).fetchone():
            errors.append("Cette adresse est déjà utilisée.")
        losing_admin = u["role"] == "admin" and u["is_active"] and (role != "admin" or not active)
        if losing_admin and _admin_count() <= 1:
            errors.append("Impossible : il doit rester au moins un administrateur actif.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/user_form.html", u={**dict(u), **f.to_dict()}, new=False, user_id=user_id)
        db.execute("UPDATE users SET email=?, username=?, role=?, is_active=? WHERE id=?",
                   (email, username, role, active, user_id))
        if password:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))
        db.commit()
        flash("Utilisateur mis à jour.", "success")
        return redirect(url_for("admin.index"))
    subjects = db.execute("SELECT * FROM subjects WHERE user_id = ? ORDER BY name", (user_id,)).fetchall()
    return render_template("admin/user_form.html", u=u, new=False, user_id=user_id, subjects=subjects)


@bp.route("/utilisateurs/<int:user_id>/supprimer", methods=("POST",))
@admin_required
def user_delete(user_id):
    u = _get_user(user_id)
    if u["id"] == g.user["id"]:
        flash("Tu ne peux pas supprimer ton propre compte.", "error")
        return redirect(url_for("admin.index"))
    if u["role"] == "admin" and _admin_count() <= 1:
        flash("Impossible de supprimer le dernier administrateur.", "error")
        return redirect(url_for("admin.index"))
    _delete_files("SELECT d.stored_path FROM documents d JOIN subjects s ON s.id = d.subject_id "
                  "WHERE s.user_id = ?", user_id)
    get_db().execute("DELETE FROM users WHERE id = ?", (user_id,))
    get_db().commit()
    flash(f"Utilisateur {u['email']} supprimé avec toutes ses données.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/matieres")
@admin_required
def subjects():
    rows = get_db().execute(
        "SELECT s.*, u.email, (SELECT COUNT(*) FROM documents d WHERE d.subject_id = s.id) AS n_docs,"
        " (SELECT COUNT(*) FROM sessions se WHERE se.subject_id = s.id) AS n_sessions "
        "FROM subjects s JOIN users u ON u.id = s.user_id ORDER BY s.created_at DESC").fetchall()
    return render_template("admin/subjects.html", subjects=rows)


@bp.route("/matieres/<int:subject_id>/supprimer", methods=("POST",))
@admin_required
def subject_delete(subject_id):
    _delete_files("SELECT stored_path FROM documents WHERE subject_id = ?", subject_id)
    get_db().execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    get_db().commit()
    flash("Matière supprimée.", "success")
    return redirect(request.form.get("next") if request.form.get("next", "").startswith("/admin")
                    else url_for("admin.subjects"))


def _delete_files(sql, arg):
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    for row in get_db().execute(sql, (arg,)):
        real = os.path.realpath(row[0])
        if real.startswith(root + os.sep) and os.path.exists(real):
            os.remove(real)
