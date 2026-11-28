"""Lecture de photos par Groq : emploi du temps → agenda, notes de cours → supports. Groq est simulé."""
import io
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from backend import create_app
from backend.business import timetable_photo
from backend.dao import vision
from backend.dao.db import get_db


class SlotsTests(unittest.TestCase):
    def test_weekday_and_date_and_repeat(self):
        monday = date(2026, 11, 23)
        items = [{"title": "CM Probabilités", "weekday": 3, "start": "8h30", "end": "10:00"},
                 {"title": "Partiel d'analyse", "date": "2026-12-01", "start": "14:00", "end": "16:00", "exam": True},
                 {"title": "Passé", "date": "2026-11-01", "start": "14:00", "end": "16:00"},
                 {"title": "", "weekday": 2, "start": "8:00", "end": "9:00"},
                 {"title": "Fin avant début", "weekday": 2, "start": "10:00", "end": "9:00"}]
        out, skipped = timetable_photo.slots(items, monday, weeks=2)
        self.assertEqual(skipped, 2)
        cm = [e for e in out if e["title"] == "CM Probabilités"]
        self.assertEqual([e["start"].isoformat() for e in cm], ["2026-11-25T08:30:00", "2026-12-02T08:30:00"])
        self.assertEqual(len([e for e in out if e["exam"]]), 1, "un examen n'est pas répété")
        self.assertFalse([e for e in out if e["title"] == "Passé"])

    def test_bad_answer(self):
        self.assertEqual(timetable_photo.slots(None, date.today()), ([], 0))


class PhotoRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False, "VISION_AI": True,
            "GROQ_API_KEY": "gsk_test", "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
            "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up"),
        })
        self.client = self.app.test_client()
        self.client.post("/inscription", data={"email": "e@test.fr", "username": "Léa", "password": "Carnet-Revision-2026",
                                               "password2": "Carnet-Revision-2026", "level": "master", "school": "ENSAI"})

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def photo(self, name="edt.jpg"):
        return (io.BytesIO(b"fake image"), name, "image/jpeg")

    def test_timetable_photo_adds_events_once(self):
        day = (date.today() + timedelta(days=3)).isoformat()
        answer = {"events": [{"title": "CM Probabilités", "date": day, "start": "08:30", "end": "10:00"},
                             {"title": "Probabilités", "date": day, "start": "14:00", "end": "16:00", "exam": True}]}
        self.assertIn("Ajouter depuis une photo", self.client.get("/agenda").get_data(as_text=True))
        with mock.patch.object(vision, "ask", return_value=answer):
            page = self.client.post("/agenda/photo", data={"photo": self.photo(), "create_missing": "on"},
                                    content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
            self.assertIn("2 créneau(x) ajouté(s)", page)
            again = self.client.post("/agenda/photo", data={"photo": self.photo()},
                                     content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn("déjà dans ton agenda", again)
        rows = self.q("SELECT title, kind, source, subject_id FROM events ORDER BY start")
        self.assertEqual([(r["title"], r["kind"], r["source"]) for r in rows],
                         [("CM Probabilités", "cours", "photo"), ("Examen Probabilités", "examen", "photo")])
        self.assertTrue(rows[0]["subject_id"], "matière créée et rattachée")

    def test_timetable_photo_error(self):
        with mock.patch.object(vision, "ask", side_effect=vision.VisionError("Quota de Groq atteint.")):
            page = self.client.post("/agenda/photo", data={"photo": self.photo()},
                                    content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Photo non lue : Quota de Groq atteint.", page)
        self.assertFalse(self.q("SELECT id FROM events"))

    def test_course_photo_becomes_document(self):
        r = self.client.post("/matieres/nouvelle", data={"name": "Statistique", "goal": "a_jour", "weekly_hours": "2",
                                                         "comfort": "3", "topics": "A"})
        url = r.headers["Location"]
        self.assertIn("Ajouter depuis des photos", self.client.get(url).get_data(as_text=True))
        notes = "# Estimateurs\nBiais et variance.\n# Tests d'hypothèses\nLa statistique $T$ suit une loi de Student."
        with mock.patch.object(vision, "ask", return_value=notes):
            page = self.client.post(url + "/documents/photo", data={"photos": [self.photo("IMG_1.jpg")]},
                                    content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn("lue par Groq", page)
        doc = self.q("SELECT filename, stored_path FROM documents")[0]
        self.assertEqual(doc["filename"], "IMG_1 (photo).md")
        with open(doc["stored_path"], encoding="utf-8") as f:
            self.assertIn("loi de Student", f.read())
        topics = self.q("SELECT topics FROM subjects")[0]["topics"]
        self.assertIn("Estimateurs", topics)

    def test_disabled_without_key(self):
        self.app.config.update(VISION_AI=False)
        self.assertIn("Indisponible", self.client.get("/agenda").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
