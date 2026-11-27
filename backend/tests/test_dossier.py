"""Dossier scolaire : règles de calcul, matières listées sans note, confidentialité des notes."""
import io
import os
import re
import tempfile
import unittest
from datetime import date, timedelta

from backend import create_app
from backend.business import grades as rules
from backend.dao.db import get_db


def g(value, out_of=20, coefficient=1, day="2026-11-01"):
    return {"value": value, "out_of": out_of, "coefficient": coefficient, "day": day}


class GradeRulesTests(unittest.TestCase):
    def test_validate(self):
        data, errors = rules.validate({"value": "14,5", "out_of": "20", "coefficient": "2", "label": "Partiel"})
        self.assertEqual(errors, [])
        self.assertEqual((data["value"], data["coefficient"], data["day"]), (14.5, 2.0, date.today().isoformat()))
        self.assertTrue(rules.validate({"value": "25"})[1], "note au-dessus du barème refusée")
        self.assertTrue(rules.validate({"value": ""})[1])
        self.assertTrue(rules.validate({"value": "12", "coefficient": "0"})[1])
        future = (date.today() + timedelta(days=3)).isoformat()
        self.assertTrue(rules.validate({"value": "12", "day": future})[1], "pas de note dans le futur")

    def test_averages_and_progress(self):
        c = rules.Course("Maths", grades=[g(10), g(8, out_of=10, coefficient=2), g(18)])
        # sur 20 : 10, 16 (coef 2), 18 -> (10 + 32 + 18) / 4 = 15
        self.assertAlmostEqual(c.average, 15)
        self.assertAlmostEqual(c.progress, 15 - 14, msg="la dernière note fait passer de 14 à 15")
        self.assertIsNone(rules.Course("Vide").average)
        self.assertIsNone(rules.Course("Une", grades=[g(12)]).progress)

    def test_overall_and_history(self):
        a = rules.Course("A", grades=[g(10, day="2026-11-01"), g(14, day="2026-11-10")])
        b = rules.Course("B", grades=[g(16, day="2026-11-05")])
        empty = rules.Course("C")
        self.assertAlmostEqual(rules.overall([a, b, empty]), (12 + 16) / 2, msg="les matières sans note ne comptent pas")
        self.assertEqual([h["value"] for h in rules.overall_history([a, b])], [10, 13, 14])
        chart = rules.line_chart(rules.overall_history([a, b]))
        self.assertEqual(len(chart["points"]), 3)
        self.assertLess(chart["points"][0]["x"], chart["points"][-1]["x"])


class DossierAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def login_as(self, email):
        self.client.post("/deconnexion")
        self.client.post("/inscription", data={"email": email, "username": email.split("@")[0], "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})
        self.client.post("/connexion", data={"email": email, "password": "motdepasse1"})

    def import_course(self, title):
        d = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
        ics = (f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:{title}\r\nSUMMARY:{title}\r\n"
               f"DTSTART:{d}T100000\r\nDTEND:{d}T120000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
        self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "edt.ics")}, content_type="multipart/form-data")

    def add_grade(self, course, value, **extra):
        data = {"course": course, "value": value, "out_of": "20", "coefficient": "1", "label": "Contrôle"}
        data.update(extra)
        return self.client.post("/dossier/notes", data=data)

    def test_agenda_courses_are_listed_without_grades(self):
        self.login_as("lea@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})
        self.import_course("Séries temporelles 1 2ASTA03 - Marie")
        page = self.client.get("/dossier/").get_data(as_text=True)
        names = re.findall(r'<div class="course-name">\s*<strong>(.*?)</strong>', page)
        self.assertEqual(names, ["Probabilités", "Séries temporelles 1"], "matières créées puis cours de l'agenda")
        self.assertEqual(page.count("Sans note"), 2)
        self.assertIn("dans ton agenda", page)
        # barre du haut : l'espace « Dossier scolaire », surligné ; onglet « Mes notes »
        self.assertRegex(page, r'href="/dossier/"\s+aria-current="page">Dossier scolaire</a>')
        self.assertIn(">Mes notes</a>", page)

    def test_grades_progress_and_link_to_subject_created_later(self):
        self.login_as("lea@test.fr")
        self.import_course("Séries temporelles 1 2ASTA03 - Marie")
        old = (date.today() - timedelta(days=10)).isoformat()
        self.add_grade("Séries temporelles 1", "10", day=old)
        self.add_grade("Séries temporelles 1", "15")
        page = self.client.get("/dossier/").get_data(as_text=True)
        self.assertIn("12,5<small> / 20</small>", page, "moyenne de la matière")
        self.assertIn("+2,5 pt", page, "la dernière note fait progresser la moyenne de 10 à 12,5")
        self.assertIn('class="sparkline"', page)
        self.assertIn('class="chart"', page, "courbe de la moyenne générale (deux dates)")
        self.assertEqual({r["subject_id"] for r in self.q("SELECT subject_id FROM grades")}, {None})
        # la matière est créée ensuite : ses notes y sont rattachées
        self.client.post("/matieres/nouvelle", data={"name": "Séries temporelles 1", "goal": "a_jour",
                                                     "weekly_hours": "2", "comfort": "3", "topics": "A"})
        self.client.get("/dossier/")
        sid = self.q("SELECT id FROM subjects")[0]["id"]
        self.assertEqual({r["subject_id"] for r in self.q("SELECT subject_id FROM grades")}, {sid})
        # supprimer la matière garde l'historique des notes
        self.client.post(f"/matieres/{sid}/supprimer")
        self.assertEqual(len(self.q("SELECT * FROM grades")), 2)

    def test_invalid_grade_is_refused(self):
        self.login_as("lea@test.fr")
        r = self.add_grade("Probabilités", "25")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM grades")[0]["n"], 0)
        self.assertIn("comprise entre 0 et 20", self.client.get(r.headers["Location"]).get_data(as_text=True))

    def test_grades_are_private(self):
        self.login_as("lea@test.fr")
        self.add_grade("Probabilités", "17,25", label="Partiel secret")
        grade_id = self.q("SELECT id FROM grades")[0]["id"]
        self.assertIn("Partiel secret", self.client.get("/dossier/").get_data(as_text=True))

        self.login_as("tom@test.fr")
        page = self.client.get("/dossier/").get_data(as_text=True)
        self.assertNotIn("Partiel secret", page)
        self.assertNotIn("17,25", page)
        self.assertEqual(self.client.post(f"/dossier/notes/{grade_id}/supprimer").status_code, 404)
        self.assertEqual(len(self.q("SELECT * FROM grades")), 1, "la note de Léa n'a pas été supprimée")

        self.client.post("/deconnexion")
        self.client.post("/connexion", data={"email": "admin@test.fr", "password": "adminpass123"})
        lea_id = self.q("SELECT id FROM users WHERE email = 'lea@test.fr'")[0]["id"]
        for url in ["/admin/", f"/admin/utilisateurs/{lea_id}", "/admin/matieres", "/dossier/"]:
            body = self.client.get(url).get_data(as_text=True)
            self.assertNotIn("Partiel secret", body, f"aucune note visible par l'admin ({url})")
            self.assertNotIn("17,25", body)
        # supprimer le compte supprime ses notes
        self.client.post(f"/admin/utilisateurs/{lea_id}/supprimer")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM grades")[0]["n"], 0)

    def test_requires_account(self):
        r = self.client.get("/dossier/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/connexion", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
