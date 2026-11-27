"""« J'ai raté ma séance » : règles pour rattraper des séances de révision (pures, testables seules).

Les options sont d'abord simulées (le planning est recalculé puis annulé) ; l'étudiant en choisit une, qui
est alors appliquée pour de bon :
- replacer : seules les séances ratées sont replacées dans tes créneaux libres, le reste ne bouge pas ;
- réorganiser : tout le planning est recalculé ; les matières dont l'examen est le plus proche passent en
  premier, celles dont l'examen est loin sont repoussées ;
- plus de temps : comme réorganiser, avec une heure de plus de travail possible par jour.
Les deux dernières ne sont proposées que si replacer laisse du travail sans créneau avant l'examen et
qu'elles font mieux.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .exams import days_left
from .planner import KIND_LABELS, fmt_minutes
from .text import day_fr

LOOKBACK_DAYS = 14      # séances ratées prises en compte
EXTRA_MINUTES = 60      # option « plus de temps » : une heure de plus par jour
MAX_DAILY_MINUTES = 720

LABELS = {
    "replacer": "Replacer seulement ces séances",
    "reorganiser": "Réorganiser tout le planning (examens proches d'abord)",
    "plus_de_temps": "Réorganiser avec 1 h de travail en plus par jour",
}


def window(now: datetime) -> tuple[str, str, str]:
    """(début, maintenant, fin d'aujourd'hui). Ratées : non faites, commencées depuis 14 jours ;
    « je vais rater » : non faites, à venir aujourd'hui (seulement si on les désigne)."""
    since = (now - timedelta(days=LOOKBACK_DAYS)).isoformat(timespec="minutes")
    end_of_day = datetime.combine(now.date() + timedelta(days=1), time()).isoformat(timespec="minutes")
    return since, now.isoformat(timespec="minutes"), end_of_day


def resume_from(starts: list[str], now: datetime) -> datetime | None:
    """Une séance ratée aujourd'hui : on ne replanifie pas le reste de la journée (dès demain)."""
    if any(s[:10] == now.date().isoformat() for s in starts):
        return datetime.combine(now.date() + timedelta(days=1), time())
    return None


def session_line(s) -> str:
    """« samedi 28 novembre, 14:00-14:50 : Probabilités, Lois discrètes (Étude) »."""
    start, end = str(s["start"]), str(s["end"])
    return (f"{day_fr(start)}, {start[11:16]}-{end[11:16]} : {s['subject_name']}, {s['topic']} "
            f"({KIND_LABELS.get(s['kind'], s['kind'])})")


def exam_blockers(subjects: list[dict], today: date) -> list[str]:
    """Matières dont l'examen est aujourd'hui ou demain : plus rien à rattraper avant."""
    out = []
    for s in subjects:
        left = days_left(s.get("exam_date")) if s.get("goal") == "exam" else None
        if left is not None and left <= 1:
            when = "aujourd'hui" if left <= 0 else "demain"
            out.append(f"L'examen de « {s['name']} » est {when} : la séance ratée ne peut plus être rattrapée "
                       "avant. Concentre-toi sur une révision courte de l'essentiel.")
    return out


def choose_options(simulations: dict[str, dict]) -> list[str]:
    """Options à proposer, d'après les simulations {clé: {"missing": minutes sans créneau, …}}."""
    keys = ["replacer"]
    best = simulations["replacer"]["missing"]
    for key in ("reorganiser", "plus_de_temps"):
        sim = simulations.get(key)
        if best > 0 and sim is not None and sim["missing"] < best:
            keys.append(key)
            best = sim["missing"]
    return keys


def option_summary(key: str, sim: dict) -> dict:
    """Ce que l'interface et l'assistant montrent d'une option simulée."""
    lines = sim["sessions"][:4]
    more = len(sim["sessions"]) - len(lines)
    if more > 0:
        lines.append(f"… et {more} autre(s) séance(s)")
    warning = (f"{fmt_minutes(sim['missing'])} de travail resteraient sans créneau avant l'examen."
               if sim["missing"] else "")
    return {"key": key, "label": LABELS[key], "details": lines or ["Aucune nouvelle séance nécessaire."],
            "warning": warning}
