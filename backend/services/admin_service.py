"""Administration : CRUD des utilisateurs, liste et suppression des matières."""
from werkzeug.security import generate_password_hash

from backend.business.accounts import LEVELS, validate_account
from backend.dao import documents as documents_dao
from backend.dao import subjects as subjects_dao
from backend.dao import users as dao

from . import auth_service, subject_service
from .common import owned


def overview(search: str = "") -> dict:
    return {"users": dao.list_with_counts(search), "stats": dao.global_stats(), "q": search}


def get_user(user_id):
    return owned(dao.get(user_id))


def create_user(form) -> list[str]:
    email, username = form.get("email", "").strip().lower(), form.get("username", "").strip()
    errors = validate_account(email, username, form.get("password", ""))
    if not errors and dao.email_taken(email):
        errors.append("Cette adresse est déjà utilisée.")
    if not errors:
        auth_service.create_user(email, username, form["password"], "admin" if form.get("role") == "admin" else "user",
                                 level=form.get("level", "autre"), school=form.get("school", ""))
    return errors


def update_user(user_id, form) -> list[str]:
    u = get_user(user_id)
    email, username = form.get("email", "").strip().lower(), form.get("username", "").strip()
    password = form.get("password", "")
    role = "admin" if form.get("role") == "admin" else "user"
    active = 1 if form.get("is_active") else 0
    errors = validate_account(email, username, password, require_password=False)
    if dao.email_taken(email, except_id=user_id):
        errors.append("Cette adresse est déjà utilisée.")
    losing_admin = u["role"] == "admin" and u["is_active"] and (role != "admin" or not active)
    if losing_admin and dao.active_admin_count() <= 1:
        errors.append("Impossible : il doit rester au moins un administrateur actif.")
    if errors:
        return errors
    dao.update(user_id, email=email, username=username, role=role, is_active=active,
               level=form.get("level") if form.get("level") in LEVELS else u["level"],
               school=form.get("school", "").strip()[:120])
    if password:
        dao.update(user_id, password_hash=generate_password_hash(password))
    return []


def delete_user(user_id, current_user_id) -> str | None:
    """Supprime un utilisateur et toutes ses données ; renvoie un message d'erreur si refusé."""
    u = get_user(user_id)
    if u["id"] == current_user_id:
        return "Tu ne peux pas supprimer ton propre compte."
    if u["role"] == "admin" and dao.active_admin_count() <= 1:
        return "Impossible de supprimer le dernier administrateur."
    subject_service.remove_files(documents_dao.paths_for_user(user_id))
    dao.delete(user_id)
    return None


def all_subjects():
    return subjects_dao.list_all_with_owner()


def user_subjects(user_id):
    return subjects_dao.list_for_user(user_id)


def delete_subject(subject_id) -> None:
    subject_service.remove_files(documents_dao.paths_for_subject(subject_id))
    subjects_dao.delete(subject_id)
