"""Planning de révision : calcul, réparation après un changement d'agenda, vues, export."""
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from backend.business import agenda_analysis, calendar_ics, matching, planner
from backend.business.subjects import topics_of
from backend.business.text import iso
from backend.core import events
from backend.dao import agenda as agenda_dao
from backend.dao import sessions as sessions_dao
from backend.dao import subjects as subjects_dao

from . import preference_service
from .report import Report


def _busy(rows):
    return [planner.Busy(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])) for r in rows]


def _conflicts(start: datetime, end: datetime, busy, prefs, exam_day: date | None) -> bool:
    """Une séance déjà planifiée est-elle devenue impossible (nouveau cours, examen avancé…) ?"""
    if start.weekday() in prefs.rest_days:
        return True
    if start.time() < prefs.day_start or end.time() > prefs.day_end or start.date() != end.date():
        return True
    if exam_day and start.date() >= exam_day:
        return True
    pad = timedelta(minutes=prefs.buffer_minutes)
    return any(start < b.end + pad and b.start - pad < end for b in busy)


def regenerate(user_id, only: set[int] | None = None, repair: bool = False,
               not_before: datetime | None = None, daily_max: int | None = None):
    """Recalcule le planning à partir de l'état actuel (préférences, agenda, matières).

    `only` : ne replanifie que ces matières ; les séances des autres restent en place et sont
    traitées comme des créneaux occupés. Les créneaux de révision posés à la main (manual = 1) ne
    sont jamais déplacés. Retourne None s'il n'y a rien à planifier.

    `repair` (après un changement d'emploi du temps) : les séances encore possibles restent à leur
    place ; seules celles qui chevauchent un nouveau cours, tombent un jour de repos ou après
    l'examen sont replacées, avec le travail qui manquerait.

    `not_before` : rien n'est placé avant cette date (ex. demain, après une séance ratée aujourd'hui) ;
    `daily_max` : temps maximum de travail par jour à utiliser à la place de celui des préférences.
    """
    now = max(datetime.now(), not_before) if not_before else datetime.now()
    prefs, prow = preference_service.load(user_id)
    if daily_max:
        prefs = replace(prefs, daily_max_minutes=daily_max)
    rows = subjects_dao.list_for_user(user_id)
    comfort = {r["id"]: r["comfort"] for r in rows}
    if only is not None:
        rows = [r for r in rows if r["id"] in only]
    if not rows:
        return None
    subjects = [planner.SubjectIn(
        id=r["id"], name=r["name"], goal=r["goal"], comfort=r["comfort"],
        exam_date=date.fromisoformat(r["exam_date"]) if r["exam_date"] else None,
        weekly_hours=r["weekly_hours"], topics=topics_of(r)) for r in rows]

    # Chapitres déjà étudiés (séances d'étude cochées) : on ne les replanifie pas.
    done_study = defaultdict(dict)
    for d in sessions_dao.done_study_minutes(user_id):
        if d["minutes"] + 1 >= planner.study_minutes(comfort.get(d["subject_id"], 3)):
            done_study[d["subject_id"]][d["topic"]] = date.fromisoformat(d["last"])

    ids = [r["id"] for r in rows]
    # Les vacances et jours fériés de l'emploi du temps sont des jours libres pour réviser.
    event_busy = _busy(e for e in agenda_dao.busy_from(user_id, iso(now))
                       if not agenda_analysis.is_holiday(e["title"]))
    fixed: list[planner.PlannedSession] = []
    conflicting = 0
    if repair:
        locked = _busy(sessions_dao.locked_from(user_id, iso(now)))
        exam_days = {s.id: s.exam_date for s in subjects if s.goal == "exam"}
        to_delete = []
        for s in sessions_dao.replannable(user_id, iso(now), ids):
            start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
            if _conflicts(start, end, event_busy + locked, prefs, exam_days.get(s["subject_id"])):
                to_delete.append(s["id"])
            else:
                fixed.append(planner.PlannedSession(s["subject_id"], start, end, s["topic"], s["kind"],
                                                    s["activity"], ref=s["id"]))
        conflicting = len(to_delete)
        sessions_dao.delete_ids(to_delete)
    else:
        sessions_dao.delete_replannable(user_id, iso(now), ids)
    kept_ids = {f.ref for f in fixed}
    busy = event_busy + _busy(s for s in sessions_dao.future(user_id, iso(now)) if s["id"] not in kept_ids)

    result = planner.generate_plan(subjects, prefs, busy, now, horizon_days=prow["horizon_weeks"] * 7,
                                   done_study=done_study, fixed=fixed)
    sessions_dao.delete_ids(f.ref for f in result.dropped)
    sessions_dao.insert_planned(user_id, [
        {"subject_id": s.subject_id, "start": iso(s.start), "end": iso(s.end), "topic": s.topic, "kind": s.kind,
         "activity": s.activity} for s in result.sessions])
    result.kept = len(fixed) - len(result.dropped)
    result.replaced = conflicting + len(result.dropped)
    events.emit(events.PLAN_UPDATED, user_id=user_id)
    return result


def update(user_id, reason: str = "", only: set[int] | None = None, repair: bool = False,
           quiet: bool = False, not_before: datetime | None = None) -> Report:
    """Recalcule (ou répare) le planning et explique le résultat (retour immédiat : loi de Doherty)."""
    report = Report()
    result = regenerate(user_id, only, repair, not_before=not_before)
    if result is None:
        return report.success(reason)
    planned = sum(int((s.end - s.start).total_seconds() // 60) for s in result.sessions)
    if repair:
        if not result.sessions and not result.replaced:
            if not quiet:
                report.success(f"{reason} Ton planning n'a pas besoin de bouger.".strip())
        else:
            report.success(f"{reason} Planning ajusté : {result.kept} séance(s) gardée(s) à leur place, "
                           f"{len(result.sessions)} replacée(s) ou ajoutée(s).".strip())
    else:
        report.success(f"{reason} Planning mis à jour : {len(result.sessions)} séances, "
                       f"{planner.fmt_minutes(planned)} de travail.".strip())
    for w in result.warnings:
        report.warning(w)
    return report


def has_subjects(user_id) -> bool:
    return bool(subjects_dao.list_for_user(user_id))


# ---------------------------------------------------------------- vues
def dashboard(user_id) -> dict:
    today = date.today()
    return {
        "today_sessions": sessions_dao.between(user_id, today.isoformat(), (today + timedelta(days=1)).isoformat()),
        "upcoming": sessions_dao.upcoming(user_id, (today + timedelta(days=1)).isoformat(), 6),
        "subjects": subjects_dao.with_progress(user_id, iso(datetime.now())),
        "today": today, "now": iso(datetime.now()),
    }


def week(user_id, ref: date) -> dict:
    """Grille de la semaine : blocs positionnés en pourcentage de la plage horaire affichée."""
    monday = ref - timedelta(days=ref.weekday())
    end = monday + timedelta(days=7)
    now = datetime.now()
    prefs, _ = preference_service.load(user_id)
    sessions = sessions_dao.between(user_id, monday.isoformat(), end.isoformat())
    evts = agenda_analysis.dedupe(agenda_dao.in_window(user_id, monday.isoformat(), end.isoformat()))

    # Plage horaire affichée : tes heures de travail, élargies si un événement déborde.
    lo, hi = prefs.day_start.hour, prefs.day_end.hour + (1 if prefs.day_end.minute else 0)
    for r in list(sessions) + evts:
        s, e = datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])
        if s.date() == e.date():
            lo, hi = min(lo, s.hour), max(hi, e.hour + (1 if e.minute else 0))
    lo, hi = max(0, lo), min(24, max(hi, lo + 1))
    g_min, g_len = lo * 60, (hi - lo) * 60

    def pct(minutes):
        return round((min(max(minutes, g_min), g_min + g_len) - g_min) / g_len * 100, 3)

    def block(kind, row, d):
        day0 = datetime.combine(d, time())
        sm = max(0, (datetime.fromisoformat(row["start"]) - day0).total_seconds() / 60)
        em = min(1440, (datetime.fromisoformat(row["end"]) - day0).total_seconds() / 60)
        top, bottom = pct(sm), pct(em)
        return {"type": kind, "row": row, "top": top, "height": bottom - top, "short": (em - sm) < 60} \
            if bottom > top else None

    def minutes(rows):
        return sum(int((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds() // 60)
                   for s in rows)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        ds = d.isoformat()
        day_events = [e for e in evts if e["start"][:10] <= ds <= e["end"][:10]]
        day_sessions = [s for s in sessions if s["start"][:10] == ds]
        blocks = [block("event", e, d) for e in day_events if not agenda_analysis.is_holiday(e["title"])]
        blocks += [block("session", s, d) for s in day_sessions]
        days.append({
            "date": d, "is_today": d == date.today(), "is_rest": d.weekday() in prefs.rest_days,
            "is_past": d < date.today(), "blocks": [b for b in blocks if b], "minutes": minutes(day_sessions),
            "holidays": sorted({matching.clean_title(e["title"]) for e in day_events
                                if agenda_analysis.is_holiday(e["title"])}),
            "work_top": pct(prefs.day_start.hour * 60 + prefs.day_start.minute),
            "work_bottom": pct(prefs.day_end.hour * 60 + prefs.day_end.minute),
        })
    now_min = now.hour * 60 + now.minute
    return {
        "days": days, "monday": monday, "prev": monday - timedelta(days=7), "next": end, "today": date.today(),
        "subjects": subjects_dao.list_for_user(user_id), "default_exam_events": agenda_dao.default_exam_ids(user_id),
        "hours": list(range(lo, hi)), "prefs": prefs, "week_minutes": sum(d["minutes"] for d in days),
        "now_top": pct(now_min) if monday <= now.date() < end and lo * 60 <= now_min < hi * 60 else None,
        "now": iso(now),
    }


def toggle(session_id, user_id) -> None:
    sessions_dao.toggle_done(session_id, user_id)


def export_ics(user_id) -> str:
    rows = sessions_dao.from_date(user_id, (date.today() - timedelta(days=7)).isoformat())
    return calendar_ics.build_ics([{
        "uid": f"session-{r['id']}@planificateur-revisions",
        "title": f"{r['subject_name']} : {r['topic']}",
        "description": f"{planner.KIND_LABELS.get(r['kind'], r['kind'])} - {r['activity']}",
        "start": datetime.fromisoformat(r["start"]), "end": datetime.fromisoformat(r["end"]),
    } for r in rows])
