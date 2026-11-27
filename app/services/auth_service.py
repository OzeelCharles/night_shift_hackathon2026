"""Comptes : inscription, connexion, profil, compte administrateur initial."""
import os
import secrets

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from app.business.accounts import LEVELS, validate_account, validate_profile, validate_username
from app.core import events
from app.dao import users as dao

from .report import Report


def create_user(email, username, password, role="user", level="autre", school="") -> int:
    return dao.create(email, username, generate_password_hash(password), role,
                      level if level in LEVELS else "autre", school.strip()[:120])


def register(form) -> tuple[int | None, list[str]]:
    email = form.get("email", "").strip().lower()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    level, school = form.get("level", ""), form.get("school", "").strip()
    errors = validate_account(email, username, password) + validate_profile(level, school)
    if password != form.get("password2", ""):
        errors.append("Les mots de passe ne correspondent pas.")
    if not errors and dao.email_taken(email):
        errors.append("Un compte existe déjà avec cette adresse.")
    if errors:
        return None, errors
    return create_user(email, username, password, level=level, school=school), []


def authenticate(email: str, password: str):
    """(utilisateur, None) si les identifiants sont bons, sinon (None, message d'erreur)."""
    user = dao.by_email(email.strip())
    if user is None or not check_password_hash(user["password_hash"], password):
        return None, "E-mail ou mot de passe incorrect."
    if not user["is_active"]:
        return None, "Ce compte a été désactivé par un administrateur."
    return user, None


def update_profile(user, form) -> tuple[Report, list[str]]:
    username = form.get("username", "").strip()
    level, school = form.get("level", ""), form.get("school", "").strip()
    errors = validate_profile(level, school) + validate_username(username)
    if errors:
        return Report(), errors
    dao.update(user["id"], username=username, level=level, school=school)
    extra = []
    if (level, school) != (user["level"], user["school"]):
        extra = events.emit(events.PROFILE_CHANGED, user_id=user["id"], level=level,
                            level_label=LEVELS[level], school=school)
    return Report().success(" ".join(["Profil enregistré."] + extra)), []


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
