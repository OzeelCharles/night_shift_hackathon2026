"""Outils de l'assistant fournis par le cœur : planning du jour, matières et chapitres, notes, graphiques.

Lecture seule : aucun outil ne modifie les données. Chaque outil reçoit l'utilisateur connecté et ne lit
que ses données.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.business import agenda_analysis, exams, matching, planner, rescheduling
from backend.business import assistant as rules
from backend.business import grades as grade_rules
from backend.business.subjects import topics_of
from backend.business.text import date_fr, day_fr, iso
from backend.core.tools import Tool, params, text_param
from backend.dao import agenda as agenda_dao
from backend.dao import sessions as sessions_dao
from backend.dao import subjects as subjects_dao

from backend.dao import rag as rag_dao

from . import dossier_service, rag_service, reschedule_service

SUBJECT = text_param("Nom de la matière, tel que l'étudiant l'a écrit (ex. « proba », « Économétrie »).")


def _hm(value: str) -> str:
    return value[11:16]


def _session_row(s) -> dict:
    return {"jour": day_fr(s["start"]), "heure": f"{_hm(s['start'])}-{_hm(s['end'])}", "matiere": s["subject_name"],
            "chapitre": s["topic"], "type": planner.KIND_LABELS.get(s["kind"], s["kind"]),
            "activite": s["activity"], "faite": bool(s["done"])}


def _studied(user_id) -> dict[int, dict[str, str]]:
    """Chapitres déjà étudiés (séances d'étude cochées) : {subject_id: {chapitre: date}}."""
    out: dict[int, dict[str, str]] = {}
    for d in sessions_dao.done_study_minutes(user_id):
        out.setdefault(d["subject_id"], {})[d["topic"]] = d["last"]
    return out


def find_subject(user_id, name: str):
    """(matière, None) ou (None, résultat d'erreur à renvoyer au modèle)."""
    rows = subjects_dao.list_for_user(user_id)
    if not rows:
        return None, {"erreur": "L'étudiant n'a encore créé aucune matière."}
    row, _ = matching.best_match(name or "", rows)
    if row is None:
        return None, {"erreur": f"Aucune matière ne correspond à « {name} ».",
                      "matieres_de_l_etudiant": [r["name"] for r in rows]}
    return row, None


# ---------------------------------------------------------------- programme du jour
def programme_du_jour(user, **_) -> dict:
    uid, today = user["id"], date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    now = iso(datetime.now())
    studied = _studied(uid)
    exams_soon = []
    for s in subjects_dao.with_progress(uid, now):
        left = exams.days_left(s["exam_date"]) if s["goal"] == "exam" else None
        if left is None or left < 0:
            continue
        exams_soon.append({
            "matiere": s["name"], "examen": date_fr(s["exam_date"]), "dans_jours": left,
            "source_de_la_date": exams.SOURCES.get(s["exam_source"] or "", "date choisie par toi"),
            "aisance_sur_5": s["comfort"], "seances_manquees": s["missed"] or 0,
            "chapitres_pas_encore_etudies": [t for t in topics_of(s) if t not in studied.get(s["id"], {})][:12],
        })
    courses = [{"heure": f"{_hm(e['start'])}-{_hm(e['end'])}", "intitule": e["title"]}
               for e in agenda_analysis.dedupe(agenda_dao.in_window(uid, today.isoformat(), tomorrow))
               if not agenda_analysis.is_holiday(e["title"])]
    seances = [_session_row(s) for s in sessions_dao.between(uid, today.isoformat(), tomorrow)]
    return {
        "date": day_fr(today),
        "seances_de_revision_aujourd_hui": seances,
        "aucune_seance_prevue": not seances,
        "cours_et_evenements_aujourd_hui": courses,
        "examens_a_venir": sorted(exams_soon, key=lambda e: e["dans_jours"])[:8],
        "prochaines_seances": [_session_row(s) for s in sessions_dao.upcoming(uid, tomorrow, 5)],
    }


# ---------------------------------------------------------------- matières et chapitres
def chapitres(user, matiere: str = "", **_) -> dict:
    s, error = find_subject(user["id"], matiere)
    if error:
        return error
    done = _studied(user["id"]).get(s["id"], {})
    left = exams.days_left(s["exam_date"]) if s["goal"] == "exam" else None
    return {
        "matiere": s["name"],
        "examen": f"{date_fr(s['exam_date'])} (dans {left} jours)" if left is not None and left >= 0 else "aucun à venir",
        "aisance_sur_5": s["comfort"],
        "chapitres": [{"chapitre": t, "deja_etudie": t in done, "etudie_le": date_fr(done[t]) if t in done else None}
                      for t in topics_of(s)],
    }


# ---------------------------------------------------------------- notes (dossier scolaire)
def _grade_row(g) -> dict:
    return {"intitule": g["label"] or "note", "note": f"{g['value']:g}/{g['out_of']:g}",
            "sur_20": round(grade_rules.on_twenty(g), 2), "coefficient": g["coefficient"], "date": date_fr(g["day"])}


def _pick_course(courses, matiere: str):
    return matching.best_match(matiere, courses, key=lambda c: c.name)[0]


def notes(user, matiere: str = "", **_) -> dict:
    courses = dossier_service.courses(user["id"])
    overall = grade_rules.overall(courses)
    if matiere:
        c = _pick_course(courses, matiere)
        if c is None:
            return {"erreur": f"Aucune matière du dossier ne correspond à « {matiere} ».",
                    "matieres_du_dossier": [x.name for x in courses]}
        courses = [c]
    return {
        "moyenne_generale_sur_20": round(overall, 2) if overall is not None else None,
        "matieres": [{
            "matiere": c.name,
            "moyenne_sur_20": round(c.average, 2) if c.average is not None else None,
            "effet_derniere_note": round(c.progress, 2) if c.progress is not None else None,
            "nombre_de_notes": len(c.grades),
            "notes": [_grade_row(g) for g in c.grades[-8:]],
        } for c in courses],
        "remarque": "Matières sans note : l'étudiant n'a encore rien saisi dans son dossier scolaire."
        if any(not c.grades for c in courses) else "",
    }


def graphique_notes(user, matiere: str = "", **_) -> dict:
    courses = dossier_service.courses(user["id"])
    if matiere:
        c = _pick_course(courses, matiere)
        if c is None or not c.grades:
            return {"erreur": f"Pas de note enregistrée pour « {matiere} » dans le dossier scolaire."}
        points = [{"label": date_fr(g["day"]), "value": grade_rules.on_twenty(g)} for g in c.grades]
        title = f"Notes en {c.name} (sur 20)"
    else:
        history = grade_rules.overall_history(courses)
        if not history:
            return {"erreur": "Aucune note dans le dossier scolaire."}
        points = [{"label": h["label"], "value": h["value"]} for h in history]
        title = "Moyenne générale (sur 20)"
    return {"titre": title, "premier": round(points[0]["value"], 2), "dernier": round(points[-1]["value"], 2),
            "nombre_de_points": len(points), "chart": rules.chart(title, points)}


# ---------------------------------------------------------------- supports de cours (RAG)
def chercher_dans_mes_cours(user, question: str = "", matiere: str = "", **_) -> dict:
    subject_id = None
    if matiere:
        s, error = find_subject(user["id"], matiere)
        if error:
            return error
        subject_id = s["id"]
    found = rag_service.search(user["id"], question or matiere, subject_id)
    waiting = ""
    if found["en_cours"]:
        waiting = (f"Supports en cours de préparation pour la recherche ({', '.join(found['en_cours'][:5])}) : "
                   "dis à l'étudiant qu'ils seront consultables dans quelques minutes.")
    if not found["passages"]:
        if waiting:
            return {"resultat": "Aucun passage disponible pour l'instant.", "attention": waiting}
        if not rag_dao.stats(user["id"])["chunks"]:
            return {"resultat": "L'étudiant n'a déposé aucun support de cours lisible. Il peut en ajouter sur la "
                                "page de la matière (PDF, diapositives, photos de notes)."}
        return {"resultat": "Aucun passage de ses supports ne correspond à cette question"
                            + (f" en {matiere}." if matiere else ".")}
    out = {
        "passages": [{k: p[k] for k in ("source", "document", "matiere", "page", "section", "texte")}
                     for p in found["passages"]],
        "sources": found["sources"],
        "consigne": "Réponds en t'appuyant sur ces passages et cite-les avec leur numéro : [S1], [S2]…",
    }
    subjects = sorted({p["matiere"] for p in found["passages"]})
    notes = [waiting] if waiting else []
    if not matiere and len(subjects) > 1:
        notes.append(f"Passages de plusieurs matières ({', '.join(subjects)}) : si la question est ambiguë, "
                     "demande de quelle matière il s'agit.")
    if notes:
        out["attention"] = " ".join(notes)
    return out


# ---------------------------------------------------------------- séances ratées
def seances_a_rattraper(user, matiere: str = "", **_) -> dict:
    missed, error = reschedule_service.to_catch_up(user["id"], matiere)
    if error:
        return error
    later, _ = reschedule_service.to_catch_up(user["id"], matiere, later_today=True)
    return {"seances_ratees": [{"id": r["id"], "seance": rescheduling.session_line(r)} for r in missed],
            "a_venir_aujourd_hui": [{"id": r["id"], "seance": rescheduling.session_line(r)} for r in later],
            "remarque": "Pour « je vais rater celle de ce soir », passe son id à preparer_rattrapage (seances)."}


def preparer_rattrapage(user, matiere: str = "", seances: str = "", **_) -> dict:
    return reschedule_service.prepare(user, matiere, seances)


TOOLS = [
    Tool("seances_a_rattraper",
         "Séances de révision ratées (non faites, 14 derniers jours) et celles encore à venir aujourd'hui, avec "
         "leur identifiant.",
         seances_a_rattraper, params(matiere=text_param("Matière (facultatif)."))),
    Tool("preparer_rattrapage",
         "Quand l'étudiant a raté (ou va rater) une séance : calcule les options pour la rattraper (replacer la "
         "séance, réorganiser le planning, ajouter du temps) avec leurs conséquences réelles. Ne modifie rien : "
         "l'étudiant valide une option avec un bouton. Sans « seances » : les séances ratées (déjà commencées, non "
         "faites, 14 derniers jours) ; pour une séance à venir qu'il va rater, passe son id (voir "
         "seances_a_rattraper).",
         preparer_rattrapage,
         params(matiere=text_param("Matière de la séance ratée (facultatif)."),
                seances=text_param("Identifiants des séances à rattraper, séparés par des virgules (facultatif)."))),
    Tool("chercher_dans_mes_cours",
         "Cherche dans les supports de cours déposés par l'étudiant (PDF, diapositives, notes en photo) les passages "
         "qui répondent à une question de cours. À utiliser avant d'expliquer une notion, une formule ou un exercice.",
         chercher_dans_mes_cours,
         params(("question",), question=text_param("La question de cours, reformulée en une phrase complète."),
                matiere=text_param("Matière concernée, si elle est connue (facultatif)."))),
    Tool("programme_du_jour",
         "Séances de révision prévues aujourd'hui, cours du jour, examens à venir (avec chapitres pas encore "
         "étudiés) et prochaines séances. À utiliser pour « qu'est-ce que je fais aujourd'hui ? ».",
         programme_du_jour),
    Tool("chapitres",
         "Chapitres d'une matière de l'étudiant (lesquels sont déjà étudiés), date d'examen et aisance.",
         chapitres, params(("matiere",), matiere=SUBJECT)),
    Tool("notes",
         "Notes du dossier scolaire : moyenne générale, moyenne et dernières notes par matière, effet de la "
         "dernière note (positif = en progrès). Sans matière : toutes les matières.",
         notes, params(matiere=text_param("Matière (facultatif)."))),
    Tool("graphique_notes",
         "Affiche la courbe d'évolution des notes d'une matière (sur 20), ou de la moyenne générale si aucune "
         "matière n'est donnée.",
         graphique_notes, params(matiere=text_param("Matière (facultatif)."))),
]
