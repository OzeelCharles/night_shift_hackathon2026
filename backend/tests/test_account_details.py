"""Informations personnelles du compte : facultatives, validées, réutilisées ailleurs."""
import os
import tempfile
import unittest
from datetime import date

from backend import create_app
from backend.business import accounts
from backend.dao.db import get_db


class DetailsRulesTests(unittest.TestCase):
    def test_age(self):
        self.assertEqual(accounts.age("2004-11-28", date(2026, 11, 27)), 21)
        self.assertEqual(accounts.age("2004-11-27", date(2026, 11, 27)), 22)
        self.assertIsNone(accounts.age("", date(2026, 11, 27)))

    def test_clean_details(self):
        values, errors = accounts.clean_details({"phone": "06 12 34 56 78", "website": "linkedin.com/in/lea",
                                                 "driving_license": "en_cours", "wants_internship": "peut-être",
                                                 "home_city": "  Lyon   7e "}, date(2026, 11, 27))
        self.assertEqual(errors, [])
        self.assertEqual(values["website"], "https://linkedin.com/in/lea")
        self.assertEqual(values["driving_license"], "en_cours")
        self.assertEqual(values["wants_internship"], "", "valeur inconnue ignorée")
        self.assertEqual(values["home_city"], "Lyon 7e")
        _, errors = accounts.clean_details({"phone": "abc", "birth_date": "2030-01-01"}, date(2026, 11, 27))
        self.assertEqual(len(errors), 2)


class ProfilePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.client = self.app.test_client()
        self.client.post("/inscription", data={"email": "e@test.fr", "username": "Léa", "password": "Carnet-Revision-2026",
                                               "password2": "Carnet-Revision-2026", "level": "licence", "school": "ENSAI"})

    def tearDown(self):
        self.tmp.cleanup()

    def user(self):
        with self.app.app_context():
            return dict(get_db().execute("SELECT * FROM users WHERE email = 'e@test.fr'").fetchone())

    def test_onboarding_window_once_after_signup(self):
        first = self.client.get("/preferences").get_data(as_text=True)
        self.assertIn('<dialog class="modal" id="onboarding"', first, "fenêtre ouverte juste après l'inscription")
        self.assertIn('action="/profil/infos"', first)
        self.assertNotIn('id="onboarding"', self.client.get("/preferences").get_data(as_text=True), "une seule fois")
        r = self.client.post("/profil/infos", data={"full_name": "Léa Martin", "home_city": "Rennes",
                                                    "driving_license": "en_cours", "next": "/preferences"})
        self.assertTrue(r.headers["Location"].endswith("/preferences"), "retour à la page d'où vient la fenêtre")
        u = self.user()
        self.assertEqual((u["full_name"], u["home_city"], u["driving_license"], u["level"], u["school"]),
                         ("Léa Martin", "Rennes", "en_cours", "licence", "ENSAI"), "le reste du compte est intact")

    def test_onboarding_errors_open_the_full_profile(self):
        page = self.client.post("/profil/infos", data={"phone": "abc", "home_city": "Brest"}).get_data(as_text=True)
        self.assertIn("Numéro de téléphone invalide.", page)
        self.assertIn('value="Brest"', page, "la saisie est conservée")
        self.assertIn('value="ENSAI"', page, "le formulaire complet est prérempli avec le compte")
        self.assertEqual(self.user()["home_city"], "")
        self.assertEqual(self.client.post("/profil/infos", data={"next": "https://ailleurs.example"}).headers["Location"],
                         "/tableau-de-bord", "pas de redirection vers un autre site")

    def test_save_and_show_details(self):
        page = self.client.get("/profil").get_data(as_text=True)
        self.assertIn('<label>Ville où tu habites <input name="home_city"', page, "formulaire classique")
        self.assertNotIn('class="madlib"', page)
        self.client.post("/profil", data={"username": "Léa", "level": "licence", "school": "ENSAI",
                                          "full_name": "Léa Martin", "birth_date": "2004-03-01",
                                          "home_city": "Rennes", "study_city": "Bruz", "driving_license": "oui",
                                          "wants_internship": "oui", "field": "Statistique", "languages": "anglais"})
        u = self.user()
        self.assertEqual((u["full_name"], u["home_city"], u["study_city"], u["driving_license"], u["wants_internship"]),
                         ("Léa Martin", "Rennes", "Bruz", "oui", "oui"))
        page = self.client.get("/profil").get_data(as_text=True)
        self.assertIn('value="Léa Martin"', page)
        self.assertIn('value="oui" checked', page)
        self.assertIn('title="Mon compte">Léa Martin</a>', page, "le nom complet s'affiche dans la barre")

    def test_invalid_details_keep_input(self):
        r = self.client.post("/profil", data={"username": "Léa", "level": "licence", "school": "ENSAI",
                                              "phone": "pas un numéro", "home_city": "Nantes"})
        page = r.get_data(as_text=True)
        self.assertIn("Numéro de téléphone invalide.", page)
        self.assertIn('value="Nantes"', page, "la saisie est conservée")
        self.assertEqual(self.user()["home_city"], "")


if __name__ == "__main__":
    unittest.main()
