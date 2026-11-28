"""Espace « Communauté » : forum collaboratif, messages privés et partage de supports de cours."""
from flask import Blueprint, g, redirect, render_template, request, send_file, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required, moderator_required
from backend.business import community as rules
from backend.services import community_service as service

from . import flash_report

bp = Blueprint("community", __name__, url_prefix="/communaute")


@bp.route("/")
@login_required
def index():
    return redirect(url_for("community.forum"))


# ---------------------------------------------------------------- forum
@bp.route("/forum")
@login_required
def forum():
    page = request.args.get("page", type=int) or 1
    return render_template("community/forum.html", **service.forum_home(request.args.get("categorie"),
                                                                       request.args.get("q", ""), page))


@bp.route("/forum/nouveau", methods=("GET", "POST"))
@login_required
def new_topic():
    if request.method == "POST":
        topic_id, report = service.new_topic(g.user, request.form, request.files.getlist("files"))
        flash_report(report)
        if topic_id:
            return redirect(url_for("community.topic", topic_id=topic_id))
    return render_template("community/new_topic.html", **service.new_topic_form(g.user, request.values.get("categorie", "")))


@bp.route("/forum/<int:topic_id>")
@login_required
def topic(topic_id):
    return render_template("community/topic.html", **service.topic_page(g.user, topic_id))


@bp.route("/forum/<int:topic_id>/repondre", methods=("POST",))
@login_required
def reply(topic_id):
    flash_report(service.reply(g.user, topic_id, request.form, request.files.getlist("files")))
    return redirect(url_for("community.topic", topic_id=topic_id) + "#fin")


@bp.route("/forum/messages/<int:post_id>/supprimer", methods=("POST",))
@login_required
def delete_post(post_id):
    topic_id, topic_gone, report = service.delete_post(g.user, post_id)
    flash_report(report)
    return redirect(url_for("community.forum") if topic_gone else url_for("community.topic", topic_id=topic_id))


@bp.route("/forum/messages/<int:post_id>/signaler", methods=("POST",))
@login_required
def report_post(post_id):
    topic_id, report = service.report_post(g.user, post_id, request.form)
    flash_report(report)
    return redirect(url_for("community.topic", topic_id=topic_id) + f"#m{post_id}")


# ---------------------------------------------------------------- messages privés
@bp.route("/messages")
@login_required
def inbox():
    return render_template("community/inbox.html", **service.inbox(g.user, request.args.get("q", "")))


@bp.route("/messages/<int:user_id>", methods=("GET", "POST"))
@login_required
def conversation(user_id):
    if request.method == "POST":
        flash_report(service.send(g.user, user_id, request.form, request.files.getlist("files")))
        return redirect(url_for("community.conversation", user_id=user_id) + "#fin")
    return render_template("community/conversation.html", **service.conversation(g.user, user_id))


@bp.route("/messages/signaler/<int:message_id>", methods=("POST",))
@login_required
def report_message(message_id):
    other_id, report = service.report_message(g.user, message_id, request.form)
    flash_report(report)
    return redirect(url_for("community.conversation", user_id=other_id))


# ---------------------------------------------------------------- règles et modération
@bp.route("/regles")
@login_required
def rules_page():
    return render_template("community/rules.html", rules=rules.RULES, reasons=rules.REASONS)


@bp.route("/moderation")
@moderator_required
def moderation():
    return render_template("community/moderation.html", **service.moderation_page(g.user))


@bp.route("/moderation/<int:report_id>", methods=("POST",))
@moderator_required
def decide(report_id):
    flash_report(service.decide(g.user, report_id, request.form))
    return redirect(url_for("community.moderation"))


@bp.route("/moderation/membres/<int:user_id>/retablir", methods=("POST",))
@moderator_required
def restore(user_id):
    flash_report(service.restore_member(g.user, user_id))
    return redirect(url_for("community.moderation") + "#suspendus")


# ---------------------------------------------------------------- fichiers
@bp.route("/fichiers/<int:file_id>")
@login_required
def file(file_id):
    path, mime, name = service.download(g.user, file_id)
    return send_file(path, mimetype=mime, as_attachment=True, download_name=name)


feature = Feature("communaute", blueprints=[bp], nav=[
    NavItem("community.index", "Communauté", order=3, section="site", opens="communaute"),
    NavItem("community.forum", "Forum", order=10, section="communaute"),
    NavItem("community.inbox", "Messages", order=20, section="communaute"),
    NavItem("community.rules_page", "Règles", order=30, section="communaute"),
    NavItem("community.moderation", "Modération", order=40, section="communaute", moderator=True),
])
