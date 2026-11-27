"""Moteur de planification des révisions.

Module pur Python (aucune dépendance à Flask) pour pouvoir être testé seul.

Principe :
1. Chaque matière est transformée en une liste de tâches :
   - objectif « examen » : étude de chaque chapitre, révisions espacées
     (J+1, J+3, J+7, J+14) puis annales / examen blanc en fin de période ;
   - objectif « être à jour » : un budget hebdomadaire réparti entre
     avancement dans le cours et révision des chapitres déjà vus.
   La durée d'étude dépend de l'aisance déclarée (1 = difficile, 5 = très à l'aise).
2. Les créneaux libres sont calculés jour par jour à partir des préférences
   (plage horaire, jours de repos, durée max par jour) en retirant les
   événements de l'agenda (plus une marge).
3. Les tâches sont placées jour après jour en suivant l'échéance la plus proche
   (EDF), en respectant les dépendances (une révision suit l'étude du chapitre)
   et en alternant les matières quand c'est possible.
"""
from __future__ import annotations

import itertools
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

MIN_SLOT = 20  # durée minimale d'une séance (minutes)
BASE_TOPIC_MINUTES = 90  # temps d'étude d'un chapitre pour une aisance moyenne
COMFORT_FACTOR = {1: 1.6, 2: 1.3, 3: 1.0, 4: 0.75, 5: 0.5}
REVIEW_OFFSETS = (1, 3, 7, 14)  # répétition espacée (jours après l'étude)
MANDATORY_REVIEWS = 2  # les 2 premières révisions sont prioritaires, les suivantes optionnelles
DEFAULT_TOPICS = [
    "Notions de base et vocabulaire",
    "Concepts clés du cours",
    "Méthodes et techniques",
    "Applications et exercices types",
    "Synthèse et cas complexes",
]

METHODS = {
    "pomodoro": {
        "label": "Pomodoro",
        "description": "Blocs de 25 min de travail séparés de 5 min de pause.",
        "study": "Étude du chapitre en pomodoros",
        "review": "Révision en pomodoros : relire puis se tester",
        "practice": "Exercices chronométrés en pomodoros",
    },
    "blocs_longs": {
        "label": "Sessions longues",
        "description": "Peu de séances mais longues, pour approfondir.",
        "study": "Étude approfondie du chapitre",
        "review": "Reprise du chapitre et des points flous",
        "practice": "Sujet complet en temps limité",
    },
    "active_recall": {
        "label": "Rappel actif / flashcards",
        "description": "Se tester plutôt que relire, avec répétition espacée.",
        "study": "Lecture active et création de flashcards",
        "review": "Auto-test sur flashcards (répétition espacée)",
        "practice": "Annale en conditions d'examen puis correction",
    },
    "fiches": {
        "label": "Fiches de synthèse",
        "description": "Résumer chaque chapitre sur une fiche puis la retravailler.",
        "study": "Lecture du cours et rédaction de la fiche",
        "review": "Relecture active de la fiche et questions",
        "practice": "Exercices à partir des fiches",
    },
    "mixte": {
        "label": "Mixte",
        "description": "Un peu de tout : comprendre, se tester, s'entraîner.",
        "study": "Comprendre le cours (lecture et exemples)",
        "review": "Se tester : questions, flashcards, schémas",
        "practice": "Exercices et annales corrigées",
    },
}

KIND_LABELS = {"study": "Étude", "review": "Révision", "practice": "Entraînement"}


@dataclass
class SubjectIn:
    id: int
    name: str
    goal: str  # "exam" ou "a_jour"
    comfort: int = 3
    exam_date: date | None = None
    weekly_hours: float = 3.0
    topics: list[str] = field(default_factory=list)


@dataclass
class Prefs:
    method: str = "pomodoro"
    session_minutes: int = 50
    break_minutes: int = 10
    daily_max_minutes: int = 180
    day_start: time = time(8, 0)
    day_end: time = time(21, 0)
    rest_days: set[int] = field(default_factory=lambda: {6})  # 0 = lundi, 6 = dimanche
    buffer_minutes: int = 15


@dataclass
class Busy:
    start: datetime
    end: datetime


@dataclass
class Task:
    id: int
    subject_id: int
    subject_name: str
    topic: str
    kind: str  # study / review / practice
    minutes: int
    earliest: date
    deadline: date
    target: date
    depends_on: int | None = None
    offset_days: int = 0
    optional: bool = False
    remaining: int = 0

    def __post_init__(self):
        self.remaining = self.minutes


@dataclass
class PlannedSession:
    subject_id: int
    start: datetime
    end: datetime
    topic: str
    kind: str
    activity: str


@dataclass
class PlanResult:
    sessions: list[PlannedSession]
    unplaced: list[Task]
    needed_minutes: int
    available_minutes: int
    warnings: list[str]


def _round5(x: float) -> int:
    return max(5, int(round(x / 5.0)) * 5)


def study_minutes(comfort: int) -> int:
    return _round5(BASE_TOPIC_MINUTES * COMFORT_FACTOR.get(comfort, 1.0))


def build_tasks(subject: SubjectIn, today: date, horizon_days: int, ids,
                done_study: dict[str, date] | None = None) -> tuple[list[Task], list[str]]:
    """Transforme une matière en tâches. `done_study` : chapitres déjà étudiés -> date."""
    done_study = done_study or {}
    topics = subject.topics or DEFAULT_TOPICS
    tasks: list[Task] = []
    warnings: list[str] = []
    s_min = study_minutes(subject.comfort)
    r_min = max(MIN_SLOT, _round5(s_min * 0.35))

    def add(**kw) -> Task:
        t = Task(id=next(ids), subject_id=subject.id, subject_name=subject.name, **kw)
        tasks.append(t)
        return t

    if subject.goal == "exam":
        if not subject.exam_date or subject.exam_date <= today:
            warnings.append(f"« {subject.name} » : date d'examen absente ou passée, matière ignorée.")
            return [], warnings
        deadline = subject.exam_date - timedelta(days=1)
        days = (deadline - today).days + 1
        study_window = max(1, math.ceil(days * 0.6))
        practice_days = max(1, math.ceil(days * 0.25))
        practice_start = max(today, deadline - timedelta(days=practice_days - 1))
        n = len(topics)
        for i, topic in enumerate(topics):
            target = today + timedelta(days=(i * study_window) // n)
            study = None
            anchor = done_study.get(topic)
            if anchor is None:
                study = add(topic=topic, kind="study", minutes=s_min, earliest=today,
                            deadline=deadline, target=target)
            for k, off in enumerate(REVIEW_OFFSETS):
                base = anchor or target
                if base + timedelta(days=off) > deadline:
                    break
                add(topic=topic, kind="review", minutes=r_min,
                    earliest=max(today, anchor + timedelta(days=off)) if anchor else today,
                    deadline=deadline, target=base + timedelta(days=off),
                    depends_on=study.id if study else None, offset_days=off,
                    optional=k >= MANDATORY_REVIEWS)
        n_practice = min(4, max(1, n // 3)) + (1 if subject.comfort <= 2 else 0)
        p_min = max(60, min(120, s_min))
        for k in range(n_practice):
            last = k == n_practice - 1
            add(topic="Bilan final : examen blanc" if last else "Annales et exercices de synthèse",
                kind="practice", minutes=p_min, earliest=practice_start, deadline=deadline,
                target=practice_start + timedelta(days=(k * practice_days) // n_practice))
    else:  # être à jour : budget hebdomadaire
        weeks = max(1, math.ceil(horizon_days / 7))
        weekly = int(round(subject.weekly_hours * 60))
        if weekly < MIN_SLOT:
            warnings.append(f"« {subject.name} » : budget hebdomadaire trop faible.")
            return [], warnings
        pending = [t for t in topics if t not in done_study]
        covered = [t for t in topics if t in done_study]
        need = {t: s_min for t in pending}
        review_ptr = 0
        study_share = 0.6 if subject.comfort <= 3 else 0.5
        for w in range(weeks):
            ws = today + timedelta(days=7 * w)
            we = min(ws + timedelta(days=6), today + timedelta(days=horizon_days - 1))
            budget_study = int(weekly * study_share) if pending else 0
            budget_review = weekly - budget_study
            while budget_study >= MIN_SLOT and pending:
                topic = pending[0]
                m = min(need[topic], budget_study)
                add(topic=topic, kind="study", minutes=m, earliest=ws, deadline=we, target=ws)
                budget_study -= m
                need[topic] -= m
                if need[topic] <= 0:
                    covered.append(pending.pop(0))
            budget_review += budget_study
            if not covered:
                continue
            # révisions : chapitres vus, du plus récent au plus ancien, en boucle
            order = list(reversed(covered))
            while budget_review >= MIN_SLOT:
                topic = order[review_ptr % len(order)]
                review_ptr += 1
                m = min(r_min, budget_review)
                add(topic=topic, kind="review", minutes=m, earliest=ws, deadline=we,
                    target=min(we, ws + timedelta(days=3)))
                budget_review -= m
    return tasks, warnings


def _ceil_quarter(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (-dt.minute) % 15
    return dt + timedelta(minutes=extra)


def free_slots(day: date, prefs: Prefs, busy: list[Busy], now: datetime) -> list[tuple[datetime, datetime]]:
    start = datetime.combine(day, prefs.day_start)
    end = datetime.combine(day, prefs.day_end)
    if day == now.date():
        start = max(start, _ceil_quarter(now))
    if start >= end:
        return []
    buf = timedelta(minutes=prefs.buffer_minutes)
    intervals = [(start, end)]
    for b in busy:
        bs, be = b.start - buf, b.end + buf
        if be <= start or bs >= end:
            continue
        nxt = []
        for s, e in intervals:
            if be <= s or bs >= e:
                nxt.append((s, e))
                continue
            if bs > s:
                nxt.append((s, bs))
            if be < e:
                nxt.append((be, e))
        intervals = nxt
    return [(s, e) for s, e in intervals if e - s >= timedelta(minutes=MIN_SLOT)]


def activity_label(method: str, kind: str, minutes: int) -> str:
    label = METHODS.get(method, METHODS["mixte"])[kind]
    if method == "pomodoro":
        n = minutes // 25 if minutes >= 50 else 1
        label += f" ({n} × 25 min)" if minutes >= 25 else " (séance courte)"
    return label


def _ready(t: Task, day: date, done_date: dict[int, date]) -> bool:
    if day < t.earliest or day > t.deadline:
        return False
    if t.depends_on is None:
        return True
    d = done_date.get(t.depends_on)
    return d is not None and day >= d + timedelta(days=t.offset_days)


def _pick(ready: list[Task], day: date, last_subject: int | None) -> Task:
    kind_rank = {"review": 0, "study": 1, "practice": 2}
    ready.sort(key=lambda t: (t.target > day, t.deadline, t.target, kind_rank[t.kind], t.id))
    best = ready[0]
    if best.subject_id == last_subject:
        # alternance : on change de matière si une autre tâche est presque aussi urgente
        for c in ready[1:]:
            if c.subject_id != last_subject and c.target <= day and c.deadline <= best.deadline + timedelta(days=3):
                return c
    return best


def schedule(tasks: list[Task], prefs: Prefs, busy: list[Busy], now: datetime) -> PlanResult:
    pending = {t.id: t for t in tasks}
    known_ids = set(pending)
    done_date: dict[int, date] = {}
    sessions: list[PlannedSession] = []
    unplaced: list[Task] = []
    available = 0
    if not tasks:
        return PlanResult([], [], 0, 0, [])
    day = now.date()
    last_day = max(t.deadline for t in tasks)
    busy = sorted(busy, key=lambda b: b.start)
    while day <= last_day and pending:
        for t in list(pending.values()):
            if t.deadline < day or (t.depends_on in known_ids and t.depends_on not in pending
                                    and t.depends_on not in done_date):
                unplaced.append(pending.pop(t.id))
        if day.weekday() in prefs.rest_days:
            day += timedelta(days=1)
            continue
        day_busy = [b for b in busy if b.start.date() <= day <= b.end.date()]
        slots = free_slots(day, prefs, day_busy, now)
        available += min(prefs.daily_max_minutes,
                         sum(int((e - s).total_seconds() // 60) for s, e in slots))
        used = 0
        last_subject = None
        for s_start, s_end in slots:
            cursor = s_start
            while True:
                room = int((s_end - cursor).total_seconds() // 60)
                limit = min(room, prefs.daily_max_minutes - used, prefs.session_minutes)
                if limit < MIN_SLOT:
                    break
                ready = [t for t in pending.values() if _ready(t, day, done_date)]
                if not ready:
                    break
                t = _pick(ready, day, last_subject)
                dur = min(t.remaining, limit)
                leftover = t.remaining - dur
                if 0 < leftover < MIN_SLOT and dur - (MIN_SLOT - leftover) >= MIN_SLOT:
                    dur -= MIN_SLOT - leftover  # évite de laisser un bout de séance trop court
                end = cursor + timedelta(minutes=dur)
                sessions.append(PlannedSession(t.subject_id, cursor, end, t.topic, t.kind,
                                               activity_label(prefs.method, t.kind, dur)))
                t.remaining -= dur
                if t.remaining <= 0:
                    done_date[t.id] = day
                    del pending[t.id]
                used += dur
                last_subject = t.subject_id
                cursor = end + timedelta(minutes=prefs.break_minutes)
        day += timedelta(days=1)
    unplaced.extend(pending.values())
    needed = sum(t.minutes for t in tasks)
    return PlanResult(sessions, unplaced, needed, available, [])


def fmt_minutes(m: int) -> str:
    h, mn = divmod(int(m), 60)
    if h and mn:
        return f"{h} h {mn:02d}"
    return f"{h} h" if h else f"{mn} min"


def generate_plan(subjects: list[SubjectIn], prefs: Prefs, busy: list[Busy], now: datetime,
                  horizon_days: int = 28,
                  done_study: dict[int, dict[str, date]] | None = None) -> PlanResult:
    done_study = done_study or {}
    ids = itertools.count(1)
    tasks: list[Task] = []
    warnings: list[str] = []
    for s in subjects:
        t, w = build_tasks(s, now.date(), horizon_days, ids, done_study.get(s.id))
        tasks += t
        warnings += w
    result = schedule(tasks, prefs, busy, now)
    missing = defaultdict(int)
    names = {s.id: s.name for s in subjects}
    for t in result.unplaced:
        if not t.optional:
            missing[t.subject_id] += t.remaining
    for sid, m in missing.items():
        if m >= MIN_SLOT:
            warnings.append(
                f"« {names[sid]} » : {fmt_minutes(m)} de travail n'ont pas pu être placées avant l'échéance. "
                "Augmente le temps max par jour, élargis tes horaires ou réduis le nombre de chapitres.")
    if result.needed_minutes > result.available_minutes and not missing:
        warnings.append("Planning serré : certaines révisions optionnelles ont été abandonnées.")
    result.warnings = warnings
    return result
