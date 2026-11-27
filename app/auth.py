import functools
import re
import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


def validate_account(email: str, username: str, password: str | None, require_password=True) -> list[str]:
    errors = []
    if not EMAIL_RE.match(email):
        errors.append("Adresse e-mail invalide.")
    if not (2 <= len(username) <= 40):
        errors.append("Le nom d'utilisateur doit faire entre 2 et 40 caractères.")
    if (require_password or password) and len(password or "") < MIN_PASSWORD:
        errors.append(f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères.")
    return errors


def create_user(email: str, username: str, password: str, role: str = "user") -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (email, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (email.lower(), username, generate_password_hash(password), role),
    )
    db.execute("INSERT INTO preferences (user_id) VALUES (?)", (cur.lastrowid,))
    db.commit()
    return cur.lastrowid


def ensure_admin():
    """Crée un compte administrateur au premier lancement s'il n'en existe aucun."""
    import os

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
        return
    email = os.environ.get("ADMIN_EMAIL", "admin@revision.local")
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email.lower(),)).fetchone()
    if existing:
        db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (existing["id"],))
        db.commit()
        return
    create_user(email, "Administrateur", password, role="admin")
    if not current_app.config.get("TESTING"):
        print("=" * 60)
        print(" Compte administrateur créé")
        print(f"   e-mail       : {email}")
        print(f"   mot de passe : {password}")
        print(" Changez-le depuis l'espace admin (ou via ADMIN_PASSWORD).")
        print("=" * 60)


@bp.before_app_request
def load_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if user and user["is_active"]:
            g.user = user
        else:
            session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            flash("Connecte-toi pour accéder à ce service.", "info")
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(**kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(**kwargs)
    return wrapped


@bp.route("/inscription", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        errors = validate_account(email, username, password)
        if password != request.form.get("password2", ""):
            errors.append("Les mots de passe ne correspondent pas.")
        if not errors and get_db().execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("Un compte existe déjà avec cette adresse.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            uid = create_user(email, username, password)
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
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], request.form.get("password", "")):
            flash("E-mail ou mot de passe incorrect.", "error")
        elif not user["is_active"]:
            flash("Ce compte a été désactivé par un administrateur.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)
    return render_template("auth/login.html")


@bp.route("/deconnexion", methods=("POST",))
def logout():
    session.clear()
    flash("Tu es déconnecté.", "info")
    return redirect(url_for("main.index"))
