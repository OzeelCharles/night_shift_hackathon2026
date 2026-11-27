"""Dates d'examen : lecture dans l'emploi du temps, synchronisation, examens ajoutés à la main."""
from datetime import date, datetime

from app.business import exams
from app.business.text import date_fr, iso, parse_date, parse_time
from app.dao import agenda as agenda_dao
from app.dao import subjects as subjects_dao

from . import planning_service
from .common import owned
from .report import Report


def from_agenda(subject_id) -> tuple[str | None, str | None]:
    """Date que donnerait l'emploi du temps : la prochaine épreuve rattachée, sinon (par défaut)
    la dernière séance de cours à venir. Retourne (date ISO, source)."""
    today = date.today().isoformat()
    exam = agenda_dao.next_exam_day(subject_id, today)
    if exam:
        return exam, "agenda"
    last = agenda_dao.last_class_day(subject_id, today)
    return (last, "derniere_seance") if last else (None, None)


def sync(user_id, promote=frozenset()) -> list[str]:
    """Recalcule la date d'examen de chaque matière « examen » et explique les changements.
    `promote` : matières qui viennent de recevoir une épreuve et passent à l'objectif « examen »
    si elles étaient « être à jour »."""
    today = date.today().isoformat()
    messages = []
    for s in subjects_dao.list_for_user(user_id):
        agenda_date, agenda_src = from_agenda(s["id"])
        goal = "exam" if s["goal"] != "exam" and s["id"] in promote and agenda_src == "agenda" else s["goal"]
        chosen = exams.choose(s["exam_manual"], agenda_date, agenda_src, today) if goal == "exam" else None
        if chosen is None or (*chosen, goal) == (s["exam_date"], s["exam_source"], s["goal"]):
            continue
        new, src = chosen
        subjects_dao.update(s["id"], goal=goal, exam_date=new, exam_source=src)
        if msg := exams.change_message(s["name"], new, src, s["exam_date"], s["exam_source"]):
            messages.append(msg)
    return messages


def agenda_proposal(subject) -> tuple[date, dict] | tuple[None, None]:
    """Date de l'emploi du temps à proposer quand elle diffère de la date retenue à la main."""
    agenda_date, src = from_agenda(subject["id"])
    if not agenda_date or agenda_date == subject["exam_date"]:
        return None, None
    if subject["exam_source"] != "manuel" and subject["goal"] == "exam":
        return None, None
    title = "dernière séance de cours" if src == "derniere_seance" else agenda_dao.exam_title_on(subject["id"], agenda_date)
    return date.fromisoformat(agenda_date), {"title": title, "source": src}


def add(user, subject_id, form) -> tuple[Report, bool]:
    """Ajoute un examen à la main (en plus de ceux de l'emploi du temps)."""
    s = owned(subjects_dao.get_owned(subject_id, user["id"]))
    report = Report()
    d = parse_date(form.get("date"))
    start_t, end_t = parse_time(form.get("start") or "08:00"), parse_time(form.get("end") or "10:00")
    if d is None or start_t is None or end_t is None:
        return report.error("Date ou heures de l'examen invalides."), False
    if d <= date.today():
        return report.error("L'examen doit être à une date à venir."), False
    if end_t <= start_t:
        return report.error("L'heure de fin doit être après l'heure de début."), False
    title = (form.get("title") or "").strip()[:200] or f"Examen de {s['name']}"
    agenda_dao.create(user["id"], title, iso(datetime.combine(d, start_t)), iso(datetime.combine(d, end_t)),
                      "manuel", "examen", subject_id)
    report.info(*sync(user["id"], {subject_id}))
    report += planning_service.update(user["id"], f"Examen ajouté le {date_fr(d)}.", repair=True)
    return report, True


def follow_agenda(user, subject_id) -> Report:
    """Abandonne la date choisie à la main : l'emploi du temps décide de nouveau."""
    s = owned(subjects_dao.get_owned(subject_id, user["id"]))
    subjects_dao.update(subject_id, exam_manual=None, goal="exam")
    report = Report().info(*(sync(user["id"]) or
                             [f"La date d'examen de « {s['name']} » suit maintenant ton emploi du temps."]))
    report += planning_service.update(user["id"], f"Programme de « {s['name']} » recalculé.", only={subject_id})
    return report
