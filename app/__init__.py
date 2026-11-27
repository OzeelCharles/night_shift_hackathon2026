import os
import secrets
from datetime import date, datetime

from flask import Flask, abort, render_template, request, session

from . import db
from .planner import fmt_minutes

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def _secret_key(instance_path: str) -> str:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _to_dt(v):
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=_secret_key(app.instance_path),
        DATABASE=os.path.join(app.instance_path, "revision.sqlite"),
        UPLOAD_FOLDER=os.path.join(app.instance_path, "uploads"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        ENABLE_WEB=True,  # recherche de ressources sur internet
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    from . import admin, auth, routes
    app.register_blueprint(auth.bp)
    app.register_blueprint(routes.bp)
    app.register_blueprint(admin.bp)

    with app.app_context():
        auth.ensure_admin()

    # --- Protection CSRF simple (jeton en session, vérifié sur chaque POST) ---
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    @app.before_request
    def check_csrf():
        if request.method == "POST" and not app.config.get("WTF_CSRF_DISABLED"):
            token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
                abort(400, "Jeton CSRF invalide : rechargez la page.")

    app.jinja_env.globals["csrf_token"] = csrf_token

    # --- Filtres d'affichage en français ---
    @app.template_filter("hm")
    def hm(v):
        return _to_dt(v).strftime("%H:%M")

    @app.template_filter("jour")
    def jour(v):
        d = _to_dt(v) if not isinstance(v, date) else v
        return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"

    @app.template_filter("datefr")
    def datefr(v):
        if not v:
            return ""
        d = date.fromisoformat(v) if isinstance(v, str) else v
        return f"{d.day} {MOIS[d.month - 1]} {d.year}"

    app.jinja_env.filters["duree"] = fmt_minutes

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="Accès réservé aux administrateurs."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page introuvable."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, message=e.description), 400

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413, message="Fichier trop volumineux (25 Mo max)."), 413

    return app
