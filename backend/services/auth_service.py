"""Comptes : inscription, connexion, profil, compte administrateur initial."""
import functools
import os
import secrets
import time
from datetime import date

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from backend.business.accounts import LEVELS, clean_details, validate_account, validate_profile, validate_username
from backend.core import events
from backend.dao import auth_attempts, banned_words, passwords
from backend.dao import users as dao

from .report import Report

# Messages neutres (SEC-03) : ils ne disent ni si l'adresse existe, ni si le compte est désactivé.
LOGIN_ERROR = "E-mail ou mot de passe incorrect."
TOO_MANY_LOGINS = "Trop de tentatives de connexion. Patiente quelques minutes avant de réessayer."
TOO_MANY_SIGNUPS = "Trop d'inscriptions depuis ce réseau. Réessaie plus tard."
SIGNUP_REFUSED = ("Impossible de créer un compte avec ces informations. "
                  "Si tu as déjà un compte, connecte-toi.")
TOO_MANY = 429


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Empreinte factice (même algorithme que les vraies) : vérifiée quand l'e-mail est inconnu,
    pour que la réponse prenne le même temps qu'avec un compte existant."""
    return generate_password_hash(secrets.token_urlsafe(16))


def _limit(name: str) -> int:
    return int(current_app.config[name])


def _take(kind: str, key: str, max_name: str, window_name: str, now: float) -> tuple[bool, int]:
    """Réserve une tentative AVANT de faire le travail (vérifier un mot de passe, créer un compte) : elle est
    enregistrée et validée tout de suite, puis on compte les tentatives de la fenêtre enregistrées jusqu'à elle
    incluse. Des requêtes simultanées ne peuvent donc pas toutes lire « 4 » : au plus `max` passent.
    Renvoie (permis, identifiant de la tentative)."""
    attempt = auth_attempts.record(kind, key, now)
    allowed = auth_attempts.count_upto(kind, key, now - _limit(window_name), attempt) <= _limit(max_name)
    return allowed, attempt


def create_user(email, username, password, role="user", level="autre", school="") -> int:
    return dao.create(email, username, generate_password_hash(password), role,
                      level if level in LEVELS else "autre", school.strip()[:120])


def register(form, ip: str = "") -> tuple[int | None, list[str], int]:
    """(identifiant, [], 200) si le compte est créé ; sinon (None, erreurs, statut HTTP : 200 ou 429).
    5 inscriptions au plus par IP et par heure ; une adresse déjà prise reçoit un message neutre."""
    now = time.time()
    email = form.get("email", "").strip().lower()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    level, school = form.get("level", ""), form.get("school", "").strip()
    errors = validate_account(email, username, password, common=passwords.common(),
                              banned=banned_words.terms()) + validate_profile(level, school)
    if password != form.get("password2", ""):
        errors.append("Les mots de passe ne correspondent pas.")
    if errors:
        return None, errors, 200
    # chaque inscription valide compte, adresse libre ou non (sinon l'oracle d'existence serait gratuit)
    allowed, attempt = _take("register_ip", ip, "REGISTER_MAX_PER_IP", "REGISTER_WINDOW_SECONDS", now)
    if not allowed:
        auth_attempts.forget(attempt)  # refusée : ne prolonge pas le blocage
        return None, [TOO_MANY_SIGNUPS], TOO_MANY
    if dao.email_taken(email):
        generate_password_hash(password)  # même coût qu'une création : le temps ne trahit pas l'adresse
        return None, [SIGNUP_REFUSED], 200
    return create_user(email, username, password, level=level, school=school), [], 200


def authenticate(email: str, password: str):
    """(utilisateur, None) si les identifiants sont bons, sinon (None, message neutre).
    Le mot de passe est toujours vérifié (empreinte factice si l'e-mail est inconnu) et le même message
    couvre e-mail inconnu, mauvais mot de passe et compte désactivé."""
    user = dao.by_email(email.strip())
    valid = check_password_hash(user["password_hash"] if user else _dummy_hash(), password)
    if user is None or not valid or not user["is_active"]:
        return None, LOGIN_ERROR
    return user, None


def login(email: str, password: str, ip: str = ""):
    """Connexion limitée : (utilisateur, None, 200) ou (None, message neutre, 200 ou 429).
    Après 5 échecs en 15 minutes sur un compte, ou depuis une IP, toute tentative est refusée (429),
    même avec le bon mot de passe."""
    now = time.time()
    account = email.strip().lower()
    window = "LOGIN_WINDOW_SECONDS"
    auth_attempts.purge_before(now - max(_limit(window), _limit("REGISTER_WINDOW_SECONDS")))
    account_ok, account_attempt = _take("login_account", account, "LOGIN_MAX_FAILURES", window, now)
    ip_ok, ip_attempt = _take("login_ip", ip, "LOGIN_MAX_FAILURES", window, now)
    if not (account_ok and ip_ok):
        auth_attempts.forget(account_attempt, ip_attempt)  # refusée sans essai : ne compte pas comme un échec
        return None, TOO_MANY_LOGINS, TOO_MANY
    started = time.monotonic()
    user, error = authenticate(email, password)
    if error:
        # Durée plancher d'un échec : l'écart restant (charge du serveur, accès à la base) disparaît derrière
        # une attente fixe, identique pour un e-mail inconnu, un mauvais mot de passe ou un compte désactivé.
        time.sleep(max(0.0, float(current_app.config["LOGIN_FAILURE_MIN_SECONDS"]) - (time.monotonic() - started)))
        return None, error, 200  # les deux tentatives réservées restent : ce sont des échecs
    # réussite : le compteur du compte repart de zéro ; celui de l'IP garde ses échecs passés
    auth_attempts.clear("login_account", account)
    auth_attempts.forget(ip_attempt)
    return user, None, 200


def update_profile(user, form) -> tuple[Report, list[str]]:
    username = form.get("username", "").strip()
    level, school = form.get("level", ""), form.get("school", "").strip()
    details, detail_errors = clean_details(form, date.today())
    errors = validate_profile(level, school) + validate_username(username, banned_words.terms()) + detail_errors
    if errors:
        return Report(), errors
    dao.update(user["id"], username=username, level=level, school=school, **details)
    extra = []
    if (level, school) != (user["level"], user["school"]):
        extra = events.emit(events.PROFILE_CHANGED, user_id=user["id"], level=level,
                            level_label=LEVELS[level], school=school)
    return Report().success(" ".join(["Profil enregistré."] + extra)), []


def update_details(user, form) -> tuple[Report, list[str]]:
    """Informations complémentaires seules (fenêtre qui suit l'inscription) : toutes facultatives."""
    details, errors = clean_details(form, date.today())
    if errors:
        return Report(), errors
    dao.update(user["id"], **details)
    return Report().success("C'est noté : ces informations préremplissent maintenant tes lettres de motivation."), []


def ensure_admin() -> None:
    """Crée un compte administrateur au premier lancement s'il n'en existe aucun."""
    if dao.any_admin():
        return
    email = os.environ.get("ADMIN_EMAIL", "admin@revision.local")
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    existing = dao.by_email(email)
    if existing:
        dao.update(existing["id"], role="admin")
        return
    create_user(email, "Administrateur", password, role="admin")
    if not current_app.config.get("TESTING"):
        print("=" * 60)
        print(" Compte administrateur créé")
        print(f"   e-mail       : {email}")
        print(f"   mot de passe : {password}")
        print(" Changez-le depuis l'espace admin (ou via ADMIN_PASSWORD).")
        print("=" * 60)
