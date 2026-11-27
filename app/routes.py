import json
import mimetypes
import os
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.utils import secure_filename

from . import calendar_ics, extractor, matching, planner, resources
from .auth import LEVELS, login_required
from .db import get_db

bp = Blueprint("main", __name__)

GOALS = {"exam": "Préparer un examen", "a_jour": "Être à jour dans le cours"}
COMFORT = {1: "Très difficile", 2: "Difficile", 3: "Moyen", 4: "À l'aise", 5: "Très à l'aise"}
JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
EVENT_KINDS = {"cours": "Cours en classe", "revision": "Révision que je prévois", "autre": "Autre occupation"}


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


def _profile_sig(user) -> str:
    return f"{user['level']}|{user['school']}"


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


def create_subject(name: str, goal="a_jour", exam_date=None, comfort=3, weekly_hours=2.0, topics=None) -> int:
    if topics is None:
        topics, _ = _initial_topics(name)
    cur = get_db().execute(
        "INSERT INTO subjects (user_id, name, goal, exam_date, comfort, weekly_hours, topics) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], name[:100], goal, exam_date, comfort, weekly_hours, json.dumps(topics, ensure_ascii=False)))
    get_db().commit()
    return cur.lastrowid


def resolve_subject(title: str, explicit: str | None, create: bool = True, subjects=None):
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
    sid = create_subject(name)
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
    found, errors = resources.find_resources(subject["name"], subject_topics(subject), kw,
                                             level=user["level"], school=user["school"])
    saved = {r["url"] for r in db.execute("SELECT url FROM resources WHERE subject_id = ? AND saved = 1",
                                          (subject["id"],))}
    db.execute("DELETE FROM resources WHERE subject_id = ? AND saved = 0", (subject["id"],))
    db.executemany(
        "INSERT INTO resources (subject_id, title, url, source, snippet, kind, thumbnail, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(subject["id"], r["title"][:300], r["url"], r["source"], r["snippet"], r["kind"], r["thumbnail"], r["meta"])
         for r in found if r["url"] not in saved])
    db.execute("UPDATE subjects SET resources_status = ?, resources_sig = ? WHERE id = ?",
               ("error" if errors and len(found) <= 6 else "ok", _profile_sig(user), subject["id"]))
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
        data, errors = _subject_form()
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("subjects/form.html", s=request.form, goals=GOALS, comfort=COMFORT, new=True,
                                   force_new=request.form.get("force_new"))
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

        sid = create_subject(data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"],
                             topics=data["topics"] or [])
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
    return render_template("subjects/form.html", s=prefill, goals=GOALS, comfort=COMFORT, new=True,
                           force_new=request.args.get("nouvelle"))


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
    res = db.execute("SELECT * FROM resources WHERE subject_id = ? ORDER BY id", (subject_id,)).fetchall()
    stats = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(done), 0) AS done,"
        " COALESCE(SUM((julianday(end) - julianday(start)) * 1440), 0) AS minutes,"
        " COALESCE(SUM(CASE WHEN done THEN (julianday(end) - julianday(start)) * 1440 END), 0) AS done_min "
        "FROM sessions WHERE subject_id = ?", (subject_id,)).fetchone()
    return render_template(
        "subjects/detail.html", s=s, topics=subject_topics(s), goals=GOALS, comfort=COMFORT, now=now,
        kinds=planner.KIND_LABELS, stats=stats, hint=hint, today=date.today(),
        classes=db.execute("SELECT * FROM events WHERE subject_id = ? AND user_id = ? AND end >= ? "
                           "ORDER BY start LIMIT 6", (subject_id, g.user["id"], now)).fetchall(),
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
        days_left=(date.fromisoformat(s["exam_date"]) - date.today()).days if s["exam_date"] else None,
        profile_changed=bool(res) and s["resources_sig"] not in ("", _profile_sig(g.user)),
        level_label=LEVELS.get(g.user["level"], ""), web_enabled=current_app.config.get("ENABLE_WEB"))


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
        others = [o for o in user_subjects(g.user["id"]) if o["id"] != subject_id]
        twin, score = matching.best_match(data["name"], others)
        get_db().execute(
            "UPDATE subjects SET name=?, goal=?, exam_date=?, comfort=?, weekly_hours=?, topics=? WHERE id=?",
            (data["name"], data["goal"], data["exam_date"], data["comfort"], data["weekly_hours"],
             json.dumps(data["topics"] or planner.DEFAULT_TOPICS, ensure_ascii=False), subject_id))
        get_db().commit()
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
    events, skipped = calendar_ics.parse_ics(text, datetime.combine(date.today(), time()))
    db = get_db()
    uid = g.user["id"]
    if request.form.get("replace"):
        db.execute("DELETE FROM events WHERE user_id = ? AND source = 'ics'", (uid,))
    create = bool(request.form.get("create_missing"))
    subjects = list(user_subjects(uid))
    cache: dict[str, tuple] = {}
    created, linked, new_ids = [], 0, []
    rows = []
    for e in events:
        key = matching.normalize(e["title"])
        if key not in cache:
            match, _ = matching.best_match(e["title"], subjects)
            if match is None and create and key:
                sid = create_subject(matching.clean_title(e["title"]), topics=list(planner.DEFAULT_TOPICS))
                match = own_subject(sid)
                subjects.append(match)
                created.append(match["name"])
                new_ids.append(sid)
            cache[key] = match
        match = cache[key]
        linked += match is not None
        rows.append((uid, e["title"][:200], _iso(e["start"]), _iso(e["end"]),
                     match["id"] if match else None, "cours" if match else "autre"))
    db.executemany("INSERT INTO events (user_id, title, start, end, source, subject_id, kind) "
                   "VALUES (?, ?, ?, ?, 'ics', ?, ?)", rows)
    db.commit()
    msg = f"{len(events)} créneau(x) importé(s), dont {linked} rattaché(s) à une matière."
    if created:
        msg += f" Nouvelles matières : {', '.join(created)}."
    if skipped:
        msg += f" {skipped} événement(s) « journée entière » ignoré(s)."
    refresh_resources_async(uid, new_ids)
    regenerate_and_report(uid, msg)
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
    kind = f.get("kind") if f.get("kind") in EVENT_KINDS else "autre"
    title = (f.get("title") or "").strip()[:200]
    uid = g.user["id"]
    db = get_db()
    slots = [(s + timedelta(weeks=k), e + timedelta(weeks=k)) for k in range(weeks)]

    if kind == "autre":
        db.executemany("INSERT INTO events (user_id, title, start, end, source, kind) VALUES (?, ?, ?, ?, 'manuel', 'autre')",
                       [(uid, title or "Occupé", _iso(a), _iso(b)) for a, b in slots])
        db.commit()
        regenerate_and_report(uid, f"{weeks} créneau(x) occupé(s) ajouté(s).")
        return redirect(url_for("main.calendar"))

    if not title and not (f.get("subject") or "").isdigit():
        flash("Donne un nom au cours ou choisis une matière.", "error")
        return redirect(url_for("main.calendar"))
    subject, status, score = resolve_subject(title, f.get("subject") or "auto")
    title = title or subject["name"]
    msg = _association_message(title, subject, status, score)
    if status == "created":
        refresh_resources_async(uid, [subject["id"]])
    if kind == "cours":
        db.executemany("INSERT INTO events (user_id, title, start, end, source, kind, subject_id) "
                       "VALUES (?, ?, ?, ?, 'manuel', 'cours', ?)",
                       [(uid, title, _iso(a), _iso(b), subject["id"]) for a, b in slots])
        label = f"{weeks} cours ajouté(s)."
    else:  # créneau de révision choisi par l'utilisateur : une séance fixe, jamais déplacée
        db.executemany("INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity, manual) "
                       "VALUES (?, ?, ?, ?, ?, 'review', ?, 1)",
                       [(uid, subject["id"], _iso(a), _iso(b),
                         "Révision libre" if title == subject["name"] else title,
                         "Créneau de révision choisi par toi") for a, b in slots])
        label = f"{weeks} créneau(x) de révision ajouté(s)."
    db.commit()
    regenerate_and_report(uid, " ".join(x for x in (msg, label) if x))
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
    get_db().execute("UPDATE events SET subject_id = ?, kind = ? WHERE user_id = ? AND title = ?",
                     (sid, "cours" if sid else "autre", g.user["id"], ev["title"]))
    get_db().commit()
    flash("Rattachement mis à jour pour toutes les occurrences de ce cours.", "success")
    return redirect(url_for("main.calendar"))


@bp.route("/agenda/<int:event_id>/supprimer", methods=("POST",))
@login_required
def event_delete(event_id):
    get_db().execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, g.user["id"]))
    get_db().commit()
    regenerate_and_report(g.user["id"], "Créneau supprimé.")
    return redirect(_safe_next(url_for("main.calendar")))


@bp.route("/seances/<int:session_id>/supprimer", methods=("POST",))
@login_required
def session_delete(session_id):
    get_db().execute("DELETE FROM sessions WHERE id = ? AND user_id = ? AND manual = 1", (session_id, g.user["id"]))
    get_db().commit()
    regenerate_and_report(g.user["id"], "Créneau de révision supprimé.")
    return redirect(_safe_next(url_for("main.calendar")))


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
    now_min = now.hour * 60 + now.minute
    now_top = pct(now_min) if monday <= now.date() < end and lo * 60 <= now_min < hi * 60 else None

    return render_template("plan.html", days=days, monday=monday, prev=monday - timedelta(days=7),
                           next=end, today=date.today(), subjects=user_subjects(uid),
                           hours=list(range(lo, hi)), now_top=now_top, prefs=prefs,
                           week_minutes=sum(d["minutes"] for d in days),
                           kinds=planner.KIND_LABELS, methods=planner.METHODS, now=_iso(now),
                           jours=JOURS_COURTS)


def regenerate(uid: int, only: set[int] | None = None):
    """Recalcule le planning à partir de l'état actuel (préférences, agenda, matières).

    `only` : ne replanifie que ces matières ; les séances des autres restent en place
    et sont traitées comme des créneaux occupés. Les créneaux de révision posés à la main
    par l'utilisateur (manual = 1) ne sont jamais déplacés. Retourne None s'il n'y a rien à planifier.
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
    db.execute(f"DELETE FROM sessions WHERE user_id = ? AND done = 0 AND manual = 0 AND start >= ? "
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
    return redirect(_safe_next(url_for("main.plan")))


@bp.route("/matieres/<int:subject_id>/programme", methods=("POST",))
@login_required
def subject_program(subject_id):
    """Programme de révision d'une seule matière (depuis sa page)."""
    s = own_subject(subject_id)
    f = request.form
    back = url_for("main.subject_detail", subject_id=subject_id) + "#programme"
    goal = f.get("goal") if f.get("goal") in GOALS else s["goal"]
    exam_date = s["exam_date"]
    if goal == "exam":
        try:
            exam_date = date.fromisoformat(f.get("exam_date", "")).isoformat()
        except ValueError:
            exam_date = None
        if not exam_date or exam_date <= date.today().isoformat():
            flash("Indique une date d'examen à venir pour construire le programme.", "error")
            return redirect(back)
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
