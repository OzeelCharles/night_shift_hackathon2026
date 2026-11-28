"""Agenda : import synchronisé de l'emploi du temps, créneaux ajoutés à la main, rattachements."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from backend.business import agenda_analysis as analysis
from backend.business import calendar_ics, matching, timetable_photo, uploads
from backend.business.planner import DEFAULT_TOPICS
from backend.business.text import bounded_int, iso, parse_date, parse_time
from backend.core import events
from backend.dao import agenda as dao
from backend.dao import sessions as sessions_dao
from backend.dao import vision

from . import exam_service, planning_service, subject_service
from .common import owned
from .report import Report


def page(user_id) -> dict:
    now = iso(datetime.now())
    return {"events": dao.upcoming(user_id, now), "revisions": sessions_dao.manual_upcoming(user_id, now),
            "subjects": subject_service.list_for(user_id), "photo_enabled": photo_enabled()}


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


def import_ics(user, data: bytes | str, sync_mode: bool, create_missing: bool) -> Report:
    """Synchronise l'emploi du temps : seuls les cours ajoutés, déplacés ou supprimés changent.

    sync_mode (« Mettre à jour », recommandé) : les cours disparus du nouveau fichier sont retirés ;
    sinon on ne fait qu'ajouter / corriger, sans rien supprimer.
    Fichier de 2 Mo au plus ; 5 000 créneaux au plus (SEC-06).
    """
    uid = user["id"]
    if len(data) > calendar_ics.MAX_BYTES:
        return Report().error(f"Fichier .ics trop volumineux ({calendar_ics.MAX_BYTES // (1024 * 1024)} Mo maximum).")
    text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
    parsed, skipped = calendar_ics.parse_ics(text, datetime.combine(date.today(), time()),
                                             keep_allday=analysis.is_exam)
    truncated = len(parsed) >= calendar_ics.MAX_EVENTS
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
        msg += f" {skipped} événement(s) « journée entière » ou illisible(s) ignoré(s)."
    if truncated:
        msg += f" Seuls les {calendar_ics.MAX_EVENTS} premiers créneaux ont été lus."
    report = Report()
    if link.created_ids:
        report.info(*events.emit(events.SUBJECT_CREATED, user_id=uid, subject_ids=link.created_ids))
    if not changes:
        return report.info(*exam_service.sync(uid, promote)).success(msg + " Ton planning ne bouge pas.")
    return report + _after_change(uid, msg, promote)


# ---------------------------------------------------------------- import d'une photo
PHOTO_READ_LIMIT = uploads.MAX_PHOTO_BYTES + 1  # lecture bornée : un octet de plus suffit à savoir que c'est trop
def photo_enabled() -> bool:
    return vision.available()


def import_photo(user, image: bytes, weeks: int, create_missing: bool) -> Report:
    """Lit une photo d'emploi du temps avec Groq et ajoute ses créneaux (sans doublon avec l'agenda)."""
    uid = user["id"]
    if not vision.available():
        return Report().error("La lecture de photos n'est pas disponible : il manque une clé Groq sur ce serveur.")
    if len(image) > uploads.MAX_PHOTO_BYTES:  # SEC-06 : vérifié avant l'appel au modèle
        return Report().error(f"Photo trop lourde ({uploads.MAX_PHOTO_BYTES // (1024 * 1024)} Mo maximum).")
    try:
        answer = vision.ask(image, timetable_photo.PROMPT)
    except vision.VisionError as e:
        return Report().error(f"Photo non lue : {e}")
    found, skipped = timetable_photo.slots(answer.get("events") if isinstance(answer, dict) else None,
                                           date.today(), weeks)
    if not found:
        return Report().warning("Aucun créneau lisible sur cette photo. Essaie une photo plus nette, bien cadrée.")
    existing = {(r["title"], r["start"]) for r in dao.upcoming(uid, date.today().isoformat(), limit=5000)}
    link = _Linker(uid, create_missing)
    added = 0
    promote: set[int] = set()
    for e in found:
        title = e["title"] if not e["exam"] or analysis.is_exam(e["title"]) else f"Examen {e['title']}"[:200]
        start, end = iso(e["start"]), iso(e["end"])
        if (title, start) in existing:
            continue
        sid, kind = link(title)
        dao.create(uid, title, start, end, "photo", kind, sid, uid=analysis.fallback_key(title, start))
        existing.add((title, start))
        added += 1
        if kind == "examen" and sid:
            promote.add(sid)
    report = Report()
    if link.created_ids:
        report.info(*events.emit(events.SUBJECT_CREATED, user_id=uid, subject_ids=link.created_ids))
    if not added:
        return report.info("Tous les créneaux de cette photo sont déjà dans ton agenda.")
    msg = f"Photo lue par Groq : {added} créneau(x) ajouté(s)."
    if link.created:
        msg += f" Nouvelles matières : {', '.join(link.created)}."
    if skipped:
        msg += f" {skipped} élément(s) illisible(s) ignoré(s)."
    return report + _after_change(uid, msg + " Vérifie-les ci-dessous.", promote)


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
    owned(dao.get_owned(event_id, user["id"]))  # 404 si le créneau n'est pas à l'utilisateur
    dao.delete(event_id, user["id"])
    return _after_change(user["id"], "Créneau supprimé.")


def delete_revision(user, session_id) -> Report:
    owned(sessions_dao.get_owned(session_id, user["id"], manual_only=True))
    sessions_dao.delete_manual(session_id, user["id"])
    return planning_service.update(user["id"], "Créneau de révision supprimé.", repair=True)


def clear(user) -> Report:
    dao.delete_all(user["id"])
    report = Report().info(*events.emit(events.AGENDA_CHANGED, user_id=user["id"]))
    return report + planning_service.update(user["id"], "Agenda vidé.")
