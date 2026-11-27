import io
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from app import create_app, resources
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

    # ---------------------------------------------------------------- helpers
    def register(self, email="eleve@test.fr", password="motdepasse1", level="licence", school="Université de Rennes"):
        return self.client.post("/inscription", data={"email": email, "username": "Élève", "password": password,
                                                      "password2": password, "level": level, "school": school})

    def login(self, email, password):
        self.client.post("/deconnexion")
        return self.client.post("/connexion", data={"email": email, "password": password})

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def _sessions(self, subject_id=None):
        if subject_id:
            return self.q("SELECT * FROM sessions WHERE subject_id = ? ORDER BY id", subject_id)
        return self.q("SELECT * FROM sessions ORDER BY id")

    def new_subject(self, name, **extra):
        data = {"name": name, "goal": "a_jour", "weekly_hours": "2", "comfort": "3", "topics": "A\nB"}
        data.update(extra)
        return self.client.post("/matieres/nouvelle", data=data)

    def add_slot(self, title, kind="cours", subject="auto", day=None, start="10:00", end="12:00", weeks="1"):
        day = day or (date.today() + timedelta(days=2)).isoformat()
        return self.client.post("/agenda/ajouter", data={"title": title, "kind": kind, "subject": subject,
                                                         "date": day, "start": start, "end": end,
                                                         "repeat_weeks": weeks})

    # ---------------------------------------------------------------- comptes
    def test_service_requires_account(self):
        for url in ("/tableau-de-bord", "/planning", "/agenda", "/matieres/nouvelle", "/admin/", "/profil"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/connexion", r.headers["Location"])

    def test_register_requires_level_and_school(self):
        r = self.register(level="", school="")
        self.assertEqual(r.status_code, 200)  # formulaire réaffiché avec les erreurs
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email='eleve@test.fr'")[0]["n"], 0)
        self.register(level="master", school="ENSAI")
        u = self.q("SELECT level, school FROM users WHERE email='eleve@test.fr'")[0]
        self.assertEqual((u["level"], u["school"]), ("master", "ENSAI"))

    def test_profile_can_change_school(self):
        self.register(level="licence", school="Université de Rennes")
        r = self.client.post("/profil", data={"username": "Élève", "level": "master", "school": "ENSAI"})
        self.assertEqual(r.status_code, 302)
        u = self.q("SELECT level, school FROM users WHERE email='eleve@test.fr'")[0]
        self.assertEqual((u["level"], u["school"]), ("master", "ENSAI"))
        self.assertIn("ENSAI", self.client.get("/profil").get_data(as_text=True))

    # ---------------------------------------------------------------- parcours complet
    def test_full_flow(self):
        self.assertEqual(self.register().status_code, 302)
        exam = (date.today() + timedelta(days=20)).isoformat()
        doc = io.BytesIO("# Les suites\ntexte\n# Limites\ntexte\n# Dérivation\ntexte".encode())
        r = self.client.post("/matieres/nouvelle", data={
            "name": "Analyse", "goal": "exam", "exam_date": exam, "comfort": "2", "topics": "",
            "documents": (doc, "cours.md")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        page = self.client.get(r.headers["Location"]).get_data(as_text=True)
        for t in ("Les suites", "Limites", "Dérivation", "cours.md"):
            self.assertIn(t, page)
        ics = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Cours\r\nDTSTART:20300101T100000\r\nDTEND:20300101T110000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "edt.ics")},
                         content_type="multipart/form-data")
        self.assertGreater(len(self._sessions()), 5)
        sid = self._sessions()[0]["id"]
        self.assertEqual(self.client.get("/planning").status_code, 200)
        self.assertEqual(self.client.get("/tableau-de-bord").status_code, 200)
        self.client.post(f"/seances/{sid}/fait")
        r = self.client.get("/planning/export.ics")
        self.assertIn(b"BEGIN:VEVENT", r.data)
        self.assertEqual(self.client.get("/admin/").status_code, 403)

    def test_user_isolation(self):
        self.register("a@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Secret", "goal": "a_jour", "weekly_hours": "2",
                                                     "topics": "X\nY", "documents": (io.BytesIO(b"# T\nx"), "s.md")},
                         content_type="multipart/form-data")
        self.client.post("/deconnexion")
        self.register("b@test.fr")
        self.assertEqual(self.client.get("/matieres/1").status_code, 404)
        self.assertEqual(self.client.post("/matieres/1/supprimer").status_code, 404)
        self.assertEqual(self.client.get("/documents/1").status_code, 404, "document d'un autre : inaccessible")

    # ---------------------------------------------------------------- planning en direct
    def test_preferences_apply_live(self):
        """Changer ses réglages recalcule le planning tout de suite, sans bouton à cliquer."""
        self.register()
        exam = (date.today() + timedelta(days=15)).isoformat()
        self.new_subject("Stats", goal="exam", exam_date=exam, topics="A\nB\nC")
        self.assertTrue(self._sessions(), "le planning est créé dès l'ajout de la matière")
        tomorrow = (date.today() + timedelta(days=1)).weekday()
        self.client.post("/preferences", data={
            "method": "active_recall", "session_minutes": "40", "break_minutes": "5", "daily_max_minutes": "120",
            "day_start": "14:00", "day_end": "18:00", "rest_days": [str(tomorrow)], "buffer_minutes": "0",
            "horizon_weeks": "4"})
        per_day = {}
        for s in self._sessions():
            self.assertGreaterEqual(s["start"][11:16], "14:00")
            self.assertLessEqual(s["end"][11:16], "18:00")
            self.assertNotEqual(date.fromisoformat(s["start"][:10]).weekday(), tomorrow)
            minutes = (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).seconds // 60
            self.assertLessEqual(minutes, 40)
            per_day[s["start"][:10]] = per_day.get(s["start"][:10], 0) + minutes
        self.assertLessEqual(max(per_day.values()), 120)

    def test_agenda_change_applies_live(self):
        self.register()
        self.new_subject("Droit", weekly_hours="6")
        target = self._sessions()[0]
        d, start, end = target["start"][:10], target["start"][11:16], target["end"][11:16]
        self.add_slot("Sport", kind="autre", day=d, start=start, end=end)
        for s in self._sessions():
            self.assertFalse(s["start"][:10] == d and s["start"][11:16] < end and s["end"][11:16] > start)

    # ---------------------------------------------------------------- page matière
    def test_subject_page_shows_classes_revisions_and_documents(self):
        self.register()
        self.client.post("/matieres/nouvelle", data={
            "name": "Probabilités", "goal": "a_jour", "weekly_hours": "3", "topics": "Lois\nEspérance",
            "documents": (io.BytesIO(b"# Lois\ntexte"), "poly-proba.md")}, content_type="multipart/form-data")
        self.add_slot("CM Proba - Amphi B", kind="cours")
        page = self.client.get("/matieres/1").get_data(as_text=True)
        self.assertIn("Prochains cours", page)
        self.assertIn("CM Proba - Amphi B", page)
        self.assertIn("Prochaines révisions", page)
        self.assertIn("poly-proba.md", page)
        doc = self.client.get("/documents/1")
        self.assertEqual(doc.status_code, 200)
        self.assertIn(b"Lois", doc.data)
        doc.close()
        # le planning renvoie vers la page de la matière
        plan = self.client.get("/planning").get_data(as_text=True)
        self.assertIn("/matieres/1", plan)

    def test_subject_program_from_subject_page(self):
        self.register()
        exam = (date.today() + timedelta(days=20)).isoformat()
        self.new_subject("Maths", goal="exam", exam_date=exam)
        self.new_subject("Physique")
        maths_before = self._sessions(1)
        new_exam = (date.today() + timedelta(days=10)).isoformat()
        r = self.client.post("/matieres/2/programme", data={"goal": "exam", "exam_date": new_exam, "comfort": "1"})
        self.assertIn("/matieres/2", r.headers["Location"])
        self.assertEqual(self._sessions(1), maths_before, "les autres matières ne bougent pas")
        phys = self._sessions(2)
        self.assertTrue(phys and all(s["start"][:10] < new_exam for s in phys))
        r = self.client.post("/matieres/2/programme", data={"goal": "exam", "exam_date": "", "comfort": "3"},
                             follow_redirects=True)
        self.assertIn("date d&#39;examen à venir", r.get_data(as_text=True))

    # ---------------------------------------------------------------- rapprochement des noms
    def test_new_subject_with_similar_name_is_merged(self):
        self.register()
        self.new_subject("Probabilités", topics="Lois discrètes")
        r = self.new_subject("proba", topics="Loi normale")
        self.assertEqual(len(self.q("SELECT * FROM subjects")), 1, "pas de doublon")
        self.assertIn("/matieres/1", r.headers["Location"])
        topics = self.q("SELECT topics FROM subjects WHERE id = 1")[0]["topics"]
        self.assertIn("Loi normale", topics)
        page = self.client.get("/matieres/1").get_data(as_text=True)
        self.assertIn("Crée-la séparément", page)
        # l'utilisateur peut forcer la création séparée
        self.new_subject("proba", force_new="1")
        self.assertEqual(len(self.q("SELECT * FROM subjects")), 2)
        # un nom vraiment différent crée bien une nouvelle matière
        self.new_subject("Statique")
        self.assertEqual(len(self.q("SELECT * FROM subjects")), 3)

    def test_agenda_slots_are_linked_or_create_subject(self):
        self.register()
        self.new_subject("Probabilités")
        self.add_slot("TD Proba B204", kind="cours")
        self.assertEqual(self.q("SELECT subject_id FROM events")[0]["subject_id"], 1)
        self.add_slot("Sport", kind="autre", start="18:00", end="19:00")
        self.assertIsNone(self.q("SELECT subject_id FROM events WHERE title = 'Sport'")[0]["subject_id"])
        self.assertEqual(len(self.q("SELECT * FROM subjects")), 1, "une occupation ne crée pas de matière")
        self.add_slot("CM Économétrie - Amphi A", kind="cours", start="14:00", end="16:00")
        subjects = self.q("SELECT name FROM subjects ORDER BY id")
        self.assertEqual(subjects[-1]["name"], "Économétrie", "aucune matière proche : nouvelle matière créée")
        ev = self.q("SELECT subject_id FROM events WHERE title LIKE 'CM Éco%'")[0]
        self.assertEqual(ev["subject_id"], 2)

    def test_manual_revision_slot_survives_recalculation(self):
        self.register()
        self.new_subject("Probabilités")
        day = (date.today() + timedelta(days=3)).isoformat()
        self.add_slot("Révisions proba", kind="revision", day=day, start="07:00", end="08:00")
        manual = self.q("SELECT * FROM sessions WHERE manual = 1")
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["subject_id"], 1)
        self.client.post("/planning/generer")
        self.client.post("/preferences", data={"method": "mixte", "session_minutes": "50", "break_minutes": "10",
                                               "daily_max_minutes": "180", "day_start": "09:00", "day_end": "20:00",
                                               "buffer_minutes": "15", "horizon_weeks": "4"})
        self.assertEqual(len(self.q("SELECT * FROM sessions WHERE manual = 1")), 1, "jamais déplacé ni supprimé")
        self.assertIn("Mes créneaux de révision", self.client.get("/agenda").get_data(as_text=True))

    def test_ics_import_links_courses(self):
        self.register()
        self.new_subject("Droit des contrats")
        d1 = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
        d2 = (date.today() + timedelta(days=4)).strftime("%Y%m%d")
        ics = ("BEGIN:VCALENDAR\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:TD Droit des contrats (groupe 2)\r\nDTSTART:{d1}T100000\r\nDTEND:{d1}T120000\r\nEND:VEVENT\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:CM Macroéconomie\r\nDTSTART:{d2}T100000\r\nDTEND:{d2}T120000\r\nEND:VEVENT\r\n"
               "END:VCALENDAR\r\n").encode()
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "edt.ics"), "create_missing": "on"},
                         content_type="multipart/form-data")
        evs = {e["title"]: e for e in self.q("SELECT * FROM events")}
        self.assertEqual(evs["TD Droit des contrats (groupe 2)"]["subject_id"], 1)
        self.assertEqual(self.q("SELECT name FROM subjects WHERE id = 2")[0]["name"], "Macroéconomie")
        # correction manuelle du rattachement
        eid = evs["CM Macroéconomie"]["id"]
        self.client.post(f"/agenda/{eid}/matiere", data={"subject": ""})
        self.assertIsNone(self.q("SELECT subject_id FROM events WHERE id = ?", eid)[0]["subject_id"])

    # ---------------------------------------------------------------- ressources
    def test_keep_and_remove_resources(self):
        self.register(level="master", school="ENSAI")
        self.new_subject("Probabilités")
        fake = [
            {"title": "Cours vidéo", "url": "https://www.youtube.com/watch?v=abcdefghijk", "source": "YouTube",
             "kind": "video", "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg", "meta": "Prof, 12:00", "snippet": ""},
            {"title": "Poly ENSAI", "url": "https://ensai.fr/poly.pdf", "source": "ensai.fr", "kind": "web",
             "thumbnail": "", "meta": "Lié à ENSAI", "snippet": "poly"},
        ]
        from app import routes
        with self.app.test_request_context(), \
                mock.patch.object(resources, "find_resources", return_value=(fake, [])) as fr:
            with self.app.app_context():
                db = get_db()
                user = db.execute("SELECT * FROM users WHERE email='eleve@test.fr'").fetchone()
                routes.refresh_resources(db.execute("SELECT * FROM subjects WHERE id=1").fetchone(), user)
            self.assertEqual(fr.call_args.kwargs["level"], "master")
            self.assertEqual(fr.call_args.kwargs["school"], "ENSAI")
        page = self.client.get("/matieres/1").get_data(as_text=True)
        self.assertIn("Cours vidéo", page)
        self.assertIn("mqdefault.jpg", page)
        vid = self.q("SELECT id FROM resources WHERE kind = 'video'")[0]["id"]
        self.client.post(f"/ressources/{vid}/garder")
        self.assertEqual(self.q("SELECT saved FROM resources WHERE id = ?", vid)[0]["saved"], 1)
        # une nouvelle recherche ne supprime pas ce que l'utilisateur a gardé
        with self.app.test_request_context(), mock.patch.object(resources, "find_resources", return_value=([], [])):
            with self.app.app_context():
                db = get_db()
                user = db.execute("SELECT * FROM users WHERE email='eleve@test.fr'").fetchone()
                routes.refresh_resources(db.execute("SELECT * FROM subjects WHERE id=1").fetchone(), user)
        self.assertEqual([r["id"] for r in self.q("SELECT id FROM resources")], [vid])
        # lien personnel
        self.client.post("/matieres/1/ressources/ajouter", data={"title": "Mon lien", "url": "https://exemple.fr/td"})
        self.client.post("/matieres/1/ressources/ajouter", data={"title": "Piège", "url": "javascript:alert(1)"})
        self.assertEqual(len(self.q("SELECT * FROM resources WHERE kind = 'perso'")), 1)
        self.client.post(f"/ressources/{vid}/retirer")
        self.assertEqual(self.q("SELECT saved FROM resources WHERE id = ?", vid)[0]["saved"], 0)

    # ---------------------------------------------------------------- admin
    def test_admin_crud(self):
        self.register("u@test.fr")
        self.login("admin@test.fr", "adminpass123")
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        r = self.client.post("/admin/utilisateurs/nouveau", data={
            "email": "new@test.fr", "username": "Nouveau", "password": "password12", "role": "user",
            "level": "lycee", "school": "Lycée Chateaubriand"})
        self.assertEqual(r.status_code, 302)
        uid = self.q("SELECT id FROM users WHERE email='new@test.fr'")[0]["id"]
        admin_id = self.q("SELECT id FROM users WHERE role='admin'")[0]["id"]
        self.client.post(f"/admin/utilisateurs/{uid}", data={"email": "new@test.fr", "username": "Renommé",
                                                             "role": "user", "password": "", "level": "master",
                                                             "school": "ENSAI", "is_active": "on"})
        self.assertEqual(self.q("SELECT level, school FROM users WHERE id = ?", uid)[0],
                         {"level": "master", "school": "ENSAI"})
        self.client.post(f"/admin/utilisateurs/{uid}/supprimer")
        self.client.post(f"/admin/utilisateurs/{admin_id}", data={"email": "admin@test.fr", "username": "A",
                                                                  "role": "user", "is_active": "on"})
        self.assertFalse(self.q("SELECT 1 FROM users WHERE id = ?", uid))
        self.assertEqual(self.q("SELECT role FROM users WHERE id = ?", admin_id)[0]["role"], "admin")

    def test_csrf_enforced(self):
        self.app.config["WTF_CSRF_DISABLED"] = False
        r = self.client.post("/connexion", data={"email": "x@y.fr", "password": "x"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
