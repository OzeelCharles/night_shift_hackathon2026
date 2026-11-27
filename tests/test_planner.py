import itertools
import unittest
from datetime import date, datetime, time, timedelta

from app.business import calendar_ics, extractor, planner

NOW = datetime(2026, 11, 30, 8, 0)  # un lundi


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.prefs = planner.Prefs(method="pomodoro", session_minutes=50, break_minutes=10,
                                   daily_max_minutes=180, day_start=time(9), day_end=time(19),
                                   rest_days={6}, buffer_minutes=15)

    def exam_subject(self, days=21, comfort=3, topics=None, sid=1):
        return planner.SubjectIn(id=sid, name=f"Matière {sid}", goal="exam", comfort=comfort,
                                 exam_date=NOW.date() + timedelta(days=days),
                                 topics=topics or [f"Chapitre {i}" for i in range(1, 7)])

    def test_respects_constraints(self):
        busy = [planner.Busy(datetime(2026, 12, 1, 10), datetime(2026, 12, 1, 12)),
                planner.Busy(datetime(2026, 12, 2, 9), datetime(2026, 12, 2, 17))]
        subj = self.exam_subject()
        res = planner.generate_plan([subj], self.prefs, busy, NOW)
        self.assertTrue(res.sessions)
        per_day = {}
        for s in res.sessions:
            self.assertGreaterEqual(s.start.time(), time(9))
            self.assertLessEqual(s.end.time(), time(19))
            self.assertNotEqual(s.start.weekday(), 6, "pas de séance le jour de repos")
            self.assertLess(s.start.date(), subj.exam_date, "rien le jour de l'examen ou après")
            self.assertLessEqual((s.end - s.start).total_seconds() / 60, 50)
            for b in busy:
                self.assertFalse(overlaps(s.start, s.end, b.start - timedelta(minutes=15),
                                          b.end + timedelta(minutes=15)), s)
            per_day[s.start.date()] = per_day.get(s.start.date(), 0) + (s.end - s.start).total_seconds() / 60
        self.assertTrue(all(m <= 180 for m in per_day.values()))
        ordered = sorted(res.sessions, key=lambda s: s.start)
        for a, b in zip(ordered, ordered[1:]):
            self.assertLessEqual(a.end, b.start)

    def test_reviews_after_study(self):
        res = planner.generate_plan([self.exam_subject()], self.prefs, [], NOW)
        first_study = {}
        for s in sorted(res.sessions, key=lambda s: s.start):
            if s.kind == "study":
                first_study.setdefault(s.topic, s.start.date())
            if s.kind == "review":
                self.assertIn(s.topic, first_study)
                self.assertGreater(s.start.date(), first_study[s.topic])
        self.assertTrue(any(s.kind == "practice" for s in res.sessions))
        self.assertFalse(res.warnings)

    def test_low_comfort_gets_more_time(self):
        easy = planner.generate_plan([self.exam_subject(comfort=5)], self.prefs, [], NOW)
        hard = planner.generate_plan([self.exam_subject(comfort=1)], self.prefs, [], NOW)
        total = lambda r: sum((s.end - s.start).total_seconds() for s in r.sessions)
        self.assertGreater(total(hard), total(easy))

    def test_warns_when_not_enough_time(self):
        prefs = planner.Prefs(daily_max_minutes=30, session_minutes=30)
        subj = self.exam_subject(days=3, comfort=1, topics=[f"C{i}" for i in range(15)])
        res = planner.generate_plan([subj], prefs, [], NOW)
        self.assertTrue(res.warnings)

    def test_a_jour_weekly_budget(self):
        subj = planner.SubjectIn(id=1, name="Histoire", goal="a_jour", weekly_hours=3,
                                 topics=["A", "B", "C", "D"])
        res = planner.generate_plan([subj], self.prefs, [], NOW, horizon_days=28)
        weeks = {}
        for s in res.sessions:
            w = (s.start.date() - NOW.date()).days // 7
            weeks[w] = weeks.get(w, 0) + (s.end - s.start).total_seconds() / 60
        self.assertEqual(set(weeks), {0, 1, 2, 3})
        for m in weeks.values():
            self.assertAlmostEqual(m, 180, delta=1)

    def test_interleaves_subjects(self):
        subs = [self.exam_subject(sid=1), self.exam_subject(sid=2)]
        res = planner.generate_plan(subs, self.prefs, [], NOW)
        first_day = [s for s in res.sessions if s.start.date() == NOW.date()]
        self.assertEqual({s.subject_id for s in first_day}, {1, 2})

    def test_done_topics_not_restudied(self):
        subj = self.exam_subject(topics=["A", "B"])
        res = planner.generate_plan([subj], self.prefs, [], NOW,
                                    done_study={1: {"A": NOW.date() - timedelta(days=1)}})
        self.assertFalse([s for s in res.sessions if s.topic == "A" and s.kind == "study"])
        self.assertTrue([s for s in res.sessions if s.topic == "A" and s.kind == "review"])

    def test_past_exam_ignored(self):
        subj = self.exam_subject(days=-1)
        res = planner.generate_plan([subj], self.prefs, [], NOW)
        self.assertEqual(res.sessions, [])
        self.assertTrue(res.warnings)


class RepairTests(unittest.TestCase):
    """Mode réparation : les séances déjà planifiées restent à leur place."""

    def setUp(self):
        self.prefs = planner.Prefs(session_minutes=50, daily_max_minutes=180, day_start=time(9), day_end=time(19))
        self.subj = planner.SubjectIn(id=1, name="Maths", goal="exam", comfort=3,
                                      exam_date=NOW.date() + timedelta(days=21),
                                      topics=[f"C{i}" for i in range(1, 6)])

    def test_keeping_everything_changes_nothing(self):
        first = planner.generate_plan([self.subj], self.prefs, [], NOW)
        fixed = [planner.PlannedSession(s.subject_id, s.start, s.end, s.topic, s.kind, s.activity, ref=i)
                 for i, s in enumerate(first.sessions)]
        again = planner.generate_plan([self.subj], self.prefs, [], NOW, fixed=fixed)
        self.assertEqual(again.sessions, [], "tout est déjà planifié : rien à ajouter")
        self.assertEqual(again.dropped, [])

    def test_only_missing_work_is_replanned(self):
        first = planner.generate_plan([self.subj], self.prefs, [], NOW)
        removed = first.sessions[3]
        fixed = [planner.PlannedSession(s.subject_id, s.start, s.end, s.topic, s.kind, s.activity, ref=i)
                 for i, s in enumerate(first.sessions) if s is not removed]
        again = planner.generate_plan([self.subj], self.prefs, [], NOW, fixed=fixed)
        added = sum((s.end - s.start).total_seconds() for s in again.sessions)
        self.assertEqual(added, (removed.end - removed.start).total_seconds(), "seul le travail retiré est replacé")
        self.assertEqual({(s.topic, s.kind) for s in again.sessions}, {(removed.topic, removed.kind)})
        for s in again.sessions:
            for f in fixed:
                self.assertFalse(s.start < f.end and f.start < s.end, "pas de chevauchement avec les séances gardées")

    def test_orphan_sessions_are_dropped(self):
        orphan = planner.PlannedSession(1, datetime(2026, 12, 1, 9), datetime(2026, 12, 1, 9, 50),
                                        "Chapitre supprimé", "study", "x", ref=99)
        result = planner.generate_plan([self.subj], self.prefs, [], NOW, fixed=[orphan])
        self.assertEqual([d.ref for d in result.dropped], [99])


class IcsTests(unittest.TestCase):
    def test_uid_keys_and_modified_occurrence(self):
        ics = "\r\n".join([
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT", "UID:cours-1", "SUMMARY:Stats", "DTSTART:20261130T100000", "DTEND:20261130T120000",
            "RRULE:FREQ=WEEKLY;COUNT=3", "END:VEVENT",
            # l'occurrence du 7 décembre est déplacée à 14 h
            "BEGIN:VEVENT", "UID:cours-1", "RECURRENCE-ID:20261207T100000", "SUMMARY:Stats",
            "DTSTART:20261207T140000", "DTEND:20261207T160000", "END:VEVENT",
            "END:VCALENDAR"])
        events, _ = calendar_ics.parse_ics(ics, datetime(2026, 11, 29))
        self.assertEqual([e["start"] for e in events],
                         [datetime(2026, 11, 30, 10), datetime(2026, 12, 7, 14), datetime(2026, 12, 14, 10)],
                         "l'occurrence déplacée remplace l'originale, sans doublon")
        self.assertEqual(events[1]["key"], "cours-1|2026-12-07T10:00", "clé stable : UID + date d'origine")
        self.assertEqual(len({e["key"] for e in events}), 3)

    ICS = "\r\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "SUMMARY:Cours de stats", "DTSTART:20261130T100000",
        "DTEND:20261130T120000", "RRULE:FREQ=WEEKLY;COUNT=3", "EXDATE:20261207T100000", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:Vacances", "DTSTART;VALUE=DATE:20261220", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:TD\\, salle 12", "DTSTART;TZID=Europe/Paris:20261201T140000",
        "DURATION:PT1H30M", "END:VEVENT", "END:VCALENDAR"])

    def test_parse(self):
        events, skipped = calendar_ics.parse_ics(self.ICS, datetime(2026, 11, 29))
        self.assertEqual(skipped, 1)
        stats = [e for e in events if e["title"] == "Cours de stats"]
        self.assertEqual([e["start"].date() for e in stats], [date(2026, 11, 30), date(2026, 12, 14)])
        td = [e for e in events if e["title"] == "TD, salle 12"][0]
        self.assertEqual(td["end"] - td["start"], timedelta(minutes=90))

    def test_roundtrip(self):
        ics = calendar_ics.build_ics([{"uid": "1", "title": "Maths : A, B", "start": datetime(2026, 12, 1, 9),
                                       "end": datetime(2026, 12, 1, 10)}])
        events, _ = calendar_ics.parse_ics(ics, datetime(2026, 11, 29))
        self.assertEqual(events[0]["title"], "Maths : A, B")


class ExtractorTests(unittest.TestCase):
    def test_headings(self):
        text = "CHAPITRE 1 Les suites\nblabla\n2. Limites de fonctions\nIII - Dérivation\nune phrase normale ici."
        topics = extractor.clean_topics(extractor.guess_headings(text))
        self.assertEqual(topics, ["CHAPITRE 1 Les suites", "2. Limites de fonctions", "III - Dérivation"])

    def test_clean(self):
        self.assertEqual(extractor.clean_topics(["Intro ........ 3", "intro", "Sommaire", "12"]), ["Intro"])


if __name__ == "__main__":
    unittest.main()
