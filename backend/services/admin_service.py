"""Administration : CRUD des utilisateurs, liste et suppression des matières."""
from werkzeug.security import generate_password_hash

from backend.business.accounts import LEVELS, validate_account
from backend.core import journal
from backend.dao import audit
from backend.dao import documents as documents_dao
from backend.dao import banned_words, passwords
from backend.dao import subjects as subjects_dao
from backend.dao import users as dao

from . import auth_service, subject_service
from .common import owned


SEARCH_MAX = 100  # au-delà, SQLite refuse le motif LIKE (et une recherche utile n'est jamais si longue)


def overview(search: str = "") -> dict:
    search = (search or "")[:SEARCH_MAX].strip()
    return {"users": dao.list_with_counts(search), "stats": dao.global_stats(), "q": search}


def _audit(action: str, target_id=None, target_email: str = "", **details) -> None:
    """SEC-10 : action d'un administrateur, dans le journal JSON et dans la table audit_log (jamais de mot de passe)."""
    entry = journal.log("admin." + action, target_id=target_id, target_email=target_email, **details)
    audit.record(entry.get("actor_id"), action, target_id, target_email, details)


def audit_log(limit: int = 100) -> list:
    return audit.recent(limit)


def get_user(user_id):
    return owned(dao.get(user_id))


def create_user(form) -> list[str]:
    email, username = form.get("email", "").strip().lower(), form.get("username", "").strip()
    errors = validate_account(email, username, form.get("password", ""), common=passwords.common(),
                              banned=banned_words.terms())
    if not errors and dao.email_taken(email):
        errors.append("Cette adresse est déjà utilisée.")
    if not errors:
        role = "admin" if form.get("role") == "admin" else "user"
        uid = auth_service.create_user(email, username, form["password"], role,
                                       level=form.get("level", "autre"), school=form.get("school", ""))
        _audit("creation", uid, email, role=role)
    return errors


def update_user(user_id, form, current_user_id=None) -> list[str]:
    u = get_user(user_id)
    email, username = form.get("email", "").strip().lower(), form.get("username", "").strip()
    password = form.get("password", "")
    role = "admin" if form.get("role") == "admin" else "user"
    active = 1 if form.get("is_active") else 0
    errors = validate_account(email, username, password, require_password=False, common=passwords.common(),
                              banned=banned_words.terms())
    if dao.email_taken(email, except_id=user_id):
        errors.append("Cette adresse est déjà utilisée.")
    losing_admin = u["role"] == "admin" and u["is_active"] and (role != "admin" or not active)
    if losing_admin and u["id"] == current_user_id:
        errors.append("Tu ne peux ni retirer tes propres droits d'administrateur, ni désactiver ton propre compte : "
                      "demande-le à un autre administrateur.")
    elif losing_admin and dao.active_admin_count() <= 1:
        errors.append("Impossible : il doit rester au moins un administrateur actif.")
    if errors:
        return errors
    dao.update(user_id, email=email, username=username, role=role, is_active=active,
               level=form.get("level") if form.get("level") in LEVELS else u["level"],
               school=form.get("school", "").strip()[:120])
    if password:
        dao.update(user_id, password_hash=generate_password_hash(password))
    if password or (u["is_active"] and not active):
        dao.bump_session_version(user_id)  # SEC-03 : les sessions ouvertes (cookies volés compris) tombent
    changes = {k: v for k, v in (("email", email), ("username", username), ("role", role), ("is_active", active))
               if u[k] != v}
    if password:
        _audit("reinitialisation_mot_de_passe", user_id, email)
    if u["is_active"] and not active:
        _audit("desactivation", user_id, email)
    elif not u["is_active"] and active:
        _audit("reactivation", user_id, email)
    changes.pop("is_active", None)
    if changes:
        _audit("modification", user_id, email, champs=sorted(changes))
    return []


def set_moderator(user_id, on: bool) -> str | None:
    """Nomme ou retire un modérateur de la communauté ; renvoie un message d'erreur si refusé."""
    u = get_user(user_id)
    if u["role"] == "admin":
        return "Un administrateur modère déjà : il n'a pas besoin d'être nommé."
    if on and not u["is_active"]:
        return "Ce compte est désactivé."
    dao.update(user_id, is_moderator=1 if on else 0)
    _audit("moderateur", user_id, u["email"], on=bool(on))
    return None


def delete_user(user_id, current_user_id) -> str | None:
    """Supprime un utilisateur et toutes ses données ; renvoie un message d'erreur si refusé."""
    u = get_user(user_id)
    if u["id"] == current_user_id:
        return "Tu ne peux pas supprimer ton propre compte."
    if u["role"] == "admin" and dao.active_admin_count() <= 1:
        return "Impossible de supprimer le dernier administrateur."
    subject_service.remove_files(documents_dao.paths_for_user(user_id))
    auth_service.erase_account(u)  # lignes, dossier de dépôts, tentatives de connexion
    _audit("suppression", user_id, u["email"])
    return None


def all_subjects():
    return subjects_dao.list_all_with_owner()


def user_subjects(user_id):
    return subjects_dao.list_for_user(user_id)


def delete_subject(subject_id) -> None:
    subject_service.remove_files(documents_dao.paths_for_subject(subject_id))
    subjects_dao.delete(subject_id)
    _audit("suppression_matiere", details_id=subject_id)
