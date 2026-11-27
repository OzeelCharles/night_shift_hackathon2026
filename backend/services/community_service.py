"""Communauté : messages privés, partage de supports de cours, forum."""
from __future__ import annotations

import mimetypes
import os
import shutil
import uuid

from flask import current_app
from werkzeug.exceptions import Forbidden, NotFound
from werkzeug.utils import secure_filename

from backend.business import community as rules
from backend.dao import community as dao
from backend.dao import documents as documents_dao
from backend.dao import users as users_dao

from .common import owned
from .report import Report


# ---------------------------------------------------------------- fichiers
def _folder() -> str:
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "community")
    os.makedirs(folder, exist_ok=True)
    return folder


def _inside_uploads(path: str) -> str | None:
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    real = os.path.realpath(path)
    return real if real.startswith(root + os.sep) and os.path.exists(real) else None


def remove_files(paths) -> None:
    for path in paths:
        if real := _inside_uploads(path):
            os.remove(real)


def _stored_name(filename: str) -> str:
    return os.path.join(_folder(), f"{uuid.uuid4().hex}_{secure_filename(filename) or 'fichier'}")


def _attach(user_id, files, doc_ids, **target) -> list[str]:
    """Enregistre les fichiers téléversés et les copies des supports de cours choisis. Renvoie les refus."""
    refused = []
    uploads = [f for f in files if f and f.filename][:rules.MAX_FILES]
    if len([f for f in files if f and f.filename]) > rules.MAX_FILES:
        refused.append(f"{rules.MAX_FILES} fichiers au maximum par message : les suivants sont ignorés.")
    for f in uploads:
        if not rules.allowed_attachment(f.filename):
            refused.append(f"« {f.filename} » : format non pris en charge.")
            continue
        path = _stored_name(f.filename)
        f.save(path)
        size = os.path.getsize(path)
        if size > rules.MAX_FILE_BYTES:
            os.remove(path)
            refused.append(f"« {f.filename} » : fichier trop gros ({rules.human_size(rules.MAX_FILE_BYTES)} au maximum).")
            continue
        dao.add_file(user_id, f.filename[:200], path, size, **target)
    for doc_id in doc_ids[:rules.MAX_FILES]:
        doc = documents_dao.get_owned(doc_id, user_id)  # seulement ses propres supports
        source = _inside_uploads(doc["stored_path"]) if doc else None
        if not source:
            continue
        path = _stored_name(doc["filename"])
        shutil.copyfile(source, path)  # copie : supprimer le support plus tard ne casse pas le partage
        dao.add_file(user_id, doc["filename"], path, os.path.getsize(path), **target)
    return refused


def download(user, file_id) -> tuple[str, str, str]:
    """(chemin, type MIME, nom) d'un fichier que l'utilisateur a le droit de voir : jamais ceux d'un message privé d'autrui."""
    f = owned(dao.get_file(file_id))
    if f["message_id"] is not None and user["id"] not in (f["sender_id"], f["recipient_id"]):
        raise NotFound()
    real = owned(_inside_uploads(f["stored_path"]))
    return real, mimetypes.guess_type(f["filename"])[0] or "application/octet-stream", f["filename"]


def _with_files(rows, files, key):
    by = {}
    for f in files:
        by.setdefault(f[key], []).append(dict(f, size_label=rules.human_size(f["size"])))
    return [dict(r, files=by.get(r["id"], [])) for r in rows]


MUTED = "Ton accès en écriture à la communauté est suspendu par la modération : tu peux lire, pas publier."


# ---------------------------------------------------------------- messages privés
def inbox(user, q: str = "") -> dict:
    q = q.strip()
    return {"conversations": dao.conversations(user["id"]), "q": q,
            "results": dao.search_users(user["id"], q) if len(q) >= 2 else []}


def conversation(user, other_id) -> dict:
    other = owned(dao.public_user(other_id))
    if other["id"] == user["id"]:
        raise NotFound()
    dao.mark_read(user["id"], other["id"])
    messages = dao.thread(user["id"], other["id"])
    reported = dao.reported_ids(user["id"], "message", (m["id"] for m in messages))
    messages = [dict(m, reported=m["id"] in reported) for m in _with_files(
        messages, dao.files_of_messages(m["id"] for m in messages), "message_id")]
    return {"other": other, "messages": messages, "muted": bool(user["community_muted"]),
            "documents": dao.my_documents(user["id"]), "limits": rules}


def send(user, other_id, form, files) -> Report:
    if user["community_muted"]:
        return Report().error(MUTED)
    other = owned(dao.public_user(other_id))
    if other["id"] == user["id"]:
        return Report().error("Tu ne peux pas t'écrire à toi-même.")
    body = rules.clean_body(form.get("body"))
    uploads = [f for f in files if f and f.filename]
    doc_ids = [int(d) for d in form.getlist("documents") if d.isdigit()]
    if not body and not (uploads or doc_ids):
        return Report().error("Écris un message ou joins un fichier.")
    if len(body) > rules.BODY_RANGE[1]:
        return Report().error(rules.validate_body(body)[0])
    message_id = dao.add_message(user["id"], other["id"], body)
    report = Report()
    for msg in _attach(user["id"], files, doc_ids, message_id=message_id):
        report.warning(msg)
    return report


def unread(user_id) -> int:
    return dao.unread_count(user_id)


# ---------------------------------------------------------------- forum
def forum_home(category: str | None, q: str, page: int) -> dict:
    category = category if category in rules.CATEGORIES else None
    q = (q or "").strip()[:80]
    page = max(1, page)
    total = dao.count_topics(category, q)
    return {"categories": rules.CATEGORIES, "counts": dao.category_counts(), "category": category, "q": q,
            "topics": dao.topics(category, q, rules.PAGE_SIZE, (page - 1) * rules.PAGE_SIZE),
            "page": page, "pages": max(1, -(-total // rules.PAGE_SIZE)), "total": total}


def new_topic(user, form, files) -> tuple[int | None, Report]:
    if user["community_muted"]:
        return None, Report().error(MUTED)
    category, title, body = form.get("category", ""), (form.get("title") or "").strip(), rules.clean_body(form.get("body"))
    errors = rules.validate_topic(category, title, body)
    if errors:
        report = Report()
        for e in errors:
            report.error(e)
        return None, report
    topic_id = dao.create_topic(user["id"], category, title)
    post_id = dao.add_post(topic_id, user["id"], body)
    report = Report().success("Discussion créée.")
    for msg in _attach(user["id"], files, [int(d) for d in form.getlist("documents") if d.isdigit()], post_id=post_id):
        report.warning(msg)
    return topic_id, report


def new_topic_form(user, selected: str) -> dict:
    return {"categories": rules.CATEGORIES, "selected": selected, "documents": dao.my_documents(user["id"]),
            "limits": rules}


def topic_page(user, topic_id) -> dict:
    topic = owned(dao.get_topic(topic_id))
    posts = dao.posts(topic_id)
    reported = dao.reported_ids(user["id"], "post", (p["id"] for p in posts))
    posts = [dict(p, reported=p["id"] in reported, staff=rules.is_staff(p["author_role"], p["author_is_moderator"]))
             for p in _with_files(posts, dao.files_of_posts(p["id"] for p in posts), "post_id")]
    return {"topic": topic, "category_label": rules.CATEGORIES.get(topic["category"], ("Forum",))[0],
            "posts": posts, "muted": bool(user["community_muted"]),
            "documents": dao.my_documents(user["id"]), "limits": rules}


def reply(user, topic_id, form, files) -> Report:
    if user["community_muted"]:
        return Report().error(MUTED)
    owned(dao.get_topic(topic_id))
    body = rules.clean_body(form.get("body"))
    if errors := rules.validate_body(body):
        return Report().error(errors[0])
    post_id = dao.add_post(topic_id, user["id"], body)
    report = Report().success("Réponse publiée.")
    for msg in _attach(user["id"], files, [int(d) for d in form.getlist("documents") if d.isdigit()], post_id=post_id):
        report.warning(msg)
    return report


def _may_moderate(user, author_id) -> bool:
    return rules.is_staff(user["role"], user["is_moderator"]) or user["id"] == author_id


def _remove_post(post) -> tuple[int | None, bool]:
    """Supprime une réponse, ou la discussion entière si c'est son premier message. (discussion, discussion supprimée ?)"""
    topic = dao.get_topic(post["topic_id"])
    if topic is None:
        return None, True
    if dao.posts(topic["id"])[0]["id"] == post["id"]:
        remove_files(dao.paths_for_topic(topic["id"]))
        dao.delete_topic(topic["id"])
        return topic["id"], True
    remove_files(dao.paths_for_post(post["id"]))
    dao.delete_post(post["id"])
    return topic["id"], False


def delete_post(user, post_id) -> tuple[int, bool, Report]:
    """Supprime un message du forum (son auteur ou la modération). (discussion, discussion supprimée ?, messages)"""
    post = owned(dao.get_post(post_id))
    if not _may_moderate(user, post["author_id"]):
        raise Forbidden()
    topic_id, gone = _remove_post(post)
    return topic_id, gone, Report().info("Discussion supprimée." if gone else "Réponse supprimée.")


# ---------------------------------------------------------------- signalements
def _report(user, target_type, target_id, author_id, content, context, topic_id, form) -> Report:
    reason, details = form.get("reason", ""), (form.get("details") or "").strip()
    if errors := rules.validate_report(reason, details):
        return Report().error(errors[0])
    if author_id == user["id"]:
        return Report().error("Tu ne peux pas signaler ton propre message.")
    if dao.add_report(user["id"], target_type, target_id, author_id, content, context, topic_id, reason, details) is None:
        return Report().info("Tu as déjà signalé ce message : la modération va l'examiner.")
    return Report().success("Merci, la modération va examiner ce message.")


def report_post(user, post_id, form) -> tuple[int | None, Report]:
    """Signale un message du forum. Renvoie aussi la discussion, pour y revenir."""
    post = owned(dao.get_post(post_id))
    topic = owned(dao.get_topic(post["topic_id"]))
    files = [f["filename"] for f in dao.files_of_posts([post["id"]])]
    content = post["body"] + (f"\n[Pièces jointes : {', '.join(files)}]" if files else "")
    return topic["id"], _report(user, "post", post["id"], post["author_id"], content, topic["title"], topic["id"], form)


def report_message(user, message_id, form) -> tuple[int, Report]:
    """Signale un message privé reçu. Renvoie l'interlocuteur, pour revenir à la conversation."""
    m = owned(dao.get_message(message_id))
    if m["recipient_id"] != user["id"]:  # seul le destinataire voit le message : lui seul peut le signaler
        raise NotFound()
    files = dao.filenames_for_message(m["id"])
    content = m["body"] + (f"\n[Pièces jointes : {', '.join(files)}]" if files else "")
    return m["sender_id"], _report(user, "message", m["id"], m["sender_id"], content,
                                   f"Message privé de {m['sender']} à {m['recipient']}", None, form)


# ---------------------------------------------------------------- modération
def _require_staff(user) -> None:
    if not rules.is_staff(user["role"], user["is_moderator"]):
        raise Forbidden()


def _group(reports) -> list[dict]:
    """Un élément par contenu signalé, avec tous ses signalements."""
    groups: dict = {}
    for r in reports:
        g = groups.setdefault((r["target_type"], r["target_id"]), {"report": r, "all": []})
        g["all"].append(r)
    return list(groups.values())


def moderation_page(user) -> dict:
    _require_staff(user)
    queue = []
    for g in _group(dao.open_reports()):
        r = g["report"]
        g["id"] = r["id"]
        g["may_mute"] = bool(r["author_id"]) and not r["author_muted"] and rules.can_mute(
            user["role"], r["author_role"] or "user", r["author_is_moderator"])
        queue.append(g)
    return {"queue": queue, "handled": _group(dao.recent_closed()), "muted": dao.muted_users(),
            "moderators": dao.moderators(), "reasons": rules.REASONS, "decisions": rules.DECISIONS, "limits": rules}


def decide(user, report_id, form) -> Report:
    """Applique une décision à un contenu signalé : tous ses signalements ouverts sont clos ensemble."""
    _require_staff(user)
    r = owned(dao.get_report(report_id))
    decision, note = form.get("decision", ""), (form.get("note") or "").strip()[:rules.NOTE_MAX]
    if decision not in rules.DECISIONS:
        return Report().error("Choisis une décision.")
    if r["status"] != "open":
        return Report().info("Ce signalement a déjà été traité.")
    report = Report()
    if decision != "dismiss":
        if r["target_type"] == "post":
            if post := dao.get_post(r["target_id"]):
                _remove_post(post)
        elif message := dao.get_message(r["target_id"]):
            remove_files(dao.paths_for_message(message["id"]))
            dao.delete_message(message["id"])
        report.success("Contenu supprimé.")
    if decision == "remove_mute":
        author = users_dao.get(r["author_id"]) if r["author_id"] else None
        if author is None:
            report.info("L'auteur n'existe plus.")
        elif not rules.can_mute(user["role"], author["role"], author["is_moderator"]):
            report.warning(f"{author['username']} ne peut pas être suspendu par toi (membre de l'équipe de modération).")
        else:
            users_dao.update(author["id"], community_muted=1)
            report.success(f"{author['username']} ne peut plus publier ni écrire.")
    dao.close_reports(r["target_type"], r["target_id"], decision, user["id"], note)
    return report.success("Signalement rejeté.") if decision == "dismiss" else report


def restore_member(user, member_id) -> Report:
    _require_staff(user)
    member = owned(users_dao.get(member_id))
    users_dao.update(member["id"], community_muted=0)
    return Report().success(f"{member['username']} peut de nouveau publier.")
