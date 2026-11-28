"""Sécurité transverse : utilisateur connecté, accès protégés, jeton CSRF, redirections sûres."""
import functools
import re
import secrets
import time
from urllib.parse import unquote

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from markupsafe import Markup

from backend.dao import users as users_dao


def start_session(user_id: int) -> None:
    """Ouvre une session neuve (rien de la session précédente ne survit : pas de fixation) liée à la version
    de session du compte et datée, pour l'invalidation et l'expiration (SEC-03)."""
    user = users_dao.get(user_id)
    session.clear()
    session["user_id"] = user["id"]
    session["sv"] = user["session_version"]
    session["auth_at"] = int(time.time())


def _session_valid(user) -> bool:
    if not user or not user["is_active"] or session.get("sv") != user["session_version"]:
        return False
    started = session.get("auth_at")
    lifetime = current_app.permanent_session_lifetime.total_seconds()
    return isinstance(started, int) and time.time() - started <= lifetime


def load_current_user():
    uid = session.get("user_id")
    g.user = None
    if uid is not None:
        user = users_dao.get(uid)
        if _session_valid(user):
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


_FORBIDDEN_IN_NEXT = re.compile(r"[\x00-\x1f\x7f\\]")  # caractères de contrôle (tabulation, CR, LF…) et antislash


def _internal_path(value: str) -> bool:
    if not value.startswith("/") or _FORBIDDEN_IN_NEXT.search(value):
        return False
    path = re.split(r"[?#]", value, maxsplit=1)[0]
    # « // » n'importe où dans le chemin, ou un segment « . » / « .. » : après normalisation (navigateur,
    # proxy) le chemin pourrait commencer par « // » et désigner un autre site
    return "//" not in path and not any(seg in (".", "..") for seg in path.split("/"))


def safe_next(value: str | None, default: str) -> str:
    """N'accepte qu'un chemin interne simple (SEC-04) : il commence par un seul « / », sans antislash, sans caractère
    de contrôle, sans « // » ni segment « . » / « .. ». La valeur est aussi vérifiée une fois décodée (%5C, %09,
    %2F%2F…), pour qu'aucun décodage ultérieur ne la transforme en adresse externe."""
    value = value or ""
    decoded = value
    for _ in range(3):
        decoded = unquote(decoded)
    return value if _internal_path(value) and _internal_path(decoded) else default


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


# ---------------------------------------------------------------- en-têtes HTTP (SEC-09)
CDN = "https://cdnjs.cloudflare.com"  # KaTeX (avec integrity, F-10)
FONTS_CSS, FONTS = "https://fonts.googleapis.com", "https://fonts.gstatic.com"  # polices Google (styles seulement)


def csp_nonce() -> str:
    """Nonce de la requête : seuls les <script> en ligne qui le portent s'exécutent (CSP sans 'unsafe-inline')."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(18)
    return g.csp_nonce


def content_security_policy(nonce: str) -> str:
    return "; ".join((
        "default-src 'self'",
        f"script-src 'self' {CDN} 'nonce-{nonce}'",
        f"style-src 'self' 'unsafe-inline' {CDN} {FONTS_CSS}",
        "img-src 'self' data: https:",
        f"font-src 'self' data: {CDN} {FONTS}",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ))


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",  # le type annoncé fait foi : pas de HTML deviné dans un document (SEC-06)
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def security_headers(response):
    """En-têtes de sécurité sur chaque réponse (pages, erreurs, fichiers, JSON)."""
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    response.headers.setdefault("Content-Security-Policy", content_security_policy(csp_nonce()))
    if current_app.config.get("PRODUCTION"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def init_app(app):
    app.after_request(security_headers)
    app.before_request(check_csrf)
    app.before_request(load_current_user)
    app.jinja_env.globals.update(csrf_token=csrf_token, csrf_field=csrf_field, csp_nonce=csp_nonce)
