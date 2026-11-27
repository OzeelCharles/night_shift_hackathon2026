"""Agenda : import synchronisé de l'emploi du temps, créneaux ajoutés à la main, rattachements."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.business import agenda_analysis as analysis
from app.business import calendar_ics, matching
from app.business.planner import DEFAULT_TOPICS
from app.business.text import bounded_int, iso, parse_date, parse_time
from app.core import events
from app.dao import agenda as dao
from app.dao import sessions as sessions_dao

from . import exam_service, planning_service, subject_service
from .common import owned
from .report import Report


def page(user_id) -> dict:
    now = iso(datetime.now())
    return {"events": dao.upcoming(user_id, now), "revisions": sessions_dao.manual_upcoming(user_id, now),
            "subjects": subject_service.list_for(user_id)}


def _after_change(user_id, reason: str, promote=frozenset(), quiet: bool = False) -> Report:
    """Après tout changement d'agenda : dates d'examen, puis réparation du planning."""
    report = Report().info(*exam_service.sync(user_id, promote))
    report.info(*events.emit(events.AGENDA_CHANGED, user_id=user_id))
    return report + planning_service.update(user_id, reason, repair=True, quiet=quiet)


# ---------------------------------------------------------------- import .ics
class _Linker:
    """Rattache un intitulé importé à une matière (en la créant si demandé), avec mémoire."""

    def __init__(self, user_id, create_missing: bool):
        self.user_id, self.create_missing = user_id, create_missing
        self.known = dao.known_links(user_id)  # corrections manuelles comprises : même intitulé, même matière
        self.subjects = list(subject_service.list_for(user_id))
        self.cache: dict[str, int | None] = {}
        self.created: list[str] = []
        self.created_ids: list[int] = []

    def __call__(self, title: str) -> tuple[int | None, str]:
        if title in self.known:
            return self.known[title]
        exam = analysis.is_exam(title)
        course_like = exam or analysis.is_course(title)
        name = analysis.course_name(title) if exam else title
        key = matching.normalize(name)
        if key not in self.cache:
            match, _ = matching.best_match(name, self.subjects) if course_like else (None, 0.0)
            if match is None and self.create_missing and key and course_like:
                sid = subject_service.create(self.user_id, analysis.course_name(title), topics=list(DEFAULT_TOPICS))
                match = subject_service.get(sid, self.user_id)
                self.subjects.append(match)
                self.created.append(match["name"])
                self.created_ids.append(sid)
            self.cache[key] = match["id"] if match else None
        sid = self.cache[key]
        return sid, analysis.link_kind(title, sid)


def import_ics(user, text: str, sync_mode: bool, create_missing: bool) -> Report:
    """Synchronise l'emploi du temps : seuls les cours ajoutés, déplacés ou supprimés changent.

    sync_mode (« Mettre à jour », recommandé) : les cours disparus du nouveau fichier sont retirés ;
    sinon on ne fait qu'ajouter / corriger, sans rien supprimer.
    """
    uid = user["id"]
    parsed, skipped = calendar_ics.parse_ics(text, datetime.combine(date.today(), time()),
                                             keep_allday=analysis.is_exam)
    # Un même cours répété pour chaque groupe de TD n'est gardé qu'une fois (le plus long).
    unique = analysis.dedupe(parsed, key=lambda e: (matching.normalize(e["title"]) or e["title"], e["start"]))
    duplicates = len(parsed) - len(unique)

    existing: dict[str, list] = defaultdict(list)
    for r in dao.imported_from(uid, date.today().isoformat()):
        existing[r["uid"] or analysis.fallback_key(r["title"], r["start"])].append(r)
    link = _Linker(uid, create_missing)
    added = moved = unchanged = removed = 0
    promote: set[int] = set()
    for e in unique:
        key = e["key"] or analysis.fallback_key(e["title"], iso(e["start"]))
        title, start, end = e["title"][:200], iso(e["start"]), iso(e["end"])
        row = existing[key].pop(0) if existing.get(key) else None
        if row is not None:
            if (row["title"], row["start"], row["end"]) == (title, start, end):
                unchanged += 1
                continue
            # cours déplacé ou renommé : on garde la ligne (et son rattachement, sauf si l'intitulé change)
            sid, kind = (row["subject_id"], row["kind"]) if row["title"] == title else link(title)
            dao.update(row["id"], title, start, end, sid, kind)
            moved += 1
        else:
            sid, kind = link(title)
            dao.create(uid, title, start, end, "ics", kind, sid, uid=key)
            added += 1
        if kind == "examen" and sid:
            promote.add(sid)
    if sync_mode:
        for rows in existing.values():
            for row in rows:
                dao.delete(row["id"], uid)
                removed += 1

    changes = added + moved + removed
    msg = (f"Emploi du temps mis à jour : {added} ajouté(s), {moved} déplacé(s) ou renommé(s), "
           f"{removed} supprimé(s), {unchanged} inchangé(s)." if changes else
           f"Emploi du temps déjà à jour : {unchanged} créneau(x), aucun changement.")
    if link.created:
        msg += f" Nouvelles matières : {', '.join(link.created)}."
    if duplicates:
        msg += f" {duplicates} doublon(s) (même cours répété pour plusieurs groupes) fusionné(s)."
    if skipped:
        msg += f" {skipped} événement(s) « journée entière » ignoré(s)."
    report = Report()
    if link.created_ids:
        report.info(*events.emit(events.SUBJECT_CREATED, user_id=uid, subject_ids=link.created_ids))
    if not changes:
        return report.info(*exam_service.sync(uid, promote)).success(msg + " Ton planning ne bouge pas.")
    return report + _after_change(uid, msg, promote)


# ---------------------------------------------------------------- créneaux ajoutés à la main
def add_slot(user, form) -> Report:
    uid = user["id"]
    d = parse_date(form.get("date"))
    start_t, end_t = parse_time(form.get("start")), parse_time(form.get("end"))
    if d is None or start_t is None or end_t is None:
        return Report().error("Date ou heures invalides.")
    s, e = datetime.combine(d, start_t), datetime.combine(d, end_t)
    if e <= s:
        return Report().error("L'heure de fin doit être après l'heure de début.")
    weeks = bounded_int(form.get("repeat_weeks"), 1, 1, 52)
    kind = form.get("kind") if form.get("kind") in analysis.EVENT_KINDS else "autre"
    title = (form.get("title") or "").strip()[:200]
    slots = [(iso(s + timedelta(weeks=k)), iso(e + timedelta(weeks=k))) for k in range(weeks)]

    if kind == "autre":
        for a, b in slots:
            dao.create(uid, title or "Occupé", a, b, "manuel", "autre")
        return _after_change(uid, f"{weeks} créneau(x) occupé(s) ajouté(s).")

    if not title and not (form.get("subject") or "").isdigit():
        return Report().error("Indique l'intitulé du cours : il sert à retrouver la matière.")
    # pour un examen, on rapproche le nom de la matière sans les mots « partiel », « DS »…
    lookup = analysis.course_name(title) if kind == "examen" and title else title
    subject, status, score = subject_service.resolve(uid, lookup, form.get("subject") or "auto",
                                                     goal="a_jour" if kind == "revision" else "exam")
    title = title or (f"Examen de {subject['name']}" if kind == "examen" else subject["name"])
    report = Report()
    if status == "created":
        report.info(*events.emit(events.SUBJECT_CREATED, user_id=uid, subject_ids=[subject["id"]]))
    if kind in ("cours", "examen"):
        for a, b in slots:
            dao.create(uid, title, a, b, "manuel", kind, subject["id"])
        label = f"{weeks} cours ajouté(s)." if kind == "cours" else "Examen ajouté à ton agenda."
    else:  # créneau de révision choisi par l'utilisateur : une séance fixe, jamais déplacée
        sessions_dao.insert_manual(uid, subject["id"], slots,
                                   "Révision libre" if title == subject["name"] else title,
                                   "Créneau de révision choisi par toi")
        label = f"{weeks} créneau(x) de révision ajouté(s)."
    reason = " ".join(x for x in (analysis.association_message(title, subject["name"], status, score), label) if x)
    # un cours après la dernière séance repousse l'examen par défaut ; une épreuve fixe la date
    return report + _after_change(uid, reason, {subject["id"]} if kind == "examen" else frozenset())


def link(user, event_id, choice: str) -> Report:
    """Corrige le rattachement d'un cours (toutes ses occurrences)."""
    uid = user["id"]
    ev = owned(dao.get_owned(event_id, uid))
    sid = subject_service.get(int(choice), uid)["id"] if choice.isdigit() else None
    kind = ("examen" if analysis.is_exam(ev["title"]) else "cours") if sid else "autre"
    dao.link_same_title(uid, ev["title"], sid, kind)
    report = Report().success("Rattachement mis à jour pour toutes les occurrences de ce cours.")
    return report + _after_change(uid, "", {sid} if sid and kind == "examen" else frozenset(), quiet=True)


def delete_event(user, event_id) -> Report:
    dao.delete(event_id, user["id"])
    return _after_change(user["id"], "Créneau supprimé.")


def delete_revision(user, session_id) -> Report:
    sessions_dao.delete_manual(session_id, user["id"])
    return planning_service.update(user["id"], "Créneau de révision supprimé.", repair=True)


def clear(user) -> Report:
    dao.delete_all(user["id"])
    report = Report().info(*events.emit(events.AGENDA_CHANGED, user_id=user["id"]))
    return report + planning_service.update(user["id"], "Agenda vidé.")
