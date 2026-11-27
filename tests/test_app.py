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

    # ---------------------------------------------------------------- suggestions & examens
    def test_subject_suggestions_from_agenda(self):
        self.register()
        exam_day = (date.today() + timedelta(days=15)).isoformat()
        # cours importés sans matière (cas typique d'un emploi du temps)
        d = (date.today() + timedelta(days=2)).strftime("%Y%m%d")
        x = exam_day.replace("-", "")
        ics = ("BEGIN:VCALENDAR\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:CM Probabilités - Amphi B\r\nDTSTART:{d}T100000\r\nDTEND:{d}T120000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nEND:VEVENT\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:TD Droit des contrats\r\nDTSTART:{d}T140000\r\nDTEND:{d}T160000\r\nEND:VEVENT\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:Partiel de probabilités\r\nDTSTART;VALUE=DATE:{x}\r\nEND:VEVENT\r\n"
               "END:VCALENDAR\r\n").encode()
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "edt.ics")}, content_type="multipart/form-data")
        exam_ev = self.q("SELECT * FROM events WHERE title LIKE 'Partiel%'")
        self.assertEqual(len(exam_ev), 1, "un partiel « journée entière » est gardé")
        self.assertEqual(exam_ev[0]["kind"], "examen")
        # sans saisie : les cours de l'agenda sont proposés
        initial = self.client.get("/api/suggestions/matieres").get_json()
        names = [s["name"] for s in initial]
        self.assertEqual(names[0], "Probabilités", "le cours le plus fréquent d'abord")
        self.assertIn("Droit des contrats", names)
        proba = initial[0]
        self.assertEqual(proba["exam_date"], exam_day, "date d'examen lue dans l'emploi du temps")
        self.assertEqual(proba["count"], 3)
        # en tapant : classement par similarité cosinus d'embeddings
        typed = self.client.get("/api/suggestions/matieres?q=dro").get_json()
        self.assertEqual(typed[0]["name"], "Droit des contrats")
        self.assertGreater(typed[0]["score"], 0.25)
        self.assertIn("Probabilités", self.client.get("/matieres/nouvelle").get_data(as_text=True))
        # création avec objectif examen sans date : la date est lue dans l'agenda
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "exam", "exam_date": "",
                                                     "comfort": "3", "topics": "A"})
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual((s["goal"], s["exam_date"]), ("exam", exam_day))
        linked = self.q("SELECT COUNT(*) AS n FROM events WHERE subject_id = ?", s["id"])[0]["n"]
        self.assertEqual(linked, 4, "3 cours + le partiel rattachés à la nouvelle matière")
        after = self.client.get("/api/suggestions/matieres?q=proba").get_json()
        self.assertEqual(after[0]["existing"]["id"], s["id"], "signale la matière déjà créée")
        self.assertNotIn("Probabilités", [x["name"] for x in self.client.get("/api/suggestions/matieres").get_json()])

    def test_exam_slot_sets_subject_exam_date(self):
        self.register()
        self.new_subject("Droit des contrats")
        day = (date.today() + timedelta(days=12)).isoformat()
        self.add_slot("Partiel droit des contrats", kind="examen", day=day, start="09:00", end="12:00")
        s = self.q("SELECT goal, exam_date FROM subjects WHERE id = 1")[0]
        self.assertEqual((s["goal"], s["exam_date"]), ("exam", day))
        page = self.client.get("/matieres/1").get_data(as_text=True)
        self.assertIn('id="examens"', page)
        self.assertIn("Partiel droit des contrats", page)
        # une autre date dans l'agenda : la page propose de l'utiliser, sans écraser
        other = (date.today() + timedelta(days=20)).isoformat()
        self.client.post("/matieres/1/programme", data={"goal": "exam", "exam_date": other, "comfort": "3"})
        page = self.client.get("/matieres/1").get_data(as_text=True)
        self.assertIn("Préparer l'examen du", page)

    def test_subject_form_error_is_not_a_server_error(self):
        """Régression : objectif examen sans date ni épreuve dans l'agenda -> formulaire réaffiché, pas d'erreur 500."""
        self.register()
        self.add_slot("CM Probabilités", kind="autre")  # un agenda non vide
        r = self.client.post("/matieres/nouvelle", data={"name": "Biologie", "goal": "exam", "exam_date": "",
                                                         "comfort": "3", "topics": ""})
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn("Indique la date de l&#39;examen.", page)
        self.assertIn('value="Biologie"', page, "la saisie est conservée")
        self.assertEqual(self.client.get("/matieres/nouvelle").status_code, 200)
        self.assertEqual(len(self.q("SELECT * FROM subjects")), 0)

    def test_agenda_live_link_preview(self):
        self.register()
        self.new_subject("Séries temporelles 1")
        page = self.client.get("/agenda").get_data(as_text=True)
        self.assertNotIn("Détecter automatiquement", page, "plus de menu de choix de matière : détection par défaut")
        self.assertIn("data-link-url", page)
        r = self.client.get("/api/agenda/rattachement?titre=" + "Séries temporelles 1 2ASTA03 - Marie-Lorraine CHAU").get_json()
        self.assertEqual(r["match"]["name"], "Séries temporelles 1")
        self.assertIsNone(r["new_name"])
        r = self.client.get("/api/agenda/rattachement?titre=Partiel de traitement du signal&type=examen").get_json()
        self.assertIsNone(r["match"])
        self.assertEqual(r["new_name"], "Traitement du signal")
        r = self.client.get("/api/agenda/rattachement?titre=séries&type=cours").get_json()
        self.assertEqual(r["suggestions"][0]["name"], "Séries temporelles 1")
        # l'enregistrement suit exactement l'aperçu : pas de doublon, la matière proche est utilisée
        self.add_slot("Séries temporelles 1 2ASTA03 - Marie-Lorraine CHAU", kind="cours")
        self.assertEqual(len(self.q("SELECT * FROM subjects")), 1)
        self.add_slot("Traitement du signal 2ASTA20 - Cédric HERZET", kind="cours", start="14:00", end="16:00")
        self.assertEqual(self.q("SELECT name FROM subjects ORDER BY id")[-1]["name"], "Traitement du signal")

    def test_holidays_are_free_days_and_not_suggested(self):
        self.register()
        day = date.today() + timedelta(days=2)
        while day.weekday() == 6:  # le dimanche est un jour de repos par défaut
            day += timedelta(days=1)
        d = day.strftime("%Y%m%d")
        ics = ("BEGIN:VCALENDAR\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:Vacances scolaires - Holidays  -\r\nDTSTART:{d}T080000\r\nDTEND:{d}T200000\r\nEND:VEVENT\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:Présentation des filières de 3A M347 - Christophe\r\nDTSTART:{d}T133000\r\nDTEND:{d}T140000\r\nEND:VEVENT\r\n"
               f"BEGIN:VEVENT\r\nSUMMARY:Séries temporelles 1 2ASTA03 - Marie\r\nDTSTART:{d}T140000\r\nDTEND:{d}T160000\r\nEND:VEVENT\r\n"
               "END:VCALENDAR\r\n").encode()
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "edt.ics"), "create_missing": "on"},
                         content_type="multipart/form-data")
        names = [s["name"] for s in self.q("SELECT name FROM subjects")]
        self.assertEqual(names, ["Séries temporelles 1"], "ni « Vacances » ni « Présentation » ne deviennent des matières")
        sugg = [s["name"] for s in self.client.get("/api/suggestions/matieres").get_json()]
        self.assertNotIn("Vacances scolaires", sugg)
        # des révisions peuvent être placées pendant les vacances (mais pas sur le cours ni la présentation)
        self.client.post("/matieres/1/programme", data={"goal": "a_jour", "weekly_hours": "10", "comfort": "3"})
        on_day = [s for s in self._sessions() if s["start"][:10] == day.isoformat()]
        self.assertTrue(on_day, "les vacances ne bloquent pas les révisions")
        for s in on_day:
            self.assertFalse(s["start"][11:16] < "16:15" and s["end"][11:16] > "13:15", "le cours reste bloqué")
        self.assertIn("Vacances scolaires", self.client.get(f"/planning?semaine={day.isoformat()}").get_data(as_text=True))

    # ---------------------------------------------------------------- examens et emploi du temps
    def _weekday(self, offset):
        d = date.today() + timedelta(days=offset)
        while d.weekday() == 6:
            d += timedelta(days=1)
        return d

    def test_last_class_is_default_exam_and_is_pushed_back(self):
        self.register()
        first = self._weekday(3)
        self.add_slot("CM Séries temporelles - Amphi B", kind="cours", day=first.isoformat(), weeks="3")
        s = self.q("SELECT * FROM subjects")[0]
        last = first + timedelta(weeks=2)
        self.assertEqual((s["goal"], s["exam_date"], s["exam_source"]), ("exam", last.isoformat(), "derniere_seance"),
                         "sans épreuve dans l'agenda, l'examen est la dernière séance")
        self.assertTrue(all(x["start"][:10] < last.isoformat() for x in self._sessions()), "révisions avant l'examen")
        plan = self.client.get(f"/planning?semaine={last.isoformat()}").get_data(as_text=True)
        self.assertIn("Examen (dernière séance)", plan)
        # un cours ajouté après la dernière séance repousse l'examen
        later = last + timedelta(weeks=1)
        r = self.add_slot("TD Séries temporelles", kind="cours", day=later.isoformat())
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual((s["exam_date"], s["exam_source"]), (later.isoformat(), "derniere_seance"))
        self.assertIn("repoussé", self._last_flashes(r))

    def _last_flashes(self, response):
        """Messages affichés après une redirection (on suit la redirection une fois)."""
        return self.client.get(response.headers["Location"]).get_data(as_text=True)

    def test_timetable_exam_and_extra_manual_exams(self):
        self.register()
        first = self._weekday(3)
        self.add_slot("CM Droit des contrats", kind="cours", day=first.isoformat(), weeks="4")
        exam_day = first + timedelta(days=16)
        self.add_slot("Partiel de droit des contrats", kind="examen", day=exam_day.isoformat(),
                      start="09:00", end="12:00")
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual((s["exam_date"], s["exam_source"]), (exam_day.isoformat(), "agenda"),
                         "une épreuve de l'emploi du temps l'emporte sur la dernière séance")
        # l'utilisateur ajoute un examen en plus (un oral plus tôt) : c'est le prochain à préparer
        oral = first + timedelta(days=8)
        self.client.post(f"/matieres/{s['id']}/examens", data={"date": oral.isoformat(), "start": "14:00",
                                                               "end": "14:30", "title": "Oral de droit"})
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual(s["exam_date"], oral.isoformat())
        exams = self.q("SELECT title, source FROM events WHERE kind = 'examen' ORDER BY start")
        self.assertEqual([e["title"] for e in exams], ["Oral de droit", "Partiel de droit des contrats"])
        page = self.client.get(f"/matieres/{s['id']}").get_data(as_text=True)
        self.assertIn("ajouté par toi", page)
        # une date choisie à la main reste prioritaire…
        chosen = (first + timedelta(days=20)).isoformat()
        self.client.post(f"/matieres/{s['id']}/programme", data={"goal": "exam", "exam_date": chosen, "comfort": "3"})
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual((s["exam_date"], s["exam_source"]), (chosen, "manuel"))
        self.add_slot("Examen final droit des contrats", kind="examen", day=(first + timedelta(days=18)).isoformat())
        self.assertEqual(self.q("SELECT exam_date FROM subjects")[0]["exam_date"], chosen)
        # …et recalculer sans changer la date ne la « fige » pas à tort
        self.client.post(f"/matieres/{s['id']}/examen/auto")
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual((s["exam_date"], s["exam_source"]), (oral.isoformat(), "agenda"))
        self.client.post(f"/matieres/{s['id']}/programme", data={"goal": "exam", "exam_date": oral.isoformat(),
                                                                 "comfort": "2"})
        s = self.q("SELECT * FROM subjects")[0]
        self.assertEqual(s["exam_source"], "agenda")
        self.assertIsNone(s["exam_manual"])

    def _ics(self, events):
        lines = ["BEGIN:VCALENDAR"]
        for uid, title, day, start, end in events:
            d = day.strftime("%Y%m%d")
            lines += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:{d}T{start}00",
                      f"DTEND:{d}T{end}00", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return ("\r\n".join(lines) + "\r\n").encode()

    def _import(self, events, replace=True):
        data = {"ics": (io.BytesIO(self._ics(events)), "edt.ics")}
        if replace:
            data["replace"] = "on"
        return self.client.post("/agenda/import", data=data, content_type="multipart/form-data")

    def test_reimport_with_few_changes_keeps_planning_stable(self):
        self.register()
        self.new_subject("Séries temporelles", goal="a_jour", weekly_hours="6")
        self.new_subject("Anglais", goal="a_jour", weekly_hours="3")
        days = [self._weekday(i) for i in range(1, 15)]
        timetable = [(f"ev{i}@ensai", "Séries temporelles 1 2ASTA03 - Marie" if i % 2 else "Anglais 2AHUM01 -",
                      d, "1000", "1200") for i, d in enumerate(days)]
        self._import(timetable)
        self.assertEqual(len(self.q("SELECT * FROM events")), 14)
        ids_before = {e["uid"]: e["id"] for e in self.q("SELECT id, uid FROM events")}
        # correction manuelle d'un rattachement : elle doit survivre aux mises à jour
        anglais_ev = self.q("SELECT id FROM events WHERE title LIKE 'Anglais%' LIMIT 1")[0]["id"]
        self.client.post(f"/agenda/{anglais_ev}/matiere", data={"subject": "1"})
        sessions_before = {s["id"]: (s["start"], s["end"]) for s in self._sessions()}
        self.assertTrue(sessions_before)

        # même fichier : rien ne bouge
        r = self._import(timetable)
        self.assertIn("aucun changement", self._last_flashes(r))
        self.assertEqual({s["id"]: (s["start"], s["end"]) for s in self._sessions()}, sessions_before)

        # nouvel emploi du temps : 1 cours déplacé sur une séance existante, 1 supprimé, 1 ajouté
        victim = next(s for s in self._sessions() if not s["manual"])
        vday = date.fromisoformat(victim["start"][:10])
        changed = list(timetable)
        moved_i = next(i for i, ev in enumerate(changed) if ev[2] != vday)
        uid, title, _, _, _ = changed[moved_i]
        changed[moved_i] = (uid, title, vday, victim["start"][11:13] + victim["start"][14:16],
                            victim["end"][11:13] + victim["end"][14:16])
        removed_uid = changed.pop(-1)[0]
        changed.append(("new@ensai", "Anglais 2AHUM01 -", self._weekday(16), "0800", "0900"))
        r = self._import(changed)
        msg = self._last_flashes(r)
        self.assertIn("1 ajouté(s), 1 déplacé(s) ou renommé(s), 1 supprimé(s), 12 inchangé(s)", msg)
        events = {e["uid"]: e for e in self.q("SELECT * FROM events")}
        self.assertNotIn(removed_uid, events)
        self.assertEqual(events[uid]["id"], ids_before[uid], "un cours déplacé garde sa ligne")
        self.assertEqual(self.q("SELECT subject_id FROM events WHERE id = ?", anglais_ev)[0]["subject_id"], 1,
                         "la correction manuelle est conservée")
        after = {s["id"]: (s["start"], s["end"]) for s in self._sessions()}
        self.assertNotIn(victim["id"], after, "la séance en conflit a été replacée")
        # Seules les séances qui gênent un cours nouveau ou déplacé (marge de 15 min comprise) changent.
        pad = timedelta(minutes=15)
        new_slots = [(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
                     for e in self.q("SELECT * FROM events WHERE uid IN (?, ?)", uid, "new@ensai")]
        expected_gone = {i for i, (a, b) in sessions_before.items()
                         if any(datetime.fromisoformat(a) < e + pad and s - pad < datetime.fromisoformat(b)
                                for s, e in new_slots)}
        gone = {i for i in sessions_before if after.get(i) != sessions_before[i]}
        self.assertTrue(expected_gone)
        self.assertEqual(gone, expected_gone, "le reste du planning ne bouge pas")
        for s in self._sessions():
            for e in self.q("SELECT start, end FROM events"):
                self.assertFalse(s["start"] < e["end"] and e["start"] < s["end"], "aucune séance sur un cours")

    def test_course_repeated_for_each_group_is_merged(self):
        """Export d'ENT réel : le même cours répété pour chaque groupe de TD (UID différents)."""
        self.register()
        day = self._weekday(4)
        timetable = [(f"grp{i}@ensai", "Séries temporelles 1 2ASTA03 -", day, "1400", "1640" if i == 2 else "1600")
                     for i in range(11)]
        r = self._import(timetable)
        rows = self.q("SELECT * FROM events")
        self.assertEqual(len(rows), 1, "un seul exemplaire du cours")
        self.assertEqual(rows[0]["end"][11:16], "16:40", "on garde l'exemplaire le plus long")
        self.assertIn("10 doublon(s)", self._last_flashes(r))
        r = self._import(timetable)
        self.assertEqual(len(self.q("SELECT * FROM events")), 1, "pas de doublon recréé à l'import suivant")

    def test_exam_resources_when_exam_is_near(self):
        from app import routes
        self.register(level="master", school="ENSAI")
        near = (date.today() + timedelta(days=10)).isoformat()
        far = (date.today() + timedelta(days=60)).isoformat()
        self.new_subject("Probabilités", goal="exam", exam_date=near)
        self.new_subject("Statistique", goal="exam", exam_date=far)
        fake = [{"title": "Annales corrigées", "url": "https://ensai.fr/annales.pdf", "source": "ensai.fr",
                 "kind": "exam", "thumbnail": "", "meta": "Lié à ENSAI", "snippet": ""}]
        calls = {}

        def fake_find(name, topics, kw, level, school, exam_days):
            calls[name] = exam_days
            return (fake if exam_days is not None else []), []

        with mock.patch.object(resources, "find_resources", side_effect=fake_find):
            with self.app.test_request_context(), self.app.app_context():
                db = get_db()
                user = db.execute("SELECT * FROM users WHERE email='eleve@test.fr'").fetchone()
                for sid in (1, 2):
                    routes.refresh_resources(db.execute("SELECT * FROM subjects WHERE id=?", (sid,)).fetchone(), user)
        self.assertEqual(calls["Probabilités"], 10, "examen dans 10 jours : ressources d'examen demandées")
        self.assertIsNone(calls["Statistique"], "examen lointain : pas encore de ressources d'examen")
        page = self.client.get("/matieres/1").get_data(as_text=True)
        self.assertIn("Préparer l&#39;examen", page.replace("Préparer l'examen", "Préparer l&#39;examen"))
        self.assertIn("Annales corrigées", page)
        self.assertNotIn('id="examen-ressources"', self.client.get("/matieres/2").get_data(as_text=True))
        sig = self.q("SELECT resources_sig FROM subjects WHERE id = 1")[0]["resources_sig"]
        self.assertTrue(sig.endswith("|examen"), "la signature change quand l'examen approche")

    def test_schools_api_is_public(self):
        r = self.client.get("/api/ecoles?q=ensai")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()[0]["value"], "ENSAI")

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
