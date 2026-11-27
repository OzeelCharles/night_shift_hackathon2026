"""Matières : création, page de détail, modification, supports de cours, examens."""
from flask import Blueprint, flash, g, redirect, render_template, request, send_file, session, url_for

from backend.business import subjects as rules
from backend.core.features import Feature
from backend.core.security import login_required
from backend.services import exam_service, subject_service, suggestion_service

from . import flash_report

bp = Blueprint("subjects", __name__)


def _detail(subject_id, anchor=""):
    return redirect(url_for("subjects.detail", subject_id=subject_id) + anchor)


@bp.route("/matieres/nouvelle", methods=("GET", "POST"))
@login_required
def new():
    if request.method == "POST":
        force_new = bool(request.form.get("force_new"))
        result = subject_service.create_from_form(g.user, request.form.to_dict(), request.files.getlist("documents"),
                                                  force_new)
        flash_report(result.report)
        if result.form is not None:
            return render_template("subjects/form.html", s=result.form, new=True, force_new=force_new,
                                   agenda_suggestions=suggestion_service.subjects_from_agenda(g.user["id"]))
        if result.merged_from:
            session["merged_hint"] = {"subject_id": result.subject_id, "name": result.merged_from}
        return _detail(result.subject_id)
    return render_template("subjects/form.html", s={"name": request.args.get("nom", "")}, new=True,
                           force_new=request.args.get("nouvelle"),
                           agenda_suggestions=suggestion_service.subjects_from_agenda(g.user["id"]))


@bp.route("/matieres/<int:subject_id>")
@login_required
def detail(subject_id):
    context = subject_service.detail(g.user, subject_id)
    hint = session.get("merged_hint")
    if hint and hint.get("subject_id") == subject_id:
        session.pop("merged_hint")
    else:
        hint = None
    return render_template("subjects/detail.html", hint=hint, **context)


@bp.route("/matieres/<int:subject_id>/modifier", methods=("GET", "POST"))
@login_required
def edit(subject_id):
    if request.method == "POST":
        result = subject_service.update_from_form(g.user, subject_id, request.form.to_dict())
        flash_report(result.report)
        if result.form is not None:
            return render_template("subjects/form.html", s=result.form, new=False, subject_id=subject_id)
        return _detail(subject_id)
    s = subject_service.get(subject_id, g.user["id"])
    form = dict(s, topics="\n".join(rules.topics_of(s)))
    return render_template("subjects/form.html", s=form, new=False, subject_id=subject_id)


@bp.route("/matieres/<int:subject_id>/supprimer", methods=("POST",))
@login_required
def delete(subject_id):
    flash_report(subject_service.delete(g.user, subject_id))
    return redirect(url_for("main.dashboard"))


@bp.route("/matieres/<int:subject_id>/programme", methods=("POST",))
@login_required
def program(subject_id):
    flash_report(subject_service.set_program(g.user, subject_id, request.form))
    return _detail(subject_id, "#programme")


@bp.route("/matieres/<int:subject_id>/documents", methods=("POST",))
@login_required
def upload(subject_id):
    flash_report(subject_service.upload(g.user, subject_id, request.files.getlist("documents")))
    return _detail(subject_id, "#supports")


@bp.route("/matieres/<int:subject_id>/documents/photo", methods=("POST",))
@login_required
def upload_photos(subject_id):
    flash_report(subject_service.upload_photos(g.user, subject_id, request.files.getlist("photos")))
    return _detail(subject_id, "#supports")


@bp.route("/documents/<int:doc_id>")
@login_required
def document_open(doc_id):
    path, mime, name, viewable = subject_service.document_file(g.user["id"], doc_id)
    inline = viewable and not request.args.get("telecharger")
    return send_file(path, mimetype=mime, as_attachment=not inline, download_name=name)


@bp.route("/documents/<int:doc_id>/supprimer", methods=("POST",))
@login_required
def document_delete(doc_id):
    subject_id = subject_service.delete_document(g.user["id"], doc_id)
    flash("Document supprimé.", "success")
    return _detail(subject_id, "#supports")


@bp.route("/matieres/<int:subject_id>/examens", methods=("POST",))
@login_required
def exam_add(subject_id):
    report, _ = exam_service.add(g.user, subject_id, request.form)
    flash_report(report)
    return _detail(subject_id, "#examens")


@bp.route("/matieres/<int:subject_id>/examen/auto", methods=("POST",))
@login_required
def exam_auto(subject_id):
    flash_report(exam_service.follow_agenda(g.user, subject_id))
    return _detail(subject_id)


feature = Feature("matieres", blueprints=[bp])
