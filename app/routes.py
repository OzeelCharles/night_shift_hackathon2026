import json
import mimetypes
import os
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.utils import secure_filename

from . import calendar_ics, embeddings, extractor, matching, planner, resources, schools, suggestions
from .auth import LEVELS, login_required
from .db import get_db

bp = Blueprint("main", __name__)

GOALS = {"exam": "Préparer un examen", "a_jour": "Être à jour dans le cours"}
COMFORT = {1: "Très difficile", 2: "Difficile", 3: "Moyen", 4: "À l'aise", 5: "Très à l'aise"}
JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
EVENT_KINDS = {"cours": "Cours en classe", "examen": "Examen ou partiel", "revision": "Révision que je prévois",
               "autre": "Autre occupation"}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


def _date_fr(d: date) -> str:
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


# ---------------------------------------------------------------- helpers
def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _time(value, default):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        return default


def _safe_next(default: str) -> str:
    nxt = request.form.get("next", "")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


def load_prefs(uid: int):
    row = get_db().execute("SELECT * FROM preferences WHERE user_id = ?", (uid,)).fetchone()
    if row is None:
        get_db().execute("INSERT INTO preferences (user_id) VALUES (?)", (uid,))
        get_db().commit()
        row = get_db().execute("SELECT * FROM preferences WHERE user_id = ?", (uid,)).fetchone()
    p = planner.Prefs(
        method=row["method"], session_minutes=row["session_minutes"], break_minutes=row["break_minutes"],
        daily_max_minutes=row["daily_max_minutes"], day_start=_time(row["day_start"], time(8)),
        day_end=_time(row["day_end"], time(21)),
        rest_days={int(d) for d in row["rest_days"].split(",") if d.strip().isdigit()},
        buffer_minutes=row["buffer_minutes"],
    )
    return p, row


def own_subject(subject_id: int):
    s = get_db().execute("SELECT * FROM subjects WHERE id = ? AND user_id = ?",
                         (subject_id, g.user["id"])).fetchone()
    if s is None:
        abort(404)
    return s


def user_subjects(uid: int):
    return get_db().execute("SELECT * FROM subjects WHERE user_id = ? ORDER BY name", (uid,)).fetchall()


def subject_topics(s) -> list[str]:
    try:
        return json.loads(s["topics"])
    except (TypeError, ValueError):
        return []


def _parse_topics(text: str) -> list[str]:
    return extractor.clean_topics(text.splitlines(), limit=60)


def exam_days_left(subject) -> int | None:
    if not subject["exam_date"]:
        return None
    return (date.fromisoformat(subject["exam_date"]) - date.today()).days


def exam_phase(subject) -> str:
    """« examen » quand l'épreuve approche (ressources d'annales ajoutées), sinon « cours »."""
    days = exam_days_left(subject)
    return "examen" if days is not None and 0 <= days <= EXAM_SOON_DAYS else "cours"


def _resources_sig(user, subject) -> str:
    """Ce dont dépendent les ressources proposées : profil et proximité de l'examen.
    Quand cette signature change, les ressources sont recherchées à nouveau."""
    return f"{user['level']}|{user['school']}|{exam_phase(subject)}"


def agenda_events(uid: int, past_days: int = 60) -> list[dict]:
    """Événements de l'agenda (récents et à venir) avec le nom de la matière rattachée."""
    since = (date.today() - timedelta(days=past_days)).isoformat()
    return [dict(r) for r in get_db().execute(
        "SELECT e.*, s.name AS subject_name FROM events e LEFT JOIN subjects s ON s.id = e.subject_id "
        "WHERE e.user_id = ? AND e.end >= ? ORDER BY e.start", (uid, since))]


# ---------------------------------------------------------------- dates d'examen
EXAM_SOON_DAYS = 21  # en deçà, des ressources de préparation d'examen sont ajoutées
EXAM_SOURCES = {"manuel": "date choisie par toi", "agenda": "épreuve de ton emploi du temps",
                "derniere_seance": "par défaut, dernière séance de cours"}


def agenda_exam_for(subject_id: int) -> tuple[str | None, str | None]:
    """Date d'examen que donnerait l'emploi du temps : la prochaine épreuve rattachée, sinon
    (par défaut) la dernière séance de cours à venir. Retourne (date ISO, source)."""
    today = date.today().isoformat()
    db = get_db()
    exam = db.execute("SELECT MIN(substr(start, 1, 10)) FROM events WHERE subject_id = ? AND kind = 'examen' "
                      "AND substr(start, 1, 10) > ?", (subject_id, today)).fetchone()[0]
    if exam:
        return exam, "agenda"
    last = db.execute("SELECT MAX(substr(start, 1, 10)) FROM events WHERE subject_id = ? AND kind = 'cours' "
                      "AND substr(start, 1, 10) > ?", (subject_id, today)).fetchone()[0]
    return (last, "derniere_seance") if last else (None, None)


def sync_exam_dates(uid: int, promote: set[int] | frozenset = frozenset()) -> list[str]:
    """Recalcule la date d'examen de chaque matière « examen » et explique les changements.

    Priorité : date choisie par l'utilisateur > prochaine épreuve de l'emploi du temps
    > dernière séance de cours (par défaut). `promote` : matières qui viennent de recevoir
    une épreuve et passent à l'objectif « examen » si elles étaient « être à jour ».
    """
    db = get_db()
    today = date.today().isoformat()
    messages = []
    for s in user_subjects(uid):
        agenda_date, agenda_src = agenda_exam_for(s["id"])
        goal = s["goal"]
        if goal != "exam" and s["id"] in promote and agenda_src == "agenda":
            goal = "exam"
        if goal != "exam":
            continue
        manual = s["exam_manual"] if s["exam_manual"] and s["exam_manual"] > today else None
        if manual:
            new, src = manual, "manuel"
        elif agenda_date:
            new, src = agenda_date, agenda_src
        else:
            continue  # rien de nouveau dans l'agenda : on garde la date actuelle
        if (new, src, goal) == (s["exam_date"], s["exam_source"], s["goal"]):
            continue
        db.execute("UPDATE subjects SET goal = ?, exam_date = ?, exam_source = ? WHERE id = ?",
                   (goal, new, src, s["id"]))
        d = _date_fr(date.fromisoformat(new))
        if src == "derniere_seance" and s["exam_source"] == "derniere_seance" and s["exam_date"]:
            verb = "repoussé" if new > s["exam_date"] else "avancé"
            messages.append(f"Examen de « {s['name']} » {verb} au {d} : c'est maintenant sa dernière séance de cours.")
        elif src == "derniere_seance":
            messages.append(f"Aucune épreuve de « {s['name']} » dans ton emploi du temps : l'examen est fixé "
                            f"par défaut à sa dernière séance, le {d}.")
        elif src == "agenda":
            messages.append(f"Examen de « {s['name']} » lu dans ton emploi du temps : {d}.")
    db.commit()
    return messages


def default_exam_event_ids(uid: int) -> set[int]:
    """Séances de cours qui servent d'examen par défaut (la dernière de chaque matière concernée)."""
    ids = set()
    db = get_db()
    for s in db.execute("SELECT id, exam_date FROM subjects WHERE user_id = ? AND goal = 'exam' "
                        "AND exam_source = 'derniere_seance' AND exam_date IS NOT NULL", (uid,)):
        # dernière séance du jour de l'examen (tous ses exemplaires, si l'export la répète par groupe)
        ids |= {r["id"] for r in db.execute(
            "SELECT id FROM events WHERE subject_id = ? AND kind = 'cours' AND start = ("
            " SELECT MAX(start) FROM events WHERE subject_id = ? AND kind = 'cours' AND substr(start, 1, 10) = ?)",
            (s["id"], s["id"], s["exam_date"]))}
    return ids


def dedupe_events(rows) -> list:
    """Un seul exemplaire par cours répété (même intitulé, même heure) : le plus long."""
    best: dict[tuple, object] = {}
    for r in rows:
        k = (r["title"], r["start"])
        if k not in best or r["end"] > best[k]["end"]:
            best[k] = r
    return sorted(best.values(), key=lambda r: r["start"])


def flash_all(messages, category="info"):
    for m in messages:
        flash(m, category)


def resolve_exam_input(form: dict, current=None) -> tuple[str | None, str | None]:
    """Date d'examen saisie dans un formulaire -> (date choisie par l'utilisateur, date retenue).

    Une date laissée identique à la date actuelle ne devient pas une date « choisie » : ainsi
    recalculer un programme ne fige pas la date par défaut issue de l'emploi du temps.
    """
    typed = (form.get("exam_date") or "").strip()
    if current is not None and typed and typed == current["exam_date"]:
        return current["exam_manual"], typed
    return (typed or None), (typed or None)


def _subject_form(f=None):
    f = request.form if f is None else f
    errors = []
    name = f.get("name", "").strip()
    goal = f.get("goal", "exam")
    if not name:
        errors.append("Le nom de la matière est obligatoire.")
    if goal not in GOALS:
        goal = "exam"
    exam_date = f.get("exam_date") or None
    if goal == "exam":
        try:
            if date.fromisoformat(exam_date) <= date.today():
                errors.append("La date d'examen doit être dans le futur.")
        except (TypeError, ValueError):
            errors.append("Indique la date de l'examen.")
    else:
        exam_date = None
    try:
        weekly = max(0.5, min(40.0, float(f.get("weekly_hours", 3) or 3)))
    except ValueError:
        weekly = 3.0
    return {
        "name": name[:100], "goal": goal, "exam_date": exam_date,
        "comfort": _int(f.get("comfort"), 3, 1, 5), "weekly_hours": weekly,
        "topics": _parse_topics(f.get("topics", "")),
    }, errors


def _initial_topics(name: str) -> tuple[list[str], str]:
    """Chapitres d'une matière créée sans support : plan Wikipédia, sinon plan générique."""
    if current_app.config.get("ENABLE_WEB"):
        try:
            topics = extractor.clean_topics(resources.wikipedia_outline(name), limit=15)
            if topics:
                return topics, "proposés à partir de Wikipédia"
        except Exception:
            pass
    return list(planner.DEFAULT_TOPICS), "génériques (ajoute tes chapitres pour un planning plus précis)"


def _merge_topics(current: list[str], new: list[str]) -> list[str]:
    if current == planner.DEFAULT_TOPICS:
        current = []
    lower = {t.lower() for t in current}
    return (current + [t for t in new if t.lower() not in lower])[:60]


def create_subject(name: str, goal="exam", exam_date=None, comfort=3, weekly_hours=2.0, topics=None,
                   exam_manual=None, exam_source="") -> int:
    """Crée une matière. Par défaut (matière née de l'agenda) l'objectif est l'examen, dont la date
    sera lue dans l'emploi du temps par sync_exam_dates (épreuve, sinon dernière séance)."""
    if topics is None:
        topics, _ = _initial_topics(name)
    cur = get_db().execute(
        "INSERT INTO subjects (user_id, name, goal, exam_date, comfort, weekly_hours, topics, exam_manual, exam_source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], name[:100], goal, exam_date, comfort, weekly_hours, json.dumps(topics, ensure_ascii=False),
         exam_manual, exam_source))
    get_db().commit()
    return cur.lastrowid


def resolve_subject(title: str, explicit: str | None, create: bool = True, subjects=None, goal: str = "exam"):
    """Associe un intitulé (cours, créneau) à une matière.

    explicit : id choisi par l'utilisateur, « auto » pour la détection, « aucune » pour ne rien associer.
    Retourne (matière | None, statut, score) ; statut ∈ explicit / matched / created / none.
    """
    if explicit and explicit.isdigit():
        return own_subject(int(explicit)), "explicit", 1.0
    if explicit == "aucune":
        return None, "none", 0.0
    subjects = subjects if subjects is not None else user_subjects(g.user["id"])
    match, score = matching.best_match(title, subjects)
    if match is not None:
        return match, "matched", score
    if not create:
        return None, "none", score
    name = matching.clean_title(title) or title
    sid = create_subject(name, goal=goal)
    return own_subject(sid), "created", score


def _save_documents(subject_id: int, files) -> tuple[list[str], list[str]]:
    """Enregistre et analyse les fichiers. Retourne (chapitres trouvés, messages)."""
    topics, messages = [], []
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(g.user["id"]))
    os.makedirs(folder, exist_ok=True)
    db = get_db()
    for f in files:
        if not f or not f.filename:
            continue
        if not extractor.allowed_file(f.filename):
            messages.append(f"« {f.filename} » : format non pris en charge.")
            continue
        safe = secure_filename(f.filename) or "document"
        path = os.path.join(folder, f"{uuid.uuid4().hex}_{safe}")
        f.save(path)
        text, found = extractor.extract(path)
        if not found and text:
            found = [k.capitalize() for k in extractor.keywords(text, 6)]
        topics += found
        db.execute("INSERT INTO documents (subject_id, filename, stored_path, excerpt, topics_found) "
                   "VALUES (?, ?, ?, ?, ?)", (subject_id, f.filename[:200], path, text[:5000], len(found)))
        messages.append(f"« {f.filename} » : {len(found)} chapitre(s) détecté(s)."
                        if text else f"« {f.filename} » : texte illisible (document scanné ?).")
    db.commit()
    return topics, messages


def _remove_file(path):
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    real = os.path.realpath(path)
    if real.startswith(root + os.sep) and os.path.exists(real):
        os.remove(real)


# ---------------------------------------------------------------- ressources (en arrière-plan)
def refresh_resources(subject, user) -> list[str]:
    """Remplace les suggestions d'une matière (les ressources gardées par l'utilisateur restent)."""
    db = get_db()
    excerpts = " ".join(r["excerpt"] for r in db.execute(
        "SELECT excerpt FROM documents WHERE subject_id = ?", (subject["id"],)))
    kw = extractor.keywords(excerpts, 5) if excerpts else None
    days = exam_days_left(subject)
    found, errors = resources.find_resources(
        subject["name"], subject_topics(subject), kw, level=user["level"], school=user["school"],
        exam_days=days if exam_phase(subject) == "examen" else None)
    saved = {r["url"] for r in db.execute("SELECT url FROM resources WHERE subject_id = ? AND saved = 1",
                                          (subject["id"],))}
    db.execute("DELETE FROM resources WHERE subject_id = ? AND saved = 0", (subject["id"],))
    db.executemany(
        "INSERT INTO resources (subject_id, title, url, source, snippet, kind, thumbnail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(subject["id"], r["title"][:300], r["url"], r["source"], r["snippet"], r["kind"], r["thumbnail"], r["meta"])
         for r in found if r["url"] not in saved])
    db.execute("UPDATE subjects SET resources_status = ?, resources_sig = ? WHERE id = ?",
               ("error" if errors and len(found) <= 6 else "ok", _resources_sig(user, subject), subject["id"]))
    db.commit()
    return errors


def _refresh_worker(app, uid: int, ids: list[int]):
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        for sid in ids:
            s = db.execute("SELECT * FROM subjects WHERE id = ? AND user_id = ?", (sid, uid)).fetchone()
            if s is None or user is None:
                continue
            try:
                refresh_resources(s, user)
            except Exception:
                db.execute("UPDATE subjects SET resources_status = 'error' WHERE id = ?", (sid,))
                db.commit()


def refresh_resources_async(uid: int, ids: list[int]) -> bool:
    """Lance la recherche sans faire attendre l'utilisateur ; la page affiche « en cours »."""
    if not ids or not current_app.config.get("ENABLE_WEB"):
        return False
    db = get_db()
    db.executemany("UPDATE subjects SET resources_status = 'pending' WHERE id = ?", [(i,) for i in ids])
    db.commit()
    threading.Thread(target=_refresh_worker, args=(current_app._get_current_object(), uid, list(ids)),
                     daemon=True).start()
    return True


def refresh_all_resources_async(uid: int) -> int:
    ids = [r["id"] for r in user_subjects(uid)]
    return len(ids) if refresh_resources_async(uid, ids) else 0


# ---------------------------------------------------------------- pages
@bp.route("/")
def index():
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@bp.route("/tableau-de-bord")
@login_required
def dashboard():
    db = get_db()
    uid = g.user["id"]
    now = datetime.now()
    today_sessions = db.execute(
        "SELECT se.*, su.name AS subject_name FROM sessions se JOIN subjects su ON su.id = se.subject_id "
        "WHERE se.user_id = ? AND se.start >= ? AND se.start < ? ORDER BY se.start",
        (uid, date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat())).fetchall()
    upcoming = db.execute(
        "SELECT se.*, su.name AS subject_name FROM sessions se JOIN subjects su ON su.id = se.subject_id "
        "WHERE se.user_id = ? AND se.start >= ? ORDER BY se.start LIMIT 6",
        (uid, (date.today() + timedelta(days=1)).isoformat())).fetchall()
    subjects = db.execute(
        "SELECT su.*, "
        " COALESCE(SUM((julianday(se.end) - julianday(se.start)) * 1440), 0) AS planned,"
        " COALESCE(SUM(CASE WHEN se.done THEN (julianday(se.end) - julianday(se.start)) * 1440 END), 0) AS done_min,"
        " SUM(CASE WHEN se.done = 0 AND se.end < ? THEN 1 ELSE 0 END) AS missed "
        "FROM subjects su LEFT JOIN sessions se ON se.subject_id = su.id "
        "WHERE su.user_id = ? GROUP BY su.id ORDER BY su.exam_date IS NULL, su.exam_date, su.name",
        (_iso(now), uid)).fetchall()
    return render_template("dashboard.html", today_sessions=today_sessions, upcoming=upcoming,
                           subjects=subjects, today=date.today(), now=_iso(now), goals=GOALS,
                           date_fromiso=date.fromisoformat)


@bp.route("/preferences", methods=("GET", "POST"))
@login_required
def preferences():
    uid = g.user["id"]
    _, row = load_prefs(uid)
    if request.method == "POST":
        f = request.form
        method = f.get("method") if f.get("method") in planner.METHODS else "mixte"
        start = _time(f.get("day_start"), time(8))
        end = _time(f.get("day_end"), time(21))
        if end <= start:
            flash("L'heure de fin doit être après l'heure de début.", "error")
            return redirect(url_for("main.preferences"))
        rest = sorted({int(d) for d in f.getlist("rest_days") if d.isdigit() and 0 <= int(d) <= 6})
        if len(rest) == 7:
            flash("Garde au moins un jour de travail.", "error")
            return redirect(url_for("main.preferences"))
        get_db().execute(
            "UPDATE preferences SET method=?, session_minutes=?, break_minutes=?, daily_max_minutes=?, "
            "day_start=?, day_end=?, rest_days=?, buffer_minutes=?, horizon_weeks=? WHERE user_id=?",
            (method, _int(f.get("session_minutes"), 50, 20, 240), _int(f.get("break_minutes"), 10, 0, 60),
             _int(f.get("daily_max_minutes"), 180, 20, 720), start.strftime("%H:%M"), end.strftime("%H:%M"),
             ",".join(map(str, rest)), _int(f.get("buffer_minutes"), 15, 0, 120),
             _int(f.get("horizon_weeks"), 4, 1, 16), uid))
        get_db().commit()
        regenerate_and_report(uid, "Préférences enregistrées.")
        return redirect(url_for("main.plan"))
    return render_template("preferences.html", p=row, methods=planner.METHODS, jours=JOURS_COURTS,
                           rest=[int(d) for d in row["rest_days"].split(",") if d.isdigit()])


# ---------------------------------------------------------------- matières
@bp.route("/matieres/nouvelle", methods=("GET", "POST"))
@login_required
def subject_new():
    if request.method == "POST":
        form = request.form.to_dict()
        exam_note = None
        typed_exam = (form.get("exam_date") or "").strip() or None  # date choisie : prioritaire
        exam_source = "manuel" if typed_exam else ""
        # Objectif examen sans date saisie : l'épreuve de l'emploi du temps, sinon la dernière séance.
        if form.get("goal") == "exam" and not typed_exam and form.get("name", "").strip():
            events = agenda_events(g.user["id"])
            found, ev = suggestions.detect_exam_date(form["name"], events)
            if found:
                form["exam_date"], exam_source = found.isoformat(), "agenda"
                exam_note = f"Date d'examen lue dans ton emploi du temps (« {ev['title']} ») : {_date_fr(found)}."
            else:
                last = suggestions.last_course_date(form["name"], events)
                if last:
                    form["exam_date"], exam_source = last.isoformat(), "derniere_seance"
                    exam_note = (f"Aucune épreuve dans ton emploi du temps : l'examen est fixé par défaut "
                                 f"à la dernière séance de cours, le {_date_fr(last)}.")
        data, errors = _subject_form(form)
        if errors:
            for e in errors:
                flash(e, "error")
            if "Indique la date de l'examen." in errors:
                flash("Ni épreuve ni cours à ce nom dans ton emploi du temps : saisis la date, "
                      "ou choisis « Être à jour dans le cours ».", "info")
            return render_template("subjects/form.html", s=form, goals=GOALS, comfort=COMFORT, new=True,
                                   force_new=form.get("force_new"),
                                   agenda_suggestions=suggestions.suggest(
                                       "", agenda_events(g.user["id"]), user_subjects(g.user["id"])))
        db = get_db()
        files = request.files.getlist("documents")

        # Le nom ressemble-t-il à une matière existante ? (similarité cosinus, voir matching.py)
        match, score = (None, 0.0)
        if not request.form.get("force_new"):
            match, score = matching.best_match(data["name"], user_subjects(g.user["id"]))
        if match is not None:
            doc_topics, messages = _save_documents(match["id"], files)
            new_topics = data["topics"] + extractor.clean_topics(doc_topics, limit=40)
            if new_topics:
                db.execute("UPDATE subjects SET topics = ? WHERE id = ?",
                           (json.dumps(_merge_topics(subject_topics(match), new_topics), ensure_ascii=False),
                            match["id"]))
                db.commit()
            for m in messages:
                flash(m, "info")
            session["merged_hint"] = {"subject_id": match["id"], "name": data["name"]}
            regenerate_and_report(
                g.user["id"], f"« {data['name']} » correspond à ta matière « {match['name']} » "
                              f"(similarité {score:.0%}) : tout a été ajouté à cette matière.")
            return redirect(url_for("main.subject_detail", subject_id=match["id"]))

        is_exam_goal = data["goal"] == "exam"
        sid = create_subject(data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"],
                             topics=data["topics"] or [],
                             exam_manual=typed_exam if is_exam_goal else None,
                             exam_source=exam_source if is_exam_goal else "")
        # les cours et épreuves de l'agenda au nom proche sont rattachés à la nouvelle matière
        linked = 0
        for e in agenda_events(g.user["id"]):
            exam_ev = suggestions.is_exam(e["title"])
            if e["subject_id"] is None and not (e["kind"] == "autre" and e["source"] == "manuel") and \
                    (exam_ev or suggestions.is_course(e["title"])) and \
                    matching.similarity(data["name"], suggestions.course_name(e["title"])) >= matching.THRESHOLD:
                db.execute("UPDATE events SET subject_id = ?, kind = ? WHERE id = ?",
                           (sid, "examen" if exam_ev else "cours", e["id"]))
                linked += 1
        sync_messages = []
        if linked:
            db.commit()
            flash(f"{linked} créneau(x) de ton agenda rattaché(s) à « {data['name']} ».", "info")
            sync_messages = sync_exam_dates(g.user["id"])
        about_new = [m for m in sync_messages if f"« {data['name']} »" in m]
        if exam_note and not about_new:
            flash(exam_note, "info")
        flash_all(sync_messages)
        doc_topics, messages = _save_documents(sid, files)
        topics = data["topics"] or extractor.clean_topics(doc_topics, limit=40)
        source = "saisis" if data["topics"] else "extraits des documents"
        if not topics:
            topics, source = _initial_topics(data["name"])
        db.execute("UPDATE subjects SET topics = ? WHERE id = ?", (json.dumps(topics, ensure_ascii=False), sid))
        db.commit()
        for m in messages:
            flash(m, "info")
        refresh_resources_async(g.user["id"], [sid])
        regenerate_and_report(g.user["id"], f"Matière créée avec {len(topics)} chapitres {source}.")
        return redirect(url_for("main.subject_detail", subject_id=sid))
    prefill = {"name": request.args.get("nom", "")}
    initial = suggestions.suggest("", agenda_events(g.user["id"]), user_subjects(g.user["id"]))
    return render_template("subjects/form.html", s=prefill, goals=GOALS, comfort=COMFORT, new=True,
                           force_new=request.args.get("nouvelle"), agenda_suggestions=initial)


@bp.route("/api/suggestions/matieres")
@login_required
def api_subject_suggestions():
    """Matières suggérées d'après l'agenda, classées par similarité cosinus d'embeddings."""
    q = request.args.get("q", "")[:100]
    return jsonify(suggestions.suggest(q, agenda_events(g.user["id"]), user_subjects(g.user["id"])))


@bp.route("/api/agenda/rattachement")
@login_required
def api_slot_link():
    """Aperçu en direct, pendant la saisie d'un créneau : à quelle matière sera-t-il rattaché ?"""
    title = request.args.get("titre", "").strip()[:200]
    kind = request.args.get("type", "cours")
    subjects = user_subjects(g.user["id"])
    if not title:
        return jsonify({"match": None, "new_name": None, "suggestions": []})
    lookup = suggestions.course_name(title) if kind == "examen" or suggestions.is_exam(title) else title
    match, score = matching.best_match(lookup, subjects)
    ranked = embeddings.rank(title, [s["name"] for s in subjects])[:5]
    return jsonify({
        "match": {"id": match["id"], "name": match["name"], "score": score} if match else None,
        "new_name": None if match else (matching.clean_title(lookup) or title),
        "suggestions": [{"name": n, "score": sc} for n, sc in ranked],
    })


@bp.route("/api/ecoles")
def api_schools():
    """Autocomplétion des établissements de France (accessible aussi à l'inscription)."""
    return jsonify(schools.search(request.args.get("q", "")[:80], request.args.get("niveau")))


@bp.route("/matieres/<int:subject_id>")
@login_required
def subject_detail(subject_id):
    s = own_subject(subject_id)
    db = get_db()
    now = _iso(datetime.now())
    hint = session.get("merged_hint")
    if hint and hint.get("subject_id") == subject_id:
        session.pop("merged_hint")
    else:
        hint = None
    # Les ressources dépendent du profil et de la proximité de l'examen : si l'un des deux a changé
    # depuis la dernière recherche, on relance la recherche en arrière-plan.
    if current_app.config.get("ENABLE_WEB") and s["resources_status"] != "pending" \
            and s["resources_sig"] != _resources_sig(g.user, s):
        refresh_resources_async(g.user["id"], [subject_id])
        s = own_subject(subject_id)
    res = db.execute("SELECT * FROM resources WHERE subject_id = ? ORDER BY id", (subject_id,)).fetchall()
    # Date d'examen que propose l'emploi du temps, quand elle diffère de celle retenue
    agenda_date, agenda_src = agenda_exam_for(subject_id)
    agenda_exam = agenda_exam_event = None
    if agenda_date and agenda_date != s["exam_date"] and (s["exam_source"] == "manuel" or s["goal"] != "exam"):
        agenda_exam = date.fromisoformat(agenda_date)
        agenda_exam_event = {"title": "dernière séance de cours" if agenda_src == "derniere_seance" else
                             db.execute("SELECT title FROM events WHERE subject_id = ? AND kind = 'examen' AND "
                                        "substr(start, 1, 10) = ?", (subject_id, agenda_date)).fetchone()[0],
                             "source": agenda_src}
    stats = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(done), 0) AS done,"
        " COALESCE(SUM((julianday(end) - julianday(start)) * 1440), 0) AS minutes,"
        " COALESCE(SUM(CASE WHEN done THEN (julianday(end) - julianday(start)) * 1440 END), 0) AS done_min "
        "FROM sessions WHERE subject_id = ?", (subject_id,)).fetchone()
    return render_template(
        "subjects/detail.html", s=s, topics=subject_topics(s), goals=GOALS, comfort=COMFORT, now=now,
        kinds=planner.KIND_LABELS, stats=stats, hint=hint, today=date.today(),
        agenda_exam=agenda_exam, agenda_exam_event=agenda_exam_event,
        classes=dedupe_events(db.execute("SELECT * FROM events WHERE subject_id = ? AND user_id = ? AND end >= ? "
                                         "AND kind != 'examen' ORDER BY start", (subject_id, g.user["id"], now)))[:6],
        exams=dedupe_events(db.execute("SELECT * FROM events WHERE subject_id = ? AND user_id = ? AND end >= ? "
                                       "AND kind = 'examen' ORDER BY start", (subject_id, g.user["id"], now))),
        upcoming=db.execute("SELECT * FROM sessions WHERE subject_id = ? AND end >= ? ORDER BY start LIMIT 8",
                            (subject_id, now)).fetchall(),
        history=db.execute("SELECT * FROM sessions WHERE subject_id = ? AND end < ? ORDER BY start DESC LIMIT 40",
                           (subject_id, now)).fetchall(),
        docs=db.execute("SELECT * FROM documents WHERE subject_id = ? ORDER BY created_at DESC",
                        (subject_id,)).fetchall(),
        saved=[r for r in res if r["saved"]],
        videos=[r for r in res if not r["saved"] and r["kind"] == "video"],
        web=[r for r in res if not r["saved"] and r["kind"] == "web"],
        platforms=[r for r in res if not r["saved"] and r["kind"] == "platform"],
        exam_res=[r for r in res if not r["saved"] and r["kind"] == "exam"],
        exam_phase=exam_phase(s), exam_sources=EXAM_SOURCES, exam_soon_days=EXAM_SOON_DAYS,
        default_exam_events=default_exam_event_ids(g.user["id"]),
        days_left=exam_days_left(s),
        level_label=LEVELS.get(g.user["level"], ""), web_enabled=current_app.config.get("ENABLE_WEB"))


@bp.route("/matieres/<int:subject_id>/modifier", methods=("GET", "POST"))
@login_required
def subject_edit(subject_id):
    s = own_subject(subject_id)
    if request.method == "POST":
        form = request.form.to_dict()
        manual, typed = resolve_exam_input(form, s)
        source = "manuel" if manual else s["exam_source"]
        if form.get("goal") == "exam" and not typed:
            derived, derived_src = agenda_exam_for(subject_id)  # épreuve, sinon dernière séance
            if derived:
                form["exam_date"], source = derived, derived_src
        data, errors = _subject_form(form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("subjects/form.html", s=form, goals=GOALS, comfort=COMFORT, new=False,
                                   subject_id=subject_id)
        others = [o for o in user_subjects(g.user["id"]) if o["id"] != subject_id]
        twin, score = matching.best_match(data["name"], others)
        is_exam_goal = data["goal"] == "exam"
        get_db().execute(
            "UPDATE subjects SET name=?, goal=?, exam_date=?, comfort=?, weekly_hours=?, topics=?, "
            "exam_manual=?, exam_source=? WHERE id=?",
            (data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"],
             json.dumps(data["topics"] or planner.DEFAULT_TOPICS, ensure_ascii=False),
             manual if is_exam_goal else None, source if is_exam_goal else "", subject_id))
        get_db().commit()
        flash_all(sync_exam_dates(g.user["id"]))
        if twin is not None:
            flash(f"Attention : « {data['name']} » ressemble beaucoup à ta matière « {twin['name']} » "
                  f"(similarité {score:.0%}).", "warning")
        regenerate_and_report(g.user["id"], "Matière mise à jour.")
        return redirect(url_for("main.subject_detail", subject_id=subject_id))
    form = dict(s)
    form["topics"] = "\n".join(subject_topics(s))
    return render_template("subjects/form.html", s=form, goals=GOALS, comfort=COMFORT, new=False,
                           subject_id=subject_id)


@bp.route("/matieres/<int:subject_id>/supprimer", methods=("POST",))
@login_required
def subject_delete(subject_id):
    own_subject(subject_id)
    db = get_db()
    for d in db.execute("SELECT stored_path FROM documents WHERE subject_id = ?", (subject_id,)):
        _remove_file(d["stored_path"])
    db.execute("UPDATE events SET subject_id = NULL, kind = 'autre' WHERE subject_id = ?", (subject_id,))
    db.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    db.commit()
    regenerate_and_report(g.user["id"], "Matière supprimée.")
    return redirect(url_for("main.dashboard"))


@bp.route("/matieres/<int:subject_id>/documents", methods=("POST",))
@login_required
def subject_upload(subject_id):
    s = own_subject(subject_id)
    found, messages = _save_documents(subject_id, request.files.getlist("documents"))
    current = subject_topics(s)
    merged = _merge_topics(current, extractor.clean_topics(found, 40))
    added = len(merged) - len([t for t in current if t not in planner.DEFAULT_TOPICS])
    if added > 0:
        get_db().execute("UPDATE subjects SET topics = ? WHERE id = ?",
                         (json.dumps(merged, ensure_ascii=False), subject_id))
        get_db().commit()
    for m in messages:
        flash(m, "info")
    if added > 0:
        regenerate_and_report(g.user["id"], f"{added} nouveau(x) chapitre(s) ajouté(s).")
    else:
        flash("Aucun nouveau chapitre trouvé dans ces documents.", "info")
    return redirect(url_for("main.subject_detail", subject_id=subject_id) + "#supports")


def _own_document(doc_id: int):
    d = get_db().execute("SELECT d.* FROM documents d JOIN subjects s ON s.id = d.subject_id "
                         "WHERE d.id = ? AND s.user_id = ?", (doc_id, g.user["id"])).fetchone()
    if d is None:
        abort(404)
    return d


@bp.route("/documents/<int:doc_id>")
@login_required
def document_open(doc_id):
    d = _own_document(doc_id)
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    real = os.path.realpath(d["stored_path"])
    if not real.startswith(root + os.sep) or not os.path.exists(real):
        abort(404)
    mime = mimetypes.guess_type(d["filename"])[0] or "application/octet-stream"
    inline = mime in ("application/pdf", "text/plain", "text/markdown") and not request.args.get("telecharger")
    return send_file(real, mimetype=mime, as_attachment=not inline, download_name=d["filename"])


@bp.route("/documents/<int:doc_id>/supprimer", methods=("POST",))
@login_required
def document_delete(doc_id):
    d = _own_document(doc_id)
    _remove_file(d["stored_path"])
    get_db().execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    get_db().commit()
    flash("Document supprimé.", "success")
    return redirect(url_for("main.subject_detail", subject_id=d["subject_id"]) + "#supports")


# ---------------------------------------------------------------- ressources de l'utilisateur
@bp.route("/matieres/<int:subject_id>/ressources", methods=("POST",))
@login_required
def subject_resources(subject_id):
    own_subject(subject_id)
    if refresh_resources_async(g.user["id"], [subject_id]):
        flash("Recherche de nouvelles ressources lancée : elles s'affichent dans quelques secondes.", "info")
    else:
        flash("La recherche sur internet est désactivée sur ce serveur.", "error")
    return redirect(url_for("main.subject_detail", subject_id=subject_id) + "#proposees")


def _own_resource(resource_id: int):
    r = get_db().execute("SELECT r.* FROM resources r JOIN subjects s ON s.id = r.subject_id "
                         "WHERE r.id = ? AND s.user_id = ?", (resource_id, g.user["id"])).fetchone()
    if r is None:
        abort(404)
    return r


@bp.route("/ressources/<int:resource_id>/garder", methods=("POST",))
@login_required
def resource_keep(resource_id):
    r = _own_resource(resource_id)
    get_db().execute("UPDATE resources SET saved = 1 WHERE id = ?", (resource_id,))
    get_db().commit()
    flash(f"« {r['title'][:80]} » ajouté à tes ressources.", "success")
    return redirect(url_for("main.subject_detail", subject_id=r["subject_id"]) + "#mes-ressources")


@bp.route("/ressources/<int:resource_id>/retirer", methods=("POST",))
@login_required
def resource_remove(resource_id):
    r = _own_resource(resource_id)
    if r["kind"] == "perso":
        get_db().execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    else:
        get_db().execute("UPDATE resources SET saved = 0 WHERE id = ?", (resource_id,))
    get_db().commit()
    flash("Ressource retirée de ta liste.", "success")
    return redirect(url_for("main.subject_detail", subject_id=r["subject_id"]) + "#mes-ressources")


@bp.route("/matieres/<int:subject_id>/ressources/ajouter", methods=("POST",))
@login_required
def resource_add(subject_id):
    own_subject(subject_id)
    url = request.form.get("url", "").strip()
    title = request.form.get("title", "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        flash("Lien invalide : il doit commencer par http:// ou https://.", "error")
    else:
        get_db().execute(
            "INSERT INTO resources (subject_id, title, url, source, kind, saved) VALUES (?, ?, ?, ?, 'perso', 1)",
            (subject_id, (title or parsed.netloc)[:300], url, parsed.netloc.replace("www.", "")))
        get_db().commit()
        flash("Lien ajouté à tes ressources.", "success")
    return redirect(url_for("main.subject_detail", subject_id=subject_id) + "#mes-ressources")


# ---------------------------------------------------------------- agenda
@bp.route("/agenda")
@login_required
def calendar():
    db = get_db()
    uid = g.user["id"]
    now = _iso(datetime.now())
    events = db.execute(
        "SELECT e.*, s.name AS subject_name FROM events e LEFT JOIN subjects s ON s.id = e.subject_id "
        "WHERE e.user_id = ? AND e.end >= ? ORDER BY e.start LIMIT 300", (uid, now)).fetchall()
    revisions = db.execute(
        "SELECT se.*, s.name AS subject_name FROM sessions se JOIN subjects s ON s.id = se.subject_id "
        "WHERE se.user_id = ? AND se.manual = 1 AND se.end >= ? ORDER BY se.start", (uid, now)).fetchall()
    return render_template("calendar.html", events=events, revisions=revisions, subjects=user_subjects(uid),
                           event_kinds=EVENT_KINDS)


def _association_message(title: str, subject, status: str, score: float) -> str | None:
    if status == "matched":
        return f"« {title} » associé à ta matière « {subject['name']} » (similarité {score:.0%})."
    if status == "created":
        return (f"Aucune matière ne ressemble à « {title} » : la matière « {subject['name']} » a été créée. "
                "Complète-la depuis sa page (objectif, supports).")
    return None


@bp.route("/agenda/import", methods=("POST",))
@login_required
def calendar_import():
    f = request.files.get("ics")
    if not f or not f.filename.lower().endswith(".ics"):
        flash("Choisis un fichier .ics (export de Google Agenda, Outlook, ENT...).", "error")
        return redirect(url_for("main.calendar"))
    text = f.read().decode("utf-8", errors="ignore")
    events, skipped = calendar_ics.parse_ics(text, datetime.combine(date.today(), time()),
                                             keep_allday=suggestions.is_exam)
    db = get_db()
    uid = g.user["id"]
    # « Mettre à jour » (recommandé) : les cours disparus du nouvel emploi du temps sont retirés.
    # Sinon on ne fait qu'ajouter / corriger, sans rien supprimer.
    sync_mode = bool(request.form.get("replace"))
    create = bool(request.form.get("create_missing"))

    # Événements déjà importés (à venir), indexés par leur clé de synchronisation
    existing: dict[str, list] = defaultdict(list)
    for r in db.execute("SELECT * FROM events WHERE user_id = ? AND source = 'ics' AND end >= ?",
                        (uid, date.today().isoformat())):
        existing[r["uid"] or _fallback_key(r["title"], r["start"])].append(r)
    # Rattachements déjà connus, y compris les corrections faites à la main : même intitulé, même matière
    known = {r["title"]: (r["subject_id"], r["kind"]) for r in db.execute(
        "SELECT title, subject_id, kind FROM events WHERE user_id = ? AND source = 'ics' AND subject_id IS NOT NULL "
        "ORDER BY id", (uid,))}
    subjects = list(user_subjects(uid))
    cache: dict[str, int | None] = {}
    created, new_ids = [], []

    def link(title: str) -> tuple[int | None, str]:
        if title in known:
            return known[title]
        exam = suggestions.is_exam(title)
        course_like = exam or suggestions.is_course(title)
        name = suggestions.course_name(title) if exam else title
        key = matching.normalize(name)
        if key not in cache:
            match, _ = matching.best_match(name, subjects) if course_like else (None, 0.0)
            if match is None and create and key and course_like:
                sid = create_subject(suggestions.course_name(title), topics=list(planner.DEFAULT_TOPICS))
                match = own_subject(sid)
                subjects.append(match)
                created.append(match["name"])
                new_ids.append(sid)
            cache[key] = match["id"] if match else None
        sid = cache[key]
        # une épreuve reste une épreuve même sans matière (elle sera rattachée à sa création)
        return sid, "examen" if exam else ("cours" if sid else "autre")

    # Certains exports (ENT d'école) répètent un même cours pour chaque groupe de TD : même intitulé,
    # même heure. On n'en garde qu'un (le plus long) ; les doublons déjà importés seront retirés.
    unique: dict[tuple, dict] = {}
    for e in events:
        k = (matching.normalize(e["title"]) or e["title"], e["start"])
        if k not in unique or e["end"] > unique[k]["end"]:
            unique[k] = e
    duplicates = len(events) - len(unique)
    events = sorted(unique.values(), key=lambda e: e["start"])

    added = moved = unchanged = removed = 0
    promote: set[int] = set()
    for e in events:
        key = e["key"] or _fallback_key(e["title"], _iso(e["start"]))
        title, start, end = e["title"][:200], _iso(e["start"]), _iso(e["end"])
        row = existing[key].pop(0) if existing.get(key) else None
        if row is not None:
            if (row["title"], row["start"], row["end"]) == (title, start, end):
                unchanged += 1
                continue
            # cours déplacé ou renommé : on garde la ligne (et son rattachement, sauf si l'intitulé change)
            sid, kind = (row["subject_id"], row["kind"]) if row["title"] == title else link(title)
            db.execute("UPDATE events SET title = ?, start = ?, end = ?, subject_id = ?, kind = ? WHERE id = ?",
                       (title, start, end, sid, kind, row["id"]))
            moved += 1
        else:
            sid, kind = link(title)
            db.execute("INSERT INTO events (user_id, title, start, end, source, subject_id, kind, uid) "
                       "VALUES (?, ?, ?, ?, 'ics', ?, ?, ?)", (uid, title, start, end, sid, kind, key))
            added += 1
        if kind == "examen" and sid:
            promote.add(sid)
    if sync_mode:
        for rows in existing.values():
            for row in rows:
                db.execute("DELETE FROM events WHERE id = ?", (row["id"],))
                removed += 1
    db.commit()

    changes = added + moved + removed
    if changes == 0:
        msg = f"Emploi du temps déjà à jour : {unchanged} créneau(x), aucun changement."
    else:
        msg = (f"Emploi du temps mis à jour : {added} ajouté(s), {moved} déplacé(s) ou renommé(s), "
               f"{removed} supprimé(s), {unchanged} inchangé(s).")
    if created:
        msg += f" Nouvelles matières : {', '.join(created)}."
    if duplicates:
        msg += f" {duplicates} doublon(s) (même cours répété pour plusieurs groupes) fusionné(s)."
    if skipped:
        msg += f" {skipped} événement(s) « journée entière » ignoré(s)."
    flash_all(sync_exam_dates(uid, promote))
    refresh_resources_async(uid, new_ids)
    if changes:
        regenerate_and_report(uid, msg, repair=True)
    else:
        flash(msg + " Ton planning ne bouge pas.", "success")
    return redirect(url_for("main.calendar"))


def _fallback_key(title: str, start_iso: str) -> str:
    """Clé d'un événement sans UID : intitulé normalisé + horaire (un déplacement = suppression + ajout)."""
    return f"~{matching.normalize(title)}|{start_iso}"


@bp.route("/agenda/ajouter", methods=("POST",))
@login_required
def event_add():
    f = request.form
    try:
        d = date.fromisoformat(f.get("date", ""))
        s = datetime.combine(d, datetime.strptime(f.get("start", ""), "%H:%M").time())
        e = datetime.combine(d, datetime.strptime(f.get("end", ""), "%H:%M").time())
    except ValueError:
        flash("Date ou heures invalides.", "error")
        return redirect(url_for("main.calendar"))
    if e <= s:
        flash("L'heure de fin doit être après l'heure de début.", "error")
        return redirect(url_for("main.calendar"))
    weeks = _int(f.get("repeat_weeks"), 1, 1, 52)
    kind = f.get("kind") if f.get("kind") in EVENT_KINDS else "autre"
    title = (f.get("title") or "").strip()[:200]
    uid = g.user["id"]
    db = get_db()
    slots = [(s + timedelta(weeks=k), e + timedelta(weeks=k)) for k in range(weeks)]

    if kind == "autre":
        db.executemany("INSERT INTO events (user_id, title, start, end, source, kind) VALUES (?, ?, ?, ?, 'manuel', 'autre')",
                       [(uid, title or "Occupé", _iso(a), _iso(b)) for a, b in slots])
        db.commit()
        regenerate_and_report(uid, f"{weeks} créneau(x) occupé(s) ajouté(s).", repair=True)
        return redirect(url_for("main.calendar"))

    if not title and not (f.get("subject") or "").isdigit():
        flash("Indique l'intitulé du cours : il sert à retrouver la matière.", "error")
        return redirect(url_for("main.calendar"))
    # pour un examen, on rapproche le nom de la matière sans les mots « partiel », « DS »…
    lookup = suggestions.course_name(title) if kind == "examen" and title else title
    subject, status, score = resolve_subject(lookup, f.get("subject") or "auto",
                                             goal="a_jour" if kind == "revision" else "exam")
    title = title or (f"Examen de {subject['name']}" if kind == "examen" else subject["name"])
    msg = _association_message(title, subject, status, score)
    if status == "created":
        refresh_resources_async(uid, [subject["id"]])
    if kind in ("cours", "examen"):
        db.executemany("INSERT INTO events (user_id, title, start, end, source, kind, subject_id) "
                       "VALUES (?, ?, ?, ?, 'manuel', ?, ?)",
                       [(uid, title, _iso(a), _iso(b), kind, subject["id"]) for a, b in slots])
        label = f"{weeks} cours ajouté(s)." if kind == "cours" else "Examen ajouté à ton agenda."
    else:  # créneau de révision choisi par l'utilisateur : une séance fixe, jamais déplacée
        db.executemany("INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity, manual) "
                       "VALUES (?, ?, ?, ?, ?, 'review', ?, 1)",
                       [(uid, subject["id"], _iso(a), _iso(b),
                         "Révision libre" if title == subject["name"] else title,
                         "Créneau de révision choisi par toi") for a, b in slots])
        label = f"{weeks} créneau(x) de révision ajouté(s)."
    db.commit()
    # un cours après la dernière séance repousse l'examen par défaut ; une épreuve fixe la date
    flash_all(sync_exam_dates(uid, {subject["id"]} if kind == "examen" else frozenset()))
    regenerate_and_report(uid, " ".join(x for x in (msg, label) if x), repair=True)
    return redirect(url_for("main.calendar"))


@bp.route("/agenda/<int:event_id>/matiere", methods=("POST",))
@login_required
def event_link(event_id):
    ev = get_db().execute("SELECT * FROM events WHERE id = ? AND user_id = ?", (event_id, g.user["id"])).fetchone()
    if ev is None:
        abort(404)
    choice = request.form.get("subject", "")
    sid = own_subject(int(choice))["id"] if choice.isdigit() else None
    # on rattache aussi les autres occurrences du même cours (même intitulé)
    kind = ("examen" if suggestions.is_exam(ev["title"]) else "cours") if sid else "autre"
    get_db().execute("UPDATE events SET subject_id = ?, kind = ? WHERE user_id = ? AND title = ?",
                     (sid, kind, g.user["id"], ev["title"]))
    get_db().commit()
    flash("Rattachement mis à jour pour toutes les occurrences de ce cours.", "success")
    flash_all(sync_exam_dates(g.user["id"], {sid} if sid and kind == "examen" else frozenset()))
    regenerate_and_report(g.user["id"], "", repair=True, quiet=True)
    return redirect(url_for("main.calendar"))


@bp.route("/agenda/<int:event_id>/supprimer", methods=("POST",))
@login_required
def event_delete(event_id):
    get_db().execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, g.user["id"]))
    get_db().commit()
    flash_all(sync_exam_dates(g.user["id"]))
    regenerate_and_report(g.user["id"], "Créneau supprimé.", repair=True)
    return redirect(_safe_next(url_for("main.calendar")))


@bp.route("/seances/<int:session_id>/supprimer", methods=("POST",))
@login_required
def session_delete(session_id):
    get_db().execute("DELETE FROM sessions WHERE id = ? AND user_id = ? AND manual = 1", (session_id, g.user["id"]))
    get_db().commit()
    regenerate_and_report(g.user["id"], "Créneau de révision supprimé.", repair=True)
    return redirect(_safe_next(url_for("main.calendar")))


@bp.route("/matieres/<int:subject_id>/examens", methods=("POST",))
@login_required
def exam_add(subject_id):
    """Ajouter un examen à la main (en plus de ceux de l'emploi du temps)."""
    s = own_subject(subject_id)
    f = request.form
    back = url_for("main.subject_detail", subject_id=subject_id) + "#examens"
    try:
        d = date.fromisoformat(f.get("date", ""))
        start = datetime.combine(d, datetime.strptime(f.get("start") or "08:00", "%H:%M").time())
        end = datetime.combine(d, datetime.strptime(f.get("end") or "10:00", "%H:%M").time())
    except ValueError:
        flash("Date ou heures de l'examen invalides.", "error")
        return redirect(back)
    if d <= date.today():
        flash("L'examen doit être à une date à venir.", "error")
        return redirect(back)
    if end <= start:
        flash("L'heure de fin doit être après l'heure de début.", "error")
        return redirect(back)
    title = (f.get("title") or "").strip()[:200] or f"Examen de {s['name']}"
    get_db().execute("INSERT INTO events (user_id, title, start, end, source, kind, subject_id) "
                     "VALUES (?, ?, ?, ?, 'manuel', 'examen', ?)",
                     (g.user["id"], title, _iso(start), _iso(end), subject_id))
    get_db().commit()
    flash_all(sync_exam_dates(g.user["id"], {subject_id}))
    regenerate_and_report(g.user["id"], f"Examen ajouté le {_date_fr(d)}.", repair=True)
    return redirect(back)


@bp.route("/matieres/<int:subject_id>/examen/auto", methods=("POST",))
@login_required
def exam_auto(subject_id):
    """Abandonner la date choisie à la main : l'emploi du temps décide de nouveau."""
    s = own_subject(subject_id)
    get_db().execute("UPDATE subjects SET exam_manual = NULL, goal = 'exam' WHERE id = ?", (subject_id,))
    get_db().commit()
    messages = sync_exam_dates(g.user["id"])
    flash_all(messages or [f"La date d'examen de « {s['name']} » suit maintenant ton emploi du temps."])
    regenerate_and_report(g.user["id"], f"Programme de « {s['name']} » recalculé.", only={subject_id})
    return redirect(url_for("main.subject_detail", subject_id=subject_id))


@bp.route("/agenda/vider", methods=("POST",))
@login_required
def events_clear():
    get_db().execute("DELETE FROM events WHERE user_id = ?", (g.user["id"],))
    get_db().commit()
    regenerate_and_report(g.user["id"], "Agenda vidé.")
    return redirect(url_for("main.calendar"))


# ---------------------------------------------------------------- planning
@bp.route("/planning")
@login_required
def plan():
    try:
        ref = date.fromisoformat(request.args.get("semaine", ""))
    except ValueError:
        ref = date.today()
    monday = ref - timedelta(days=ref.weekday())
    end = monday + timedelta(days=7)
    db = get_db()
    uid = g.user["id"]
    now = datetime.now()
    prefs, _ = load_prefs(uid)
    sessions = db.execute(
        "SELECT se.*, su.name AS subject_name FROM sessions se JOIN subjects su ON su.id = se.subject_id "
        "WHERE se.user_id = ? AND se.start >= ? AND se.start < ? ORDER BY se.start",
        (uid, monday.isoformat(), end.isoformat())).fetchall()
    events = db.execute(
        "SELECT e.*, s.name AS subject_name FROM events e LEFT JOIN subjects s ON s.id = e.subject_id "
        "WHERE e.user_id = ? AND e.start < ? AND e.end > ? ORDER BY e.start",
        (uid, end.isoformat(), monday.isoformat())).fetchall()
    events = dedupe_events(events)  # un cours répété pour chaque groupe ne s'affiche qu'une fois

    # Plage horaire affichée : tes heures de travail, élargies si un événement déborde.
    lo = prefs.day_start.hour
    hi = prefs.day_end.hour + (1 if prefs.day_end.minute else 0)
    for r in list(sessions) + list(events):
        s, e = datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])
        if s.date() == e.date():
            lo = min(lo, s.hour)
            hi = max(hi, e.hour + (1 if e.minute else 0))
    lo, hi = max(0, lo), min(24, max(hi, lo + 1))
    g_min, g_len = lo * 60, (hi - lo) * 60

    def pct(minutes):
        return round((min(max(minutes, g_min), g_min + g_len) - g_min) / g_len * 100, 3)

    def block(kind, row, d):
        s, e = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        day0 = datetime.combine(d, time())
        sm = max(0, (s - day0).total_seconds() / 60)
        em = min(1440, (e - day0).total_seconds() / 60)
        top, bottom = pct(sm), pct(em)
        if bottom <= top:
            return None
        return {"type": kind, "row": row, "top": top, "height": bottom - top, "short": (em - sm) < 60}

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        ds = d.isoformat()
        day_events = [e for e in events if e["start"][:10] <= ds <= e["end"][:10]]
        holidays = sorted({matching.clean_title(e["title"]) for e in day_events if suggestions.is_holiday(e["title"])})
        blocks = [block("event", e, d) for e in day_events if not suggestions.is_holiday(e["title"])]
        blocks += [block("session", s, d) for s in sessions if s["start"][:10] == ds]
        days.append({
            "holidays": holidays,
            "date": d, "is_today": d == date.today(), "is_rest": d.weekday() in prefs.rest_days,
            "is_past": d < date.today(), "blocks": [b for b in blocks if b],
            "work_top": pct(prefs.day_start.hour * 60 + prefs.day_start.minute),
            "work_bottom": pct(prefs.day_end.hour * 60 + prefs.day_end.minute),
            "minutes": sum(int((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"]))
                               .total_seconds() // 60) for s in sessions if s["start"][:10] == ds),
        })
    now_min = now.hour * 60 + now.minute
    now_top = pct(now_min) if monday <= now.date() < end and lo * 60 <= now_min < hi * 60 else None

    return render_template("plan.html", days=days, monday=monday, prev=monday - timedelta(days=7),
                           next=end, today=date.today(), subjects=user_subjects(uid),
                           default_exam_events=default_exam_event_ids(uid),
                           hours=list(range(lo, hi)), now_top=now_top, prefs=prefs,
                           week_minutes=sum(d["minutes"] for d in days),
                           kinds=planner.KIND_LABELS, methods=planner.METHODS, now=_iso(now),
                           jours=JOURS_COURTS)


def _session_conflicts(start: datetime, end: datetime, busy, prefs, exam_day: date | None) -> bool:
    """Une séance déjà planifiée est-elle devenue impossible (nouveau cours, examen avancé…) ?"""
    if start.weekday() in prefs.rest_days:
        return True
    if start.time() < prefs.day_start or end.time() > prefs.day_end or start.date() != end.date():
        return True
    if exam_day and start.date() >= exam_day:
        return True
    pad = timedelta(minutes=prefs.buffer_minutes)
    return any(start < b.end + pad and b.start - pad < end for b in busy)


def regenerate(uid: int, only: set[int] | None = None, repair: bool = False):
    """Recalcule le planning à partir de l'état actuel (préférences, agenda, matières).

    `only` : ne replanifie que ces matières ; les séances des autres restent en place
    et sont traitées comme des créneaux occupés. Les créneaux de révision posés à la main
    par l'utilisateur (manual = 1) ne sont jamais déplacés. Retourne None s'il n'y a rien à planifier.

    `repair` (après un changement d'emploi du temps) : les séances encore possibles restent à
    leur place ; seules celles qui chevauchent un nouveau cours, tombent un jour de repos ou
    après l'examen sont replacées, avec le travail qui manquerait.
    """
    db = get_db()
    now = datetime.now()
    prefs, prow = load_prefs(uid)
    rows = db.execute("SELECT * FROM subjects WHERE user_id = ?", (uid,)).fetchall()
    comfort = {r["id"]: r["comfort"] for r in rows}
    if only is not None:
        rows = [r for r in rows if r["id"] in only]
    if not rows:
        return None
    subjects = [planner.SubjectIn(
        id=r["id"], name=r["name"], goal=r["goal"], comfort=r["comfort"],
        exam_date=date.fromisoformat(r["exam_date"]) if r["exam_date"] else None,
        weekly_hours=r["weekly_hours"], topics=subject_topics(r)) for r in rows]

    # Chapitres déjà étudiés (séances d'étude cochées) : on ne les replanifie pas.
    done_study = defaultdict(dict)
    for d in db.execute(
            "SELECT subject_id, topic, MAX(substr(start, 1, 10)) AS last,"
            " SUM((julianday(end) - julianday(start)) * 1440) AS minutes "
            "FROM sessions WHERE user_id = ? AND kind = 'study' AND done = 1 GROUP BY subject_id, topic", (uid,)):
        if d["minutes"] + 1 >= planner.study_minutes(comfort.get(d["subject_id"], 3)):
            done_study[d["subject_id"]][d["topic"]] = date.fromisoformat(d["last"])

    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    # Les vacances et jours fériés de l'emploi du temps sont des jours libres pour réviser.
    event_busy = [planner.Busy(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
                  for e in db.execute("SELECT title, start, end FROM events WHERE user_id = ? AND end >= ?",
                                      (uid, _iso(now)))
                  if not suggestions.is_holiday(e["title"])]
    replannable = db.execute(
        f"SELECT * FROM sessions WHERE user_id = ? AND done = 0 AND manual = 0 AND start >= ? "
        f"AND subject_id IN ({marks}) ORDER BY start", (uid, _iso(now), *ids)).fetchall()

    fixed: list[planner.PlannedSession] = []
    conflicting = 0
    if repair:
        locked = [planner.Busy(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                  for s in db.execute("SELECT start, end FROM sessions WHERE user_id = ? AND start >= ? "
                                      "AND (done = 1 OR manual = 1)", (uid, _iso(now)))]
        exam_days = {sub.id: sub.exam_date for sub in subjects if sub.goal == "exam"}
        to_delete = []
        for s in replannable:
            start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
            if _session_conflicts(start, end, event_busy + locked, prefs, exam_days.get(s["subject_id"])):
                to_delete.append(s["id"])
            else:
                fixed.append(planner.PlannedSession(s["subject_id"], start, end, s["topic"], s["kind"],
                                                    s["activity"], ref=s["id"]))
        conflicting = len(to_delete)
        db.executemany("DELETE FROM sessions WHERE id = ?", [(i,) for i in to_delete])
    else:
        db.execute(f"DELETE FROM sessions WHERE user_id = ? AND done = 0 AND manual = 0 AND start >= ? "
                   f"AND subject_id IN ({marks})", (uid, _iso(now), *ids))
    kept_ids = {f.ref for f in fixed}
    busy = event_busy + [planner.Busy(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                         for s in db.execute("SELECT id, start, end FROM sessions WHERE user_id = ? AND start >= ?",
                                             (uid, _iso(now)))
                         if s["id"] not in kept_ids]

    result = planner.generate_plan(subjects, prefs, busy, now, horizon_days=prow["horizon_weeks"] * 7,
                                   done_study=done_study, fixed=fixed)
    db.executemany("DELETE FROM sessions WHERE id = ?", [(f.ref,) for f in result.dropped])
    db.executemany(
        "INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(uid, s.subject_id, _iso(s.start), _iso(s.end), s.topic, s.kind, s.activity) for s in result.sessions])
    db.commit()
    result.kept = len(fixed) - len(result.dropped)
    result.replaced = conflicting + len(result.dropped)
    return result


def regenerate_and_report(uid: int, reason: str, only: set[int] | None = None, repair: bool = False,
                          quiet: bool = False):
    """Recalcule et explique le résultat à l'utilisateur (retour immédiat : loi de Doherty)."""
    result = regenerate(uid, only, repair)
    if result is None:
        if reason:
            flash(reason, "success")
        return
    planned = sum(int((s.end - s.start).total_seconds() // 60) for s in result.sessions)
    if repair:
        if not result.sessions and not result.replaced:
            if not quiet:
                flash(f"{reason} Ton planning n'a pas besoin de bouger.".strip(), "success")
        else:
            flash(f"{reason} Planning ajusté : {result.kept} séance(s) gardée(s) à leur place, "
                  f"{len(result.sessions)} replacée(s) ou ajoutée(s).".strip(), "success")
    else:
        flash(f"{reason} Planning mis à jour : {len(result.sessions)} séances, "
              f"{planner.fmt_minutes(planned)} de travail.".strip(), "success")
    for w in result.warnings:
        flash(w, "warning")


@bp.route("/planning/generer", methods=("POST",))
@login_required
def plan_generate():
    if not get_db().execute("SELECT 1 FROM subjects WHERE user_id = ?", (g.user["id"],)).fetchone():
        flash("Ajoute d'abord une matière.", "error")
        return redirect(url_for("main.subject_new"))
    regenerate_and_report(g.user["id"], "C'est fait.")
    return redirect(_safe_next(url_for("main.plan")))


@bp.route("/matieres/<int:subject_id>/programme", methods=("POST",))
@login_required
def subject_program(subject_id):
    """Programme de révision d'une seule matière (depuis sa page)."""
    s = own_subject(subject_id)
    f = request.form
    back = url_for("main.subject_detail", subject_id=subject_id) + "#programme"
    goal = f.get("goal") if f.get("goal") in GOALS else s["goal"]
    exam_manual, exam_source = None, ""
    exam_date = None
    if goal == "exam":
        manual, typed = resolve_exam_input(f, s)
        source = "manuel" if manual else s["exam_source"]
        if not typed:  # champ vidé : l'emploi du temps décide (épreuve, sinon dernière séance)
            typed, source = agenda_exam_for(subject_id)
        try:
            exam_date = date.fromisoformat(typed or "").isoformat()
        except ValueError:
            exam_date = None
        if not exam_date or exam_date <= date.today().isoformat():
            flash("Indique une date d'examen à venir pour construire le programme.", "error")
            return redirect(back)
        exam_manual, exam_source = manual, source
    try:
        weekly = max(0.5, min(40.0, float(f.get("weekly_hours", s["weekly_hours"]))))
    except ValueError:
        weekly = s["weekly_hours"]
    get_db().execute("UPDATE subjects SET goal=?, exam_date=?, comfort=?, weekly_hours=?, exam_manual=?, exam_source=? "
                     "WHERE id=?", (goal, exam_date, _int(f.get("comfort"), s["comfort"], 1, 5), weekly,
                                    exam_manual, exam_source, subject_id))
    get_db().commit()
    flash_all(sync_exam_dates(g.user["id"]))
    regenerate_and_report(g.user["id"], f"Programme de « {s['name']} » construit.", only={subject_id})
    return redirect(back)


@bp.route("/seances/<int:session_id>/fait", methods=("POST",))
@login_required
def session_toggle(session_id):
    db = get_db()
    db.execute("UPDATE sessions SET done = 1 - done WHERE id = ? AND user_id = ?", (session_id, g.user["id"]))
    db.commit()
    return redirect(_safe_next(url_for("main.plan")))


@bp.route("/planning/export.ics")
@login_required
def plan_export():
    rows = get_db().execute(
        "SELECT se.*, su.name AS subject_name FROM sessions se JOIN subjects su ON su.id = se.subject_id "
        "WHERE se.user_id = ? AND se.start >= ? ORDER BY se.start",
        (g.user["id"], (date.today() - timedelta(days=7)).isoformat())).fetchall()
    ics = calendar_ics.build_ics([{
        "uid": f"session-{r['id']}@planificateur-revisions",
        "title": f"{r['subject_name']} : {r['topic']}",
        "description": f"{planner.KIND_LABELS.get(r['kind'], r['kind'])} - {r['activity']}",
        "start": datetime.fromisoformat(r["start"]), "end": datetime.fromisoformat(r["end"]),
    } for r in rows])
    return Response(ics, mimetype="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=planning-revisions.ics"})
