"""Dossier scolaire : les matières (créées ou présentes dans l'agenda) et les notes privées de l'utilisateur.

Confidentialité : chaque lecture et chaque écriture passe par l'identifiant de l'utilisateur connecté ;
aucune autre partie de l'application (administration comprise) n'accède aux notes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from backend.business import agenda_analysis, matching
from backend.business import grades as rules
from backend.business.text import date_fr, number_fr, parse_id
from backend.dao import grades as dao
from backend.dao import subjects as subjects_dao

from . import subject_service
from .common import owned
from .report import Report


def courses(user_id) -> list[rules.Course]:
    """Toutes les matières du dossier, notées ou non : d'abord celles que l'utilisateur a créées,
    puis les cours de son agenda qui ne sont pas encore des matières, puis les matières
    disparues dont il reste des notes (historique)."""
    subjects = subject_service.list_for(user_id)
    rows = [rules.Course(s["name"], subject_id=s["id"]) for s in subjects]
    for c in agenda_analysis.agenda_courses(subject_service.recent_events(user_id), subjects):
        if c["existing"] is None and matching.best_match(c["name"], rows, key=lambda r: r.name)[0] is None:
            rows.append(rules.Course(c["name"], from_agenda=True))

    to_link = defaultdict(list)
    for g in dao.list_for_user(user_id):
        row = next((r for r in rows if g["subject_id"] and r.subject_id == g["subject_id"]), None)
        if row is None:
            row, _ = matching.best_match(g["course"], rows, key=lambda r: r.name)
        if row is None:
            row = rules.Course(g["course"])
            rows.append(row)
        if row.subject_id and g["subject_id"] is None:
            to_link[row.subject_id].append(g["id"])  # note saisie avant la création de la matière
        row.grades.append(dict(g))
    for subject_id, ids in to_link.items():
        dao.link_to_subject(user_id, ids, subject_id)
    return rows


def page(user_id) -> dict:
    rows = courses(user_id)
    history = rules.overall_history(rows)
    previous = history[-2]["value"] if len(history) > 1 else None
    current = rules.overall(rows)
    return {
        "courses": rows, "overall": current, "overall_delta": current - previous if previous is not None else None,
        "history": history, "chart": rules.line_chart(history),
        "n_grades": sum(len(c.grades) for c in rows), "n_graded": sum(1 for c in rows if c.grades),
        "sparkline": rules.sparkline_points, "today_iso": date.today().isoformat(),
    }


def add(user_id, form) -> Report:
    course = (form.get("course") or "").strip()[:100]
    if not course:
        return Report().error("Choisis la matière de la note.")
    subject_id = form.get("subject_id") or ""
    subject_id = owned(subjects_dao.get_owned(parse_id(subject_id) or 0, user_id))["id"] if subject_id.isdigit() else None
    data, errors = rules.validate(form)
    if errors:
        report = Report()
        for e in errors:
            report.error(e)
        return report
    dao.create(user_id, subject_id, course, data["value"], data["out_of"], data["coefficient"], data["label"], data["day"])
    return Report().success(f"Note ajoutée en « {course} » : {number_fr(data['value'])}/{number_fr(data['out_of'])}"
                            f" du {date_fr(data['day'])}.")


def delete(user_id, grade_id) -> Report:
    g = owned(dao.get_owned(grade_id, user_id))
    dao.delete(grade_id, user_id)
    return Report().success(f"Note de « {g['course']} » supprimée.")
