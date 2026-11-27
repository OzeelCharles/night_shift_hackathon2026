import json
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)
from werkzeug.utils import secure_filename

from . import calendar_ics, extractor, planner, resources
from .auth import login_required
from .db import get_db

bp = Blueprint("main", __name__)

GOALS = {"exam": "Préparer un examen", "a_jour": "Être à jour dans le cours"}
COMFORT = {1: "Très difficile", 2: "Difficile", 3: "Moyen", 4: "À l'aise", 5: "Très à l'aise"}
JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


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


def subject_topics(s) -> list[str]:
    try:
        return json.loads(s["topics"])
    except (TypeError, ValueError):
        return []


def _parse_topics(text: str) -> list[str]:
    return extractor.clean_topics(text.splitlines(), limit=60)


def _subject_form():
    f = request.form
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


def refresh_resources(subject) -> list[str]:
    if not current_app.config.get("ENABLE_WEB"):
        return ["Recherche internet désactivée."]
    db = get_db()
    excerpts = " ".join(r["excerpt"] for r in db.execute(
        "SELECT excerpt FROM documents WHERE subject_id = ?", (subject["id"],)))
    kw = extractor.keywords(excerpts, 5) if excerpts else None
    found, errors = resources.find_resources(subject["name"], subject_topics(subject), kw)
    db.execute("DELETE FROM resources WHERE subject_id = ?", (subject["id"],))
    db.executemany("INSERT INTO resources (subject_id, title, url, source, snippet) VALUES (?, ?, ?, ?, ?)",
                   [(subject["id"], r["title"][:300], r["url"], r["source"], r.get("snippet", ""))
                    for r in found])
    db.commit()
    return errors


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
        data, errors = _subject_form()
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("subjects/form.html", s=request.form, goals=GOALS, comfort=COMFORT, new=True)
        db = get_db()
        cur = db.execute(
            "INSERT INTO subjects (user_id, name, goal, exam_date, comfort, weekly_hours, topics) "
            "VALUES (?, ?, ?, ?, ?, ?, '[]')",
            (g.user["id"], data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"]))
        sid = cur.lastrowid
        db.commit()
        doc_topics, messages = _save_documents(sid, request.files.getlist("documents"))
        topics = data["topics"] or extractor.clean_topics(doc_topics, limit=40)
        source = "saisis" if data["topics"] else "extraits des documents"
        if not topics and current_app.config.get("ENABLE_WEB"):
            try:
                topics = extractor.clean_topics(resources.wikipedia_outline(data["name"]), limit=15)
                source = "proposés à partir de Wikipédia"
            except Exception:
                topics = []
        if not topics:
            topics = list(planner.DEFAULT_TOPICS)
            source = "génériques (ajoute tes chapitres pour un planning plus précis)"
        db.execute("UPDATE subjects SET topics = ? WHERE id = ?", (json.dumps(topics, ensure_ascii=False), sid))
        db.commit()
        for m in messages:
            flash(m, "info")
        for e in refresh_resources(own_subject(sid)):
            flash(e, "info")
        regenerate_and_report(g.user["id"], f"Matière créée avec {len(topics)} chapitres {source}.")
        return redirect(url_for("main.plan", matiere=sid))
    return render_template("subjects/form.html", s={}, goals=GOALS, comfort=COMFORT, new=True)


@bp.route("/matieres/<int:subject_id>")
@login_required
def subject_detail(subject_id):
    s = own_subject(subject_id)
    db = get_db()
    docs = db.execute("SELECT * FROM documents WHERE subject_id = ? ORDER BY created_at DESC", (subject_id,)).fetchall()
    res = db.execute("SELECT * FROM resources WHERE subject_id = ? ORDER BY id", (subject_id,)).fetchall()
    sessions = db.execute("SELECT * FROM sessions WHERE subject_id = ? ORDER BY start", (subject_id,)).fetchall()
    return render_template("subjects/detail.html", s=s, topics=subject_topics(s), docs=docs, resources=res,
                           sessions=sessions, goals=GOALS, comfort=COMFORT, now=_iso(datetime.now()),
                           kinds=planner.KIND_LABELS)


@bp.route("/matieres/<int:subject_id>/modifier", methods=("GET", "POST"))
@login_required
def subject_edit(subject_id):
    s = own_subject(subject_id)
    if request.method == "POST":
        data, errors = _subject_form()
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("subjects/form.html", s=request.form, goals=GOALS, comfort=COMFORT, new=False,
                                   subject_id=subject_id)
        get_db().execute(
            "UPDATE subjects SET name=?, goal=?, exam_date=?, comfort=?, weekly_hours=?, topics=? WHERE id=?",
            (data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"],
             json.dumps(data["topics"] or planner.DEFAULT_TOPICS, ensure_ascii=False), subject_id))
        get_db().commit()
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
    db.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    db.commit()
    regenerate_and_report(g.user["id"], "Matière supprimée.")
    return redirect(url_for("main.dashboard"))


def _remove_file(path):
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    real = os.path.realpath(path)
    if real.startswith(root + os.sep) and os.path.exists(real):
        os.remove(real)


@bp.route("/matieres/<int:subject_id>/documents", methods=("POST",))
@login_required
def subject_upload(subject_id):
    s = own_subject(subject_id)
    found, messages = _save_documents(subject_id, request.files.getlist("documents"))
    current = subject_topics(s)
    if current == planner.DEFAULT_TOPICS:
        current = []
    lower = {t.lower() for t in current}
    added = [t for t in extractor.clean_topics(found, 40) if t.lower() not in lower]
    if added:
        get_db().execute("UPDATE subjects SET topics = ? WHERE id = ?",
                         (json.dumps((current + added)[:60], ensure_ascii=False), subject_id))
        get_db().commit()
    for m in messages:
        flash(m, "info")
    if added:
        regenerate_and_report(g.user["id"], f"{len(added)} nouveau(x) chapitre(s) ajouté(s).")
    else:
        flash("Aucun nouveau chapitre trouvé dans ces documents.", "info")
    return redirect(url_for("main.subject_detail", subject_id=subject_id))


@bp.route("/documents/<int:doc_id>/supprimer", methods=("POST",))
@login_required
def document_delete(doc_id):
    d = get_db().execute("SELECT d.* FROM documents d JOIN subjects s ON s.id = d.subject_id "
                         "WHERE d.id = ? AND s.user_id = ?", (doc_id, g.user["id"])).fetchone()
    if d is None:
        abort(404)
    _remove_file(d["stored_path"])
    get_db().execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    get_db().commit()
    flash("Document supprimé.", "success")
    return redirect(url_for("main.subject_detail", subject_id=d["subject_id"]))


@bp.route("/matieres/<int:subject_id>/ressources", methods=("POST",))
@login_required
def subject_resources(subject_id):
    errors = refresh_resources(own_subject(subject_id))
    for e in errors:
        flash(e, "error")
    if not errors:
        flash("Ressources mises à jour.", "success")
    return redirect(url_for("main.subject_detail", subject_id=subject_id) + "#ressources")


# ---------------------------------------------------------------- agenda
@bp.route("/agenda")
@login_required
def calendar():
    events = get_db().execute("SELECT * FROM events WHERE user_id = ? AND end >= ? ORDER BY start LIMIT 300",
                              (g.user["id"], date.today().isoformat())).fetchall()
    return render_template("calendar.html", events=events)


@bp.route("/agenda/import", methods=("POST",))
@login_required
def calendar_import():
    f = request.files.get("ics")
    if not f or not f.filename.lower().endswith(".ics"):
        flash("Choisis un fichier .ics (export de Google Agenda, Outlook, ENT...).", "error")
        return redirect(url_for("main.calendar"))
    text = f.read().decode("utf-8", errors="ignore")
    start = datetime.combine(date.today(), time())
    events, skipped = calendar_ics.parse_ics(text, start)
    db = get_db()
    if request.form.get("replace"):
        db.execute("DELETE FROM events WHERE user_id = ? AND source = 'ics'", (g.user["id"],))
    db.executemany("INSERT INTO events (user_id, title, start, end, source) VALUES (?, ?, ?, ?, 'ics')",
                   [(g.user["id"], e["title"][:200], _iso(e["start"]), _iso(e["end"])) for e in events])
    db.commit()
    msg = f"{len(events)} créneau(x) occupé(s) importé(s)."
    if skipped:
        msg += f" {skipped} événement(s) « journée entière » ignoré(s)."
    regenerate_and_report(g.user["id"], msg)
    return redirect(url_for("main.calendar"))


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
    title = (f.get("title") or "Occupé").strip()[:200]
    get_db().executemany(
        "INSERT INTO events (user_id, title, start, end, source) VALUES (?, ?, ?, ?, 'manuel')",
        [(g.user["id"], title, _iso(s + timedelta(weeks=k)), _iso(e + timedelta(weeks=k))) for k in range(weeks)])
    get_db().commit()
    regenerate_and_report(g.user["id"], f"{weeks} créneau(x) ajouté(s).")
    return redirect(url_for("main.calendar"))


@bp.route("/agenda/<int:event_id>/supprimer", methods=("POST",))
@login_required
def event_delete(event_id):
    get_db().execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, g.user["id"]))
    get_db().commit()
    regenerate_and_report(g.user["id"], "Créneau supprimé.")
    return redirect(url_for("main.calendar"))


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
    events = db.execute("SELECT * FROM events WHERE user_id = ? AND start < ? AND end > ? ORDER BY start",
                        (uid, end.isoformat(), monday.isoformat())).fetchall()

    # Plage horaire affichée : vos heures de travail, élargies si un événement déborde.
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
        blocks = [block("event", e, d) for e in events if e["start"][:10] <= ds <= e["end"][:10]]
        blocks += [block("session", s, d) for s in sessions if s["start"][:10] == ds]
        days.append({
            "date": d, "is_today": d == date.today(), "is_rest": d.weekday() in prefs.rest_days,
            "is_past": d < date.today(), "blocks": [b for b in blocks if b],
            "work_top": pct(prefs.day_start.hour * 60 + prefs.day_start.minute),
            "work_bottom": pct(prefs.day_end.hour * 60 + prefs.day_end.minute),
            "minutes": sum(int((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"]))
                               .total_seconds() // 60) for s in sessions if s["start"][:10] == ds),
        })
    now_top = pct(now.hour * 60 + now.minute) if monday <= now.date() < end and lo * 60 <= now.hour * 60 + now.minute < hi * 60 else None

    subjects = db.execute("SELECT id, name FROM subjects WHERE user_id = ? ORDER BY name", (uid,)).fetchall()
    panel = None
    sel = request.args.get("matiere", type=int)
    if sel:
        s = own_subject(sel)
        stats = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(done), 0) AS done,"
            " COALESCE(SUM((julianday(end) - julianday(start)) * 1440), 0) AS minutes,"
            " COALESCE(SUM(CASE WHEN done THEN (julianday(end) - julianday(start)) * 1440 END), 0) AS done_min "
            "FROM sessions WHERE subject_id = ?", (sel,)).fetchone()
        upcoming = db.execute("SELECT * FROM sessions WHERE subject_id = ? AND end >= ? ORDER BY start LIMIT 8",
                              (sel, _iso(now))).fetchall()
        panel = {"s": s, "stats": stats, "upcoming": upcoming, "topics": subject_topics(s),
                 "days_left": (date.fromisoformat(s["exam_date"]) - date.today()).days if s["exam_date"] else None}

    return render_template("plan.html", days=days, monday=monday, prev=monday - timedelta(days=7),
                           next=end, today=date.today(), subjects=subjects, panel=panel, sel=sel,
                           hours=list(range(lo, hi)), now_top=now_top, prefs=prefs,
                           week_minutes=sum(d["minutes"] for d in days), goals=GOALS, comfort=COMFORT,
                           kinds=planner.KIND_LABELS, methods=planner.METHODS, now=_iso(now),
                           jours=JOURS_COURTS)


def regenerate(uid: int, only: set[int] | None = None):
    """Recalcule le planning à partir de l'état actuel (préférences, agenda, matières).

    `only` : ne replanifie que ces matières ; les séances des autres restent en place
    et sont traitées comme des créneaux occupés. Retourne None s'il n'y a rien à planifier.
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

    # On garde l'historique et les séances déjà faites ; on replanifie le reste.
    ids = [r["id"] for r in rows]
    db.execute(f"DELETE FROM sessions WHERE user_id = ? AND done = 0 AND start >= ? "
               f"AND subject_id IN ({','.join('?' * len(ids))})", (uid, _iso(now), *ids))
    busy = [planner.Busy(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
            for e in db.execute("SELECT start, end FROM events WHERE user_id = ? AND end >= ?", (uid, _iso(now)))]
    busy += [planner.Busy(datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
             for s in db.execute("SELECT start, end FROM sessions WHERE user_id = ? AND start >= ?", (uid, _iso(now)))]

    result = planner.generate_plan(subjects, prefs, busy, now, horizon_days=prow["horizon_weeks"] * 7,
                                   done_study=done_study)
    db.executemany(
        "INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(uid, s.subject_id, _iso(s.start), _iso(s.end), s.topic, s.kind, s.activity) for s in result.sessions])
    db.commit()
    return result


def regenerate_and_report(uid: int, reason: str, only: set[int] | None = None):
    """Recalcule et explique le résultat à l'utilisateur (retour immédiat : loi de Doherty)."""
    result = regenerate(uid, only)
    if result is None:
        flash(reason, "success")
        return
    planned = sum(int((s.end - s.start).total_seconds() // 60) for s in result.sessions)
    flash(f"{reason} Planning mis à jour : {len(result.sessions)} séances, "
          f"{planner.fmt_minutes(planned)} de travail.", "success")
    for w in result.warnings:
        flash(w, "warning")


@bp.route("/planning/generer", methods=("POST",))
@login_required
def plan_generate():
    if not get_db().execute("SELECT 1 FROM subjects WHERE user_id = ?", (g.user["id"],)).fetchone():
        flash("Ajoute d'abord une matière.", "error")
        return redirect(url_for("main.subject_new"))
    regenerate_and_report(g.user["id"], "C'est fait.")
    return redirect(url_for("main.plan"))


@bp.route("/matieres/<int:subject_id>/programme", methods=("POST",))
@login_required
def subject_program(subject_id):
    """Programme de révision d'une seule matière, depuis le panneau du planning."""
    s = own_subject(subject_id)
    f = request.form
    goal = f.get("goal") if f.get("goal") in GOALS else s["goal"]
    exam_date = s["exam_date"]
    if goal == "exam":
        try:
            exam_date = date.fromisoformat(f.get("exam_date", "")).isoformat()
        except ValueError:
            exam_date = None
        if not exam_date or exam_date <= date.today().isoformat():
            flash("Indique une date d'examen à venir pour construire le programme.", "error")
            return redirect(url_for("main.plan", matiere=subject_id))
    else:
        exam_date = None
    try:
        weekly = max(0.5, min(40.0, float(f.get("weekly_hours", s["weekly_hours"]))))
    except ValueError:
        weekly = s["weekly_hours"]
    get_db().execute("UPDATE subjects SET goal=?, exam_date=?, comfort=?, weekly_hours=? WHERE id=?",
                     (goal, exam_date, _int(f.get("comfort"), s["comfort"], 1, 5), weekly, subject_id))
    get_db().commit()
    regenerate_and_report(g.user["id"], f"Programme de « {s['name']} » construit.", only={subject_id})
    first = get_db().execute("SELECT MIN(start) FROM sessions WHERE subject_id = ? AND start >= ?",
                             (subject_id, _iso(datetime.now()))).fetchone()[0]
    return redirect(url_for("main.plan", matiere=subject_id, semaine=first[:10] if first else None))


@bp.route("/seances/<int:session_id>/fait", methods=("POST",))
@login_required
def session_toggle(session_id):
    db = get_db()
    db.execute("UPDATE sessions SET done = 1 - done WHERE id = ? AND user_id = ?", (session_id, g.user["id"]))
    db.commit()
    nxt = request.form.get("next", "")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = url_for("main.plan")
    return redirect(nxt)


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
