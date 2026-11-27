"""Routes de l'atelier de révision."""
from flask import Blueprint, g, redirect, render_template, request, url_for

from backend.controllers import flash_report
from backend.core.security import login_required

from . import generator as gen
from . import service

bp = Blueprint("revision", __name__, template_folder="templates")


def _hub(subject_id, anchor=""):
    return redirect(url_for("revision.hub", subject_id=subject_id) + anchor)


@bp.route("/reviser")
@login_required
def index():
    return render_template("revision/index.html", rows=service.overview(g.user), difficulties=gen.DIFFICULTIES)


@bp.route("/reviser/<int:subject_id>")
@login_required
def hub(subject_id):
    context = service.hub(g.user, subject_id)
    only = request.args.get("exos", context["difficulty"])
    return render_template("revision/hub.html", **context, only=only,
                           exercises_by_chapter=service.exercises_grouped(subject_id, only),
                           difficulties=gen.DIFFICULTIES, help=gen.DIFFICULTY_HELP, settings=gen.SETTINGS)


@bp.route("/reviser/<int:subject_id>/niveau", methods=("POST",))
@login_required
def set_level(subject_id):
    s = service.subject(g.user["id"], subject_id)
    flash_report(service.set_levels(s, request.form))
    return _hub(subject_id)


@bp.route("/reviser/<int:subject_id>/fiches")
@login_required
def sheets(subject_id):
    s = service.subject(g.user["id"], subject_id)
    if "" not in service.levels(subject_id):
        return _hub(subject_id, "#niveau")  # le niveau est demandé avant toute révision
    chapter = request.args.get("chapitre", "")
    built, lv, origin = service.sheets(s, chapter)
    return render_template("revision/sheets.html", s=s, sheets=built, chapter=chapter, origin=origin,
                           difficulties=gen.DIFFICULTIES)


@bp.route("/reviser/<int:subject_id>/qcm", methods=("POST",))
@login_required
def new_quiz(subject_id):
    s = service.subject(g.user["id"], subject_id)
    if "" not in service.levels(subject_id):
        return _hub(subject_id, "#niveau")
    quiz_id, report = service.create_quiz(g.user, s, request.form.get("chapitre", ""),
                                          request.form.get("difficulte", ""))
    flash_report(report)
    return redirect(url_for("revision.quiz", quiz_id=quiz_id)) if quiz_id else _hub(subject_id, "#qcm")


@bp.route("/reviser/qcm/<int:quiz_id>", methods=("GET", "POST"))
@login_required
def quiz(quiz_id):
    if request.method == "POST":
        _, report = service.submit(g.user, quiz_id, request.form)
        flash_report(report)
        return redirect(url_for("revision.quiz", quiz_id=quiz_id))
    return render_template("revision/quiz.html", q=service.quiz(g.user, quiz_id), difficulties=gen.DIFFICULTIES)


@bp.route("/reviser/<int:subject_id>/exercices", methods=("POST",))
@login_required
def collect(subject_id):
    s = service.subject(g.user["id"], subject_id)
    flash_report(service.collect_exercises(g.user, s, request.form.get("chapitre", ""),
                                           request.form.get("difficulte", "")))
    return _hub(subject_id, "#exercices")


@bp.route("/reviser/exercices/<int:exercise_id>/supprimer", methods=("POST",))
@login_required
def delete_exercise(exercise_id):
    return _hub(service.delete_exercise(g.user, exercise_id), "#exercices")
