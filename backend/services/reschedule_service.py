"""« J'ai raté ma séance » : séances à rattraper, options simulées, puis application après validation.

Rien n'est modifié tant que l'étudiant n'a pas choisi une option (bouton sous la réponse de l'assistant).
Chaque option est calculée pour de vrai par le planificateur, dans une simulation annulée aussitôt
(dao.db.rolled_back) : l'étudiant voit les vraies nouvelles séances et le travail qui resterait sans créneau.
"""
from __future__ import annotations

from datetime import date, datetime

from backend.business import matching
from backend.business import rescheduling as rules
from backend.business.text import iso
from backend.dao import actions as actions_dao
from backend.dao import preferences as preferences_dao
from backend.dao import sessions as sessions_dao
from backend.dao import subjects as subjects_dao
from backend.dao.db import rolled_back

from . import planning_service, preference_service

KIND = "reschedule"


def _subject_filter(user_id, matiere: str):
    """(id de la matière, None) ; (None, None) sans matière ; (None, erreur) si inconnue."""
    if not matiere:
        return None, None
    rows = subjects_dao.list_for_user(user_id)
    row, _ = matching.best_match(matiere, rows)
    if row is None:
        return None, {"erreur": f"Aucune matière ne correspond à « {matiere} ».",
                      "matieres_de_l_etudiant": [r["name"] for r in rows]}
    return row["id"], None


def to_catch_up(user_id, matiere: str = "", now: datetime | None = None,
                later_today: bool = False) -> tuple[list, dict | None]:
    """Séances ratées (commencées depuis 14 jours, non faites), ou celles à venir aujourd'hui (later_today),
    filtrées par matière."""
    subject_id, error = _subject_filter(user_id, matiere)
    if error:
        return [], error
    since, now_iso, end_of_day = rules.window(now or datetime.now())
    rows = sessions_dao.missed(user_id, now_iso, end_of_day) if later_today else \
        sessions_dao.missed(user_id, since, now_iso)
    return [r for r in rows if subject_id is None or r["subject_id"] == subject_id], None


def _simulate(user_id, session_ids, subject_ids, repair: bool, not_before, daily_max=None) -> dict:
    with rolled_back():   # tout est annulé à la sortie
        sessions_dao.delete_ids(session_ids)
        result = planning_service.regenerate(user_id, repair=repair, not_before=not_before, daily_max=daily_max)
        if result is None:
            return {"sessions": [], "missing": 0}
        names = {s["id"]: s["name"] for s in subjects_dao.list_for_user(user_id)}
        new = sorted((s for s in result.sessions if s.subject_id in subject_ids), key=lambda s: s.start)
        return {
            "sessions": [rules.session_line({"start": iso(s.start), "end": iso(s.end), "topic": s.topic,
                                             "kind": s.kind, "subject_name": names.get(s.subject_id, "")})
                         for s in new],
            "missing": sum(t.remaining for t in result.unplaced if not t.optional and t.subject_id in subject_ids),
        }


def prepare(user, matiere: str = "", seances: str = "") -> dict:
    """Calcule les options de rattrapage et les garde en attente de validation ; ne modifie rien."""
    uid, now = user["id"], datetime.now()
    ids = [int(x) for x in str(seances).replace(";", ",").split(",") if x.strip().isdigit()]
    if ids:   # séances désignées : ratées, ou à venir aujourd'hui (« je vais rater celle de ce soir »)
        rows = [r for r in sessions_dao.owned_not_done(uid, ids) if r["start"] < rules.window(now)[2]]
    else:
        rows, error = to_catch_up(uid, matiere, now)
        if error:
            return error
    if not rows:
        return {"resultat": "Aucune séance ratée ou prévue aujourd'hui à rattraper"
                            + (f" en {matiere}" if matiere else "") + " : le planning est à jour."}
    session_ids = [r["id"] for r in rows]
    subject_ids = {r["subject_id"] for r in rows}
    subjects = [dict(s) for s in subjects_dao.list_for_user(uid) if s["id"] in subject_ids]
    not_before = rules.resume_from([r["start"] for r in rows], now)

    sims = {"replacer": _simulate(uid, session_ids, subject_ids, True, not_before)}
    if sims["replacer"]["missing"]:
        sims["reorganiser"] = _simulate(uid, session_ids, subject_ids, False, not_before)
        prefs, _ = preference_service.load(uid)
        more = min(rules.MAX_DAILY_MINUTES, prefs.daily_max_minutes + rules.EXTRA_MINUTES)
        if more > prefs.daily_max_minutes:
            sims["plus_de_temps"] = _simulate(uid, session_ids, subject_ids, False, not_before, more)
            sims["plus_de_temps"]["daily_max"] = more
    keys = rules.choose_options(sims)
    options = [rules.option_summary(k, sims[k]) for k in keys]
    action_id = actions_dao.create(uid, KIND, {
        "session_ids": session_ids,
        "not_before": not_before.isoformat() if not_before else None,
        "options": {k: {"label": rules.LABELS[k], "repair": k == "replacer",
                        "daily_max": sims[k].get("daily_max")} for k in keys},
    })
    blockers = rules.exam_blockers(subjects, date.today())
    title = "Rattraper : " + ", ".join(sorted({r["subject_name"] for r in rows}))
    return {
        "seances_a_rattraper": [rules.session_line(r) for r in rows],
        "options": [{"choix": o["key"], "titre": o["label"], "nouvelles_seances": o["details"],
                     "alerte": o["warning"]} for o in options],
        "examens_trop_proches": blockers,
        "consigne": "Rien n'est encore modifié. Présente les options (conséquences, alertes), recommande-en une ; "
                    "l'étudiant valide lui-même avec les boutons affichés sous ta réponse.",
        "action": {"id": action_id, "kind": KIND, "title": title, "options": options, "notes": blockers},
    }


def apply(user, action_id: int, choice: str) -> tuple[str | None, str | None]:
    """Applique l'option choisie ; renvoie (message de confirmation, erreur)."""
    action = actions_dao.get_owned(action_id, user["id"])
    if action is None or action["kind"] != KIND:
        return None, "Proposition introuvable."
    if action["status"] != "pending":
        return None, "Cette proposition a déjà été traitée."
    option = action["payload"]["options"].get(choice)
    if option is None:
        return None, "Option inconnue."
    uid = user["id"]
    rows = sessions_dao.owned_not_done(uid, action["payload"]["session_ids"])
    sessions_dao.delete_ids([r["id"] for r in rows])
    if option.get("daily_max"):
        preferences_dao.set_daily_max(uid, option["daily_max"])
    not_before = action["payload"].get("not_before")
    report = planning_service.update(uid, f"{len(rows)} séance(s) rattrapée(s).", repair=option["repair"],
                                     not_before=datetime.fromisoformat(not_before) if not_before else None)
    actions_dao.close(action_id, uid, "done", choice)
    details = " ".join(text for text, _ in report)
    extra = f" Ton temps maximum par jour passe à {option['daily_max'] // 60} h {option['daily_max'] % 60:02d}." \
        if option.get("daily_max") else ""
    return f"C'est fait : {option['label'].lower()}. {details}{extra}".strip(), None
