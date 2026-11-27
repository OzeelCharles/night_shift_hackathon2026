"""Matières : création (avec rapprochement des noms et lecture de l'agenda), modification,
supports de cours, page de détail."""
from __future__ import annotations

import mimetypes
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from flask import current_app
from werkzeug.utils import secure_filename

from backend.business import agenda_analysis, exams, extractor, matching
from backend.business import subjects as rules
from backend.business.planner import DEFAULT_TOPICS
from backend.business.text import bounded_float, bounded_int, date_fr, iso, parse_date
from backend.core import events
from backend.dao import agenda as agenda_dao
from backend.dao import documents as documents_dao
from backend.dao import sessions as sessions_dao
from backend.dao import subjects as dao
from backend.dao import vision

from . import exam_service, planning_service, resource_service
from .common import owned
from .report import Report


@dataclass
class FormResult:
    """Issue d'un formulaire : la matière concernée, ou le formulaire à réafficher avec ses erreurs."""
    subject_id: int | None
    report: Report = field(default_factory=Report)
    form: dict | None = None
    merged_from: str | None = None  # nom saisi, quand il a été rangé dans une matière existante


def get(subject_id, user_id):
    return owned(dao.get_owned(subject_id, user_id))


def list_for(user_id):
    return dao.list_for_user(user_id)


def recent_events(user_id) -> list[dict]:
    return agenda_dao.since(user_id, (date.today() - timedelta(days=60)).isoformat())


def initial_topics(name: str) -> tuple[list[str], str]:
    """Chapitres d'une matière créée sans support : plan Wikipédia, sinon plan générique."""
    topics = resource_service.outline(name)
    if topics:
        return topics, "proposés à partir de Wikipédia"
    return list(DEFAULT_TOPICS), "génériques (ajoute tes chapitres pour un planning plus précis)"


def create(user_id, name: str, goal="exam", exam_date=None, comfort=3, weekly_hours=2.0, topics=None,
           exam_manual=None, exam_source="") -> int:
    """Enregistre une matière. Par défaut (matière née de l'agenda) l'objectif est l'examen, dont
    la date sera lue dans l'emploi du temps (épreuve, sinon dernière séance)."""
    if topics is None:
        topics, _ = initial_topics(name)
    return dao.create(user_id, name=name[:100], goal=goal, exam_date=exam_date, comfort=comfort,
                      weekly_hours=weekly_hours, topics=rules.dump_topics(topics), exam_manual=exam_manual,
                      exam_source=exam_source)


def resolve(user_id, title: str, explicit: str | None = "auto", goal: str = "exam"):
    """Associe un intitulé (cours, créneau) à une matière, ou en crée une s'il n'y en a aucune proche.
    explicit : id choisi par l'utilisateur, ou « auto ». Retourne (matière, statut, score) ;
    statut ∈ explicit / matched / created."""
    if explicit and explicit.isdigit():
        return get(int(explicit), user_id), "explicit", 1.0
    match, score = matching.best_match(title, list_for(user_id))
    if match is not None:
        return match, "matched", score
    sid = create(user_id, matching.clean_title(title) or title, goal=goal)
    return get(sid, user_id), "created", score


# ---------------------------------------------------------------- supports de cours
def _upload_folder(user_id) -> str:
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(user_id))
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


def save_documents(user_id, subject_id, files) -> tuple[list[str], list[str]]:
    """Enregistre et analyse les fichiers. Retourne (chapitres trouvés, messages)."""
    topics, messages = [], []
    for f in files:
        if not f or not f.filename:
            continue
        if not extractor.allowed_file(f.filename):
            messages.append(f"« {f.filename} » : format non pris en charge.")
            continue
        path = os.path.join(_upload_folder(user_id), f"{uuid.uuid4().hex}_{secure_filename(f.filename) or 'document'}")
        f.save(path)
        text, found = extractor.extract(path)
        if not found and text:
            found = [k.capitalize() for k in extractor.keywords(text, 6)]
        topics += found
        documents_dao.create(subject_id, f.filename[:200], path, text[:5000], len(found))
        messages.append(f"« {f.filename} » : {len(found)} chapitre(s) détecté(s)."
                        if text else f"« {f.filename} » : texte illisible (document scanné ?).")
    return topics, messages


NOTES_PROMPT = (
    "Cette photo montre une page de cours (notes manuscrites, polycopié, diapositive ou tableau). "
    "Recopie fidèlement tout son contenu en Markdown, en français, sans rien inventer ni résumer :\n"
    "- les titres de chapitre ou de partie en lignes « # Titre » (sous-parties « ## ») ;\n"
    "- les formules en LaTeX ($…$ en ligne, $$…$$ isolées) ;\n"
    "- les schémas décrits en une phrase entre crochets.\n"
    "Réponds uniquement par la transcription. Si la photo ne contient aucun texte lisible, réponds « ILLISIBLE »."
)


def photo_enabled() -> bool:
    return vision.available()


def _note_name(filename: str, n: int) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0].strip() or f"photo {n}"
    return f"{stem} (photo).md"


def save_photos(user_id, subject_id, files) -> tuple[list[str], list[str]]:
    """Fait lire chaque photo par Groq et l'enregistre comme support en Markdown. Retourne (chapitres, messages)."""
    topics, messages = [], []
    for n, f in enumerate([f for f in files if f and f.filename], 1):
        if not (f.mimetype or "").startswith("image/"):
            messages.append(f"« {f.filename} » : ce n'est pas une photo.")
            continue
        try:
            text = vision.ask(f.read(), NOTES_PROMPT, as_json=False)
        except vision.VisionError as e:
            messages.append(f"« {f.filename} » : photo non lue ({e})")
            continue
        if len(text) < 20 or text.upper().startswith("ILLISIBLE"):
            messages.append(f"« {f.filename} » : aucun texte lisible sur la photo.")
            continue
        name = _note_name(f.filename, n)
        path = os.path.join(_upload_folder(user_id), f"{uuid.uuid4().hex}_{secure_filename(name) or 'photo.md'}")
        with open(path, "w", encoding="utf-8") as out:
            out.write(text + "\n")
        extracted, found = extractor.extract(path)
        if not found and extracted:
            found = [k.capitalize() for k in extractor.keywords(extracted, 6)]
        topics += found
        documents_dao.create(subject_id, name[:200], path, (extracted or text)[:5000], len(found))
        messages.append(f"« {f.filename} » lue par Groq : {len(found)} chapitre(s) détecté(s).")
    return topics, messages


def upload_photos(user, subject_id, files) -> Report:
    s = get(subject_id, user["id"])
    if not vision.available():
        return Report().error("La lecture de photos n'est pas disponible : il manque une clé Groq sur ce serveur.")
    found, messages = save_photos(user["id"], subject_id, files)
    return _merge_found(user, s, found, messages)


def upload(user, subject_id, files) -> Report:
    s = get(subject_id, user["id"])
    found, messages = save_documents(user["id"], subject_id, files)
    return _merge_found(user, s, found, messages)


def _merge_found(user, s, found, messages) -> Report:
    subject_id = s["id"]
    current = rules.topics_of(s)
    merged = rules.merge_topics(current, extractor.clean_topics(found, 40))
    added = len(merged) - len([t for t in current if t not in DEFAULT_TOPICS])
    report = Report().info(*messages)
    if added <= 0:
        return report.info("Aucun nouveau chapitre trouvé dans ces documents.")
    dao.update(subject_id, topics=rules.dump_topics(merged))
    return report + planning_service.update(user["id"], f"{added} nouveau(x) chapitre(s) ajouté(s).")


def document_file(user_id, doc_id) -> tuple[str, str, str, bool]:
    """(chemin, type MIME, nom, affichable dans le navigateur) d'un document de l'utilisateur."""
    d = owned(documents_dao.get_owned(doc_id, user_id))
    real = owned(_inside_uploads(d["stored_path"]))
    mime = mimetypes.guess_type(d["filename"])[0] or "application/octet-stream"
    return real, mime, d["filename"], mime in ("application/pdf", "text/plain", "text/markdown")


def delete_document(user_id, doc_id) -> int:
    d = owned(documents_dao.get_owned(doc_id, user_id))
    remove_files([d["stored_path"]])
    documents_dao.delete(doc_id)
    return d["subject_id"]


# ---------------------------------------------------------------- création / modification
def _link_agenda(user_id, subject_id, name) -> int:
    """Rattache à la nouvelle matière les cours et épreuves de l'agenda au nom proche."""
    linked = 0
    for e in recent_events(user_id):
        exam_ev = agenda_analysis.is_exam(e["title"])
        if e["subject_id"] is None and not (e["kind"] == "autre" and e["source"] == "manuel") \
                and (exam_ev or agenda_analysis.is_course(e["title"])) \
                and matching.similarity(name, agenda_analysis.course_name(e["title"])) >= matching.THRESHOLD:
            agenda_dao.link(e["id"], subject_id, "examen" if exam_ev else "cours")
            linked += 1
    return linked


def _exam_from_agenda(user_id, name: str) -> tuple[str | None, str, str | None]:
    """Objectif examen sans date saisie : l'épreuve de l'emploi du temps, sinon la dernière séance.
    Retourne (date, source, message)."""
    evts = recent_events(user_id)
    found, ev = agenda_analysis.detect_exam_date(name, evts)
    if found:
        return found.isoformat(), "agenda", (f"Date d'examen lue dans ton emploi du temps (« {ev['title']} ») : "
                                             f"{date_fr(found)}.")
    last = agenda_analysis.last_course_date(name, evts)
    if last:
        return last.isoformat(), "derniere_seance", (f"Aucune épreuve dans ton emploi du temps : l'examen est fixé "
                                                    f"par défaut à la dernière séance de cours, le {date_fr(last)}.")
    return None, "", None


def create_from_form(user, form: dict, files, force_new: bool) -> FormResult:
    uid = user["id"]
    typed_exam = (form.get("exam_date") or "").strip() or None  # date choisie : prioritaire
    exam_source, exam_note = ("manuel" if typed_exam else ""), None
    if form.get("goal") == "exam" and not typed_exam and form.get("name", "").strip():
        found, exam_source, exam_note = _exam_from_agenda(uid, form["name"])
        if found:
            form["exam_date"] = found
    data, errors = rules.validate(form)
    if errors:
        report = Report()
        for e in errors:
            report.error(e)
        if rules.MISSING_EXAM_DATE in errors:
            report.info("Ni épreuve ni cours à ce nom dans ton emploi du temps : saisis la date, "
                        "ou choisis « Être à jour dans le cours ».")
        return FormResult(None, report, form=form)

    # Le nom ressemble-t-il à une matière existante ? (similarité cosinus, voir business/matching.py)
    match, score = (None, 0.0) if force_new else matching.best_match(data["name"], list_for(uid))
    if match is not None:
        doc_topics, messages = save_documents(uid, match["id"], files)
        new_topics = data["topics"] + extractor.clean_topics(doc_topics, limit=40)
        if new_topics:
            dao.update(match["id"], topics=rules.dump_topics(rules.merge_topics(rules.topics_of(match), new_topics)))
        report = Report().info(*messages)
        report += planning_service.update(uid, f"« {data['name']} » correspond à ta matière « {match['name']} » "
                                               f"(similarité {score:.0%}) : tout a été ajouté à cette matière.")
        return FormResult(match["id"], report, merged_from=data["name"])

    is_exam_goal = data["goal"] == "exam"
    sid = create(uid, data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"],
                 topics=data["topics"] or [], exam_manual=typed_exam if is_exam_goal else None,
                 exam_source=exam_source if is_exam_goal else "")
    report = Report()
    sync_messages = []
    if linked := _link_agenda(uid, sid, data["name"]):
        report.info(f"{linked} créneau(x) de ton agenda rattaché(s) à « {data['name']} ».")
        sync_messages = exam_service.sync(uid)
    if not any(f"« {data['name']} »" in m for m in sync_messages):
        report.info(exam_note)  # sinon le message de synchronisation, plus précis, suffit
    report.info(*sync_messages)

    doc_topics, messages = save_documents(uid, sid, files)
    topics = data["topics"] or extractor.clean_topics(doc_topics, limit=40)
    source = "saisis" if data["topics"] else "extraits des documents"
    if not topics:
        topics, source = initial_topics(data["name"])
    dao.update(sid, topics=rules.dump_topics(topics))
    report.info(*messages)
    report.info(*events.emit(events.SUBJECT_CREATED, user_id=uid, subject_ids=[sid]))
    report += planning_service.update(uid, f"Matière créée avec {len(topics)} chapitres {source}.")
    return FormResult(sid, report)


def update_from_form(user, subject_id, form: dict) -> FormResult:
    uid = user["id"]
    s = get(subject_id, uid)
    manual, typed = exams.typed_date(form, s)
    source = "manuel" if manual else s["exam_source"]
    if form.get("goal") == "exam" and not typed:
        derived, derived_src = exam_service.from_agenda(subject_id)  # épreuve, sinon dernière séance
        if derived:
            form["exam_date"], source = derived, derived_src
    data, errors = rules.validate(form)
    if errors:
        report = Report()
        for e in errors:
            report.error(e)
        return FormResult(subject_id, report, form=form)
    twin, score = matching.best_match(data["name"], [o for o in list_for(uid) if o["id"] != subject_id])
    is_exam_goal = data["goal"] == "exam"
    dao.update(subject_id, name=data["name"], goal=data["goal"], exam_date=data["exam_date"], comfort=data["comfort"],
               weekly_hours=data["weekly_hours"], topics=rules.dump_topics(data["topics"] or DEFAULT_TOPICS),
               exam_manual=manual if is_exam_goal else None, exam_source=source if is_exam_goal else "")
    report = Report().info(*exam_service.sync(uid))
    if twin is not None:
        report.warning(f"Attention : « {data['name']} » ressemble beaucoup à ta matière « {twin['name']} » "
                       f"(similarité {score:.0%}).")
    report += planning_service.update(uid, "Matière mise à jour.")
    return FormResult(subject_id, report)


def set_program(user, subject_id, form) -> Report:
    """Programme d'une seule matière (depuis sa page) : objectif, date d'examen, aisance."""
    uid = user["id"]
    s = get(subject_id, uid)
    goal = form.get("goal") if form.get("goal") in rules.GOALS else s["goal"]
    exam_manual, exam_source, exam_date = None, "", None
    if goal == "exam":
        manual, typed = exams.typed_date(form, s)
        source = "manuel" if manual else s["exam_source"]
        if not typed:  # champ vidé : l'emploi du temps décide (épreuve, sinon dernière séance)
            typed, source = exam_service.from_agenda(subject_id)
        day = parse_date(typed)
        if day is None or day <= date.today():
            return Report().error("Indique une date d'examen à venir pour construire le programme.")
        exam_manual, exam_source, exam_date = manual, source, day.isoformat()
    dao.update(subject_id, goal=goal, exam_date=exam_date, comfort=bounded_int(form.get("comfort"), s["comfort"], 1, 5),
               weekly_hours=bounded_float(form.get("weekly_hours", s["weekly_hours"]), s["weekly_hours"], 0.5, 40.0),
               exam_manual=exam_manual, exam_source=exam_source)
    report = Report().info(*exam_service.sync(uid))
    return report + planning_service.update(uid, f"Programme de « {s['name']} » construit.", only={subject_id})


def delete(user, subject_id) -> Report:
    uid = user["id"]
    get(subject_id, uid)
    remove_files(documents_dao.paths_for_subject(subject_id))
    agenda_dao.unlink_subject(subject_id)
    dao.delete(subject_id)
    report = Report().info(*events.emit(events.SUBJECT_DELETED, user_id=uid, subject_id=subject_id))
    return report + planning_service.update(uid, "Matière supprimée.")


# ---------------------------------------------------------------- page de détail
def detail(user, subject_id) -> dict:
    uid = user["id"]
    s = get(subject_id, uid)
    if resource_service.refresh_if_stale(user, s):
        s = get(subject_id, uid)
    now = iso(datetime.now())
    agenda_exam, agenda_exam_event = exam_service.agenda_proposal(s)
    return {
        "s": s, "topics": rules.topics_of(s), "now": now, "today": date.today(),
        "stats": sessions_dao.stats(subject_id),
        "agenda_exam": agenda_exam, "agenda_exam_event": agenda_exam_event,
        "classes": agenda_analysis.dedupe(agenda_dao.for_subject(subject_id, uid, now, exams=False))[:6],
        "exams": agenda_analysis.dedupe(agenda_dao.for_subject(subject_id, uid, now, exams=True)),
        "upcoming": sessions_dao.for_subject(subject_id, now, future=True, limit=8),
        "history": sessions_dao.for_subject(subject_id, now, future=False, limit=40),
        "docs": documents_dao.list_for_subject(subject_id),
        **resource_service.grouped(subject_id),
        "exam_phase": exams.phase(s["exam_date"]), "days_left": exams.days_left(s["exam_date"]),
        "default_exam_events": agenda_dao.default_exam_ids(uid),
        "web_enabled": resource_service.web_enabled(),
        "photo_enabled": photo_enabled(),
    }
