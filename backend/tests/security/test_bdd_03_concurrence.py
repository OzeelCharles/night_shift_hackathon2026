"""BDD-03 — Concurrence : WAL, busy_timeout, et charge bornée (20 fils, 10 s au plus, local).

Pendant la charge, 20 clients du même compte cochent des séances, régénèrent le planning et importent un .ics.
Attendu : aucune erreur 500, aucun « database is locked », aucune séance en double ou orpheline.
"""
import io
import logging
import random
import threading
import time
import unittest
from datetime import date, timedelta

from ._outils import AppSecuTestCase

FILS = 20
DUREE = 10.0  # secondes, plafond imposé par les règles du duel


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        try:
            texte = record.getMessage()
        except Exception:
            texte = str(record.msg)
        if record.exc_info and record.exc_info[1] is not None:
            texte += " " + repr(record.exc_info[1])
        self.messages.append(texte)


def ics(semaine: int) -> bytes:
    j = date.today() + timedelta(days=2 + semaine % 5)
    d = j.strftime("%Y%m%d")
    return (f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:c{semaine % 3}\r\nSUMMARY:Cours d'analyse\r\n"
            f"DTSTART:{d}T{8 + semaine % 4:02d}0000\r\nDTEND:{d}T{9 + semaine % 4:02d}0000\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n").encode()


class ConcurrenceTests(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        for nom in ("Analyse", "Probabilités", "Algèbre"):
            self.client.post("/matieres/nouvelle", data={"name": nom, "goal": "a_jour", "weekly_hours": "4",
                                                         "comfort": "3", "topics": "A\nB\nC"})
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics(0)), "e.ics")},
                         content_type="multipart/form-data")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]

    def test_pragmas_wal_et_busy_timeout(self):
        from backend.dao.db import get_db
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertGreaterEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_charge_parallele_bornee(self):
        cookie = self.client.get_cookie(self.app.config.get("SESSION_COOKIE_NAME", "session")).value
        seances = [r["id"] for r in self.q("SELECT id FROM sessions WHERE user_id = ?", self.uid)]
        self.assertTrue(seances, "préparation : aucune séance planifiée")
        capture = _Capture()
        journaux = [logging.getLogger(), self.app.logger, logging.getLogger("werkzeug")]
        for j in journaux:
            j.addHandler(capture)
        self.addCleanup(lambda: [j.removeHandler(capture) for j in journaux])

        fin = time.monotonic() + DUREE
        statuts, erreurs, verrou = [], [], threading.Lock()
        depart = threading.Barrier(FILS)

        def travail(n):
            rng = random.Random(n)
            c = self.app.test_client()
            c.set_cookie(self.app.config.get("SESSION_COOKIE_NAME", "session"), cookie)
            depart.wait()
            i = 0
            while time.monotonic() < fin:
                i += 1
                try:
                    action = rng.choice(("cocher", "cocher", "planning", "ics"))
                    if action == "cocher":
                        r = c.post(f"/seances/{rng.choice(seances)}/fait")
                    elif action == "planning":
                        r = c.post("/planning/generer")
                    else:
                        r = c.post("/agenda/import", data={"ics": (io.BytesIO(ics(n + i)), "e.ics")},
                                   content_type="multipart/form-data")
                    texte = r.get_data(as_text=True) if r.status_code >= 500 else ""
                    with verrou:
                        statuts.append(r.status_code)
                        if "locked" in texte.lower():
                            erreurs.append(texte[:200])
                except Exception as e:  # une exception dans le client compte comme une erreur
                    with verrou:
                        erreurs.append(repr(e))

        fils = [threading.Thread(target=travail, args=(n,)) for n in range(FILS)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(DUREE + 60)
        verrouillee = [m for m in capture.messages if "locked" in m.lower()]
        self.assertTrue(statuts, "aucune requête n'a abouti")
        exceptions = [m[-300:] for m in capture.messages if "Exception on" in m or "Error" in m]
        self.assertEqual(statuts.count(500), 0, f"{statuts.count(500)} erreurs 500 sur {len(statuts)} requêtes : "
                                                f"{exceptions[:2]}")
        self.assertEqual(erreurs, [])
        self.assertEqual(verrouillee[:3], [], f"{len(verrouillee)} « database is locked » dans les journaux")
        doubles = self.q("SELECT start, COUNT(*) AS n FROM sessions WHERE user_id = ? GROUP BY start HAVING n > 1",
                         self.uid)
        self.assertEqual(doubles, [], "séances en double (même horaire)")
        orphelines = self.q("SELECT COUNT(*) AS n FROM sessions WHERE subject_id NOT IN (SELECT id FROM subjects)")
        self.assertEqual(orphelines[0]["n"], 0, "séances orphelines")
        chevauchements = self.q(
            "SELECT COUNT(*) AS n FROM sessions a JOIN sessions b ON a.user_id = b.user_id AND a.id < b.id "
            "AND a.start < b.end AND b.start < a.end WHERE a.user_id = ?", self.uid)
        self.assertEqual(chevauchements[0]["n"], 0, "séances qui se chevauchent")


if __name__ == "__main__":
    unittest.main()
