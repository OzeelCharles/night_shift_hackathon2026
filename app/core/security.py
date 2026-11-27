"""Sécurité transverse : utilisateur connecté, accès protégés, jeton CSRF, redirections sûres."""
import functools
import secrets

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from markupsafe import Markup

from app.dao import users as users_dao


def load_current_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        user = users_dao.get(uid)
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


def safe_next(value: str | None, default: str) -> str:
    """N'accepte qu'un chemin interne (pas de redirection vers un autre site)."""
    value = value or ""
    return value if value.startswith("/") and not value.startswith("//") else default


# ---------------------------------------------------------------- CSRF
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="_csrf" value="{csrf_token()}">')


def check_csrf():
    if request.method == "POST" and not current_app.config.get("WTF_CSRF_DISABLED"):
        token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
            abort(400, "Jeton CSRF invalide : recharge la page.")


def init_app(app):
    app.before_request(check_csrf)
    app.before_request(load_current_user)
    app.jinja_env.globals.update(csrf_token=csrf_token, csrf_field=csrf_field)
