import io
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

from app import create_app
from app.db import get_db


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({
            "TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
            "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
            "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up"),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, email="eleve@test.fr", password="motdepasse1"):
        return self.client.post("/inscription", data={"email": email, "username": "Élève",
                                                      "password": password, "password2": password})

    def login(self, email, password):
        self.client.post("/deconnexion")
        return self.client.post("/connexion", data={"email": email, "password": password})

    def test_service_requires_account(self):
        for url in ("/tableau-de-bord", "/planning", "/agenda", "/matieres/nouvelle", "/admin/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/connexion", r.headers["Location"])

    def test_full_flow(self):
        self.assertEqual(self.register().status_code, 302)
        exam = (date.today() + timedelta(days=20)).isoformat()
        doc = io.BytesIO("# Les suites\ntexte\n# Limites\ntexte\n# Dérivation\ntexte".encode())
        r = self.client.post("/matieres/nouvelle", data={
            "name": "Analyse", "goal": "exam", "exam_date": exam, "comfort": "2", "topics": "",
            "documents": (doc, "cours.md")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        page = self.client.get(r.headers["Location"]).get_data(as_text=True)
        for t in ("Les suites", "Limites", "Dérivation"):
            self.assertIn(t, page)
        ics = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Cours\r\nDTSTART:20300101T100000\r\nDTEND:20300101T110000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "edt.ics")},
                         content_type="multipart/form-data")
        self.client.post("/planning/generer")
        with self.app.app_context():
            n = get_db().execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            sid = get_db().execute("SELECT id FROM sessions LIMIT 1").fetchone()[0]
        self.assertGreater(n, 5)
        self.assertEqual(self.client.get("/planning").status_code, 200)
        self.assertEqual(self.client.get("/tableau-de-bord").status_code, 200)
        self.client.post(f"/seances/{sid}/fait")
        r = self.client.get("/planning/export.ics")
        self.assertIn(b"BEGIN:VEVENT", r.data)
        self.assertEqual(self.client.get("/admin/").status_code, 403)

    def test_user_isolation(self):
        self.register("a@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Secret", "goal": "a_jour", "weekly_hours": "2",
                                                     "topics": "X\nY"})
        self.client.post("/deconnexion")
        self.register("b@test.fr")
        self.assertEqual(self.client.get("/matieres/1").status_code, 404)
        self.assertEqual(self.client.post("/matieres/1/supprimer").status_code, 404)

    def test_admin_crud(self):
        self.register("u@test.fr")
        self.login("admin@test.fr", "adminpass123")
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        r = self.client.post("/admin/utilisateurs/nouveau", data={"email": "new@test.fr", "username": "Nouveau",
                                                                  "password": "password12", "role": "user"})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            uid = get_db().execute("SELECT id FROM users WHERE email='new@test.fr'").fetchone()[0]
            admin_id = get_db().execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
        self.client.post(f"/admin/utilisateurs/{uid}", data={"email": "new@test.fr", "username": "Renommé",
                                                             "role": "user", "password": ""})
        self.client.post(f"/admin/utilisateurs/{uid}/supprimer")
        # le dernier admin ne peut pas se retirer ses droits
        self.client.post(f"/admin/utilisateurs/{admin_id}", data={"email": "admin@test.fr", "username": "A",
                                                                  "role": "user", "is_active": "on"})
        with self.app.app_context():
            self.assertIsNone(get_db().execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone())
            self.assertEqual(get_db().execute("SELECT role FROM users WHERE id=?", (admin_id,)).fetchone()[0], "admin")

    def _sessions(self, subject_id=None):
        with self.app.app_context():
            sql, args = "SELECT * FROM sessions", ()
            if subject_id:
                sql, args = sql + " WHERE subject_id = ?", (subject_id,)
            return [dict(r) for r in get_db().execute(sql + " ORDER BY id", args)]

    def test_preferences_apply_live(self):
        """Changer ses réglages recalcule le planning tout de suite, sans bouton à cliquer."""
        self.register()
        exam = (date.today() + timedelta(days=15)).isoformat()
        self.client.post("/matieres/nouvelle", data={"name": "Stats", "goal": "exam", "exam_date": exam,
                                                     "comfort": "3", "topics": "A\nB\nC"})
        self.assertTrue(self._sessions(), "le planning est créé dès l'ajout de la matière")
        tomorrow = (date.today() + timedelta(days=1)).weekday()
        self.client.post("/preferences", data={
            "method": "active_recall", "session_minutes": "40", "break_minutes": "5", "daily_max_minutes": "120",
            "day_start": "14:00", "day_end": "18:00", "rest_days": [str(tomorrow)], "buffer_minutes": "0",
            "horizon_weeks": "4"})
        sessions = self._sessions()
        self.assertTrue(sessions)
        per_day = {}
        for s in sessions:
            self.assertGreaterEqual(s["start"][11:16], "14:00")
            self.assertLessEqual(s["end"][11:16], "18:00")
            self.assertNotEqual(date.fromisoformat(s["start"][:10]).weekday(), tomorrow)
            self.assertIn("flashcards", s["activity"].lower() + " ") if s["kind"] == "study" else None
            minutes = (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).seconds // 60
            self.assertLessEqual(minutes, 40, "durée de séance respectée")
            per_day[s["start"][:10]] = per_day.get(s["start"][:10], 0) + minutes
        self.assertLessEqual(max(per_day.values()), 120, "temps max par jour respecté")
        page = self.client.get("/planning").get_data(as_text=True)
        self.assertIn("14:00", page)

    def test_agenda_change_applies_live(self):
        self.register()
        self.client.post("/matieres/nouvelle", data={"name": "Droit", "goal": "a_jour", "weekly_hours": "6",
                                                     "topics": "A\nB"})
        target = next(s for s in self._sessions())
        d, start, end = target["start"][:10], target["start"][11:16], target["end"][11:16]
        self.client.post("/agenda/ajouter", data={"title": "Cours", "date": d, "start": start, "end": end,
                                                  "repeat_weeks": "1"})
        for s in self._sessions():
            self.assertFalse(s["start"][:10] == d and s["start"][11:16] < end and s["end"][11:16] > start,
                             "aucune séance sur le nouveau créneau occupé")

    def test_subject_program_from_planning(self):
        """Depuis le planning : un clic sur une matière ouvre son panneau et construit son programme seule."""
        self.register()
        exam = (date.today() + timedelta(days=20)).isoformat()
        self.client.post("/matieres/nouvelle", data={"name": "Maths", "goal": "exam", "exam_date": exam,
                                                     "comfort": "3", "topics": "A\nB"})
        self.client.post("/matieres/nouvelle", data={"name": "Physique", "goal": "a_jour", "weekly_hours": "2",
                                                     "topics": "X\nY"})
        maths_before = self._sessions(1)
        page = self.client.get("/planning?matiere=2").get_data(as_text=True)
        self.assertIn("Construire le programme de Physique", page.replace("Recalculer", "Construire"))
        new_exam = (date.today() + timedelta(days=10)).isoformat()
        r = self.client.post("/matieres/2/programme", data={"goal": "exam", "exam_date": new_exam, "comfort": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("matiere=2", r.headers["Location"])
        self.assertEqual(self._sessions(1), maths_before, "les autres matières ne bougent pas")
        phys = self._sessions(2)
        self.assertTrue(phys)
        self.assertTrue(all(s["start"][:10] < new_exam for s in phys))
        self.assertTrue(any(s["kind"] == "practice" for s in phys))
        # date manquante : message clair, pas de crash
        r = self.client.post("/matieres/2/programme", data={"goal": "exam", "exam_date": "", "comfort": "3"},
                             follow_redirects=True)
        self.assertIn("date d&#39;examen à venir", r.get_data(as_text=True))

    def test_csrf_enforced(self):
        self.app.config["WTF_CSRF_DISABLED"] = False
        r = self.client.post("/connexion", data={"email": "x@y.fr", "password": "x"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
