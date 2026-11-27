import io
import os
import tempfile
import unittest
from datetime import date, timedelta

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

    def test_csrf_enforced(self):
        self.app.config["WTF_CSRF_DISABLED"] = False
        r = self.client.post("/connexion", data={"email": "x@y.fr", "password": "x"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
