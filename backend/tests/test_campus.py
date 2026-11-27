"""Campus : affluence (BU, restaurants Crous), menus. Règles pures, signalements et pages (réseau simulé)."""
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

from backend import create_app
from backend.business import campus, school_search
from backend.dao import campus_web
from backend.dao.db import get_db

THURSDAY_NOON = datetime(2026, 11, 26, 13, 0)
SUNDAY = datetime(2026, 11, 29, 13, 0)


def _days(**meals):
    names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    return [{"jour": n, "ouverture": {"matin": False, "midi": n in meals.get("midi", ()), "soir": n in meals.get("soir", ())}}
            for n in names]


WEEK = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]


class BusinessTests(unittest.TestCase):
    def test_distance(self):
        self.assertAlmostEqual(campus.haversine_km(48.8566, 2.3522, 48.1173, -1.6778), 308, delta=6)  # Paris - Rennes
        self.assertEqual(campus.haversine_km(48, 2, 48, 2), 0)

    def test_live_level_needs_fresh_reports(self):
        self.assertIsNone(campus.live_level([]))
        self.assertIsNone(campus.live_level([(3, 46)]), "trop ancien")
        self.assertIsNone(campus.live_level([(9, 1)]), "niveau invalide ignoré")
        live = campus.live_level([(3, 5)])
        self.assertEqual((live["level"], live["label"], live["count"], live["reliable"]), (3, "Animé", 1, False))

    def test_live_level_resists_a_prankster_and_favours_recent(self):
        self.assertEqual(campus.live_level([(2, 2), (2, 5), (2, 8), (4, 1)])["level"], 2, "un farceur ne suffit pas")
        self.assertEqual(campus.live_level([(1, 40), (4, 1)])["level"], 4, "le plus récent pèse plus")
        self.assertTrue(campus.live_level([(2, 1), (2, 2), (3, 3)])["reliable"])

    def test_place_keys(self):
        self.assertEqual(campus.bu_key("  ENSAI "), "bu:ensai")
        self.assertEqual(campus.bu_key("Université de Rennes 2"), "bu:universite-de-rennes-2")
        self.assertIsNone(campus.bu_key("  "))
        self.assertEqual(campus.crous_key("446"), "crous:446")
        self.assertEqual([campus.parse_level(v) for v in ("1", 4, "5", "x", None)], [1, 4, None, None, None])

    def test_typical_profiles(self):
        self.assertIn(15, campus.typical_profile("bu", 2))
        self.assertEqual(campus.typical_profile("bu", 6), {}, "BU en général fermée le dimanche")
        self.assertEqual(max(campus.typical_profile("crous", 2, {"midi"}), key=campus.typical_profile("crous", 2, {"midi"}).get), 12)
        self.assertEqual(campus.typical_profile("crous", 2, set()), {})
        self.assertIn(19, campus.typical_profile("crous", 2, {"midi", "soir"}))

    def test_quiet_hours_and_chart(self):
        profile = {11: 35, 12: 95, 13: 65, 14: 10}
        self.assertEqual(campus.quiet_hours(profile, 11), [11, 14], "les deux plus calmes, dans l'ordre")
        self.assertEqual(campus.quiet_hours(profile, 15), [], "plus rien d'ouvert")
        chart = campus.chart_hours({9: 20, 12: 50}, datetime(2026, 11, 26, 12, 5))
        self.assertEqual([b["hour"] for b in chart], [9, 10, 11, 12])
        self.assertEqual([b["open"] for b in chart], [True, False, False, True])
        self.assertTrue(chart[-1]["now"])
        self.assertEqual(campus.chart_hours({}, THURSDAY_NOON), [])

    def test_opening_and_service_state(self):
        days = _days(midi=WEEK, soir=["Lundi"])
        self.assertEqual(campus.meals_open(days, 3), {"midi"})
        self.assertEqual(campus.meals_open(days, 0), {"midi", "soir"})
        self.assertEqual(campus.meals_open(days, 6), set())
        self.assertEqual(campus.meals_open(None, 0), set())
        at = lambda h, m=0, d=26: datetime(2026, 11, d, h, m)  # le 26/11/2026 est un jeudi
        self.assertEqual(campus.service_state(days, at(9))["text"], "Ouvert ce midi")
        self.assertEqual(campus.service_state(days, at(12, 30))["text"], "Service du midi en cours")
        self.assertTrue(campus.service_state(days, at(12, 30))["now"])
        self.assertEqual(campus.service_state(days, at(16))["text"], "Services terminés pour aujourd'hui")
        self.assertEqual(campus.service_state(days, at(12, d=29))["text"], "Fermé aujourd'hui")

    def test_nearby(self):
        rs = [{"code": 1, "latitude": 48.03, "longitude": -1.74}, {"code": 2, "latitude": 48.12, "longitude": -1.68},
              {"code": 3, "latitude": 43.3, "longitude": 5.4}, {"code": 4, "latitude": None, "longitude": None}]
        found, inside = campus.nearby(rs, 48.02, -1.75)
        self.assertEqual(([r["code"] for r in found], inside), ([1], True), "dans 5 km, trié, sans coordonnées inconnues")
        found, inside = campus.nearby(rs, 45.0, 2.0)
        self.assertFalse(inside)
        self.assertEqual(len(found), 3, "aucun dans le rayon : les plus proches")

    def test_menu_parsing(self):
        data = [{"date": "26-11-2026", "repas": [
            {"type": "midi", "categories": [
                {"libelle": "Plats du jour", "plats": [{"libelle": "Lasagne  de légumes"}, {"libelle": "Lasagne de légumes"}, {"libelle": " "}]},
                {"libelle": "Vide", "plats": []}]},
            {"type": "soir", "categories": []}]}, {"date": "27-11-2026", "repas": []}]
        menu = campus.menu_for(data, date(2026, 11, 26))
        self.assertEqual(menu, {"midi": [{"category": "Plats du jour", "dishes": ["Lasagne de légumes"]}]})
        self.assertEqual(campus.menu_for(data, date(2026, 12, 1)), {})
        self.assertEqual(campus.menu_for(None, date(2026, 11, 26)), {})

    def test_locate_school_city(self):
        index = school_search.build_index([("Ecole Nationale de la Statistique", "Bruz", "Supérieur", "ENSAI"),
                                           ("Lycée Jean Moulin", "Rennes", "Lycée", "")])
        self.assertEqual(school_search.locate(index, "ENSAI"), "Bruz")
        self.assertEqual(school_search.locate(index, "Lycée Jean Moulin (Rennes)"), "Rennes")
        self.assertIsNone(school_search.locate(index, "Zzzz Inconnue"))
        self.assertIsNone(school_search.locate(index, ""))


RESTOS = [
    {"code": 1, "nom": "[1 €] Bruz • Resto U' Campus", "adresse": "1 rue de Test, 35170 BRUZ", "latitude": 48.03, "longitude": -1.74,
     "type": {"libelle": "Restaurant"}, "horaires": ["Du lundi au vendredi de 11h30 à 13h30"], "jours_ouvert": _days(midi=WEEK),
     "paiement": ["IZLY"], "ispmr": True, "telephone": "02 99 00 00 00"},
    {"code": 2, "nom": "Marseille • Resto lointain", "adresse": "Marseille", "latitude": 43.3, "longitude": 5.4,
     "type": {"libelle": "Restaurant"}, "horaires": [], "jours_ouvert": _days(midi=WEEK)},
]
MENU = [{"date": "26-11-2026", "repas": [{"type": "midi", "categories": [
    {"libelle": "Plats du jour", "plats": [{"libelle": "Lasagne de légumes"}, {"libelle": "Sauté de dinde"}]}]}]}]


class Fixed(datetime):
    @classmethod
    def now(cls, tz=None):
        return THURSDAY_NOON


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.patches = [
            patch("backend.services.campus_service.datetime", Fixed),
            patch.object(campus_web, "restaurants", return_value=RESTOS),
            patch.object(campus_web, "menu", return_value=MENU),
            patch.object(campus_web, "geocode_city", return_value=(48.02, -1.74, "Bruz")),
        ]
        for p in self.patches:
            p.start()
        self.lea = self.user("lea", "ENSAI")
        self.tom = self.user("tom", "Université de Rennes 2")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def user(self, name, school):
        c = self.app.test_client()
        c.post("/inscription", data={"email": f"{name}@test.fr", "username": name, "password": "motdepasse1",
                                     "password2": "motdepasse1", "level": "master", "school": school})
        return c

    def reports(self):
        with self.app.app_context():
            return [tuple(r) for r in get_db().execute("SELECT place_key, level FROM occupancy_reports ORDER BY id")]

    def test_login_required_and_nav(self):
        anon = self.app.test_client()
        for url in ("/campus/", "/campus/bibliotheque", "/campus/restos", "/campus/restos/1", "/campus/restos/1/menu.json"):
            self.assertEqual(anon.get(url).status_code, 302, url)
        self.assertEqual(anon.post("/campus/signaler", data={"place": "bu", "level": "2"}).status_code, 302)
        page = self.lea.get("/campus/bibliotheque").get_data(as_text=True)
        self.assertIn(">Campus</a>", page)
        self.assertIn(">Restos U</a>", page)

    def test_library_shows_an_honest_estimate_then_live_reports(self):
        page = self.lea.get("/campus/bibliotheque").get_data(as_text=True)
        self.assertIn("BU de ENSAI", page)
        self.assertIn("Bruz", page, "ville déduite de l'école")
        self.assertIn(">estimation<", page, "sans signalement : une estimation, étiquetée comme telle")
        self.assertNotIn("signalé par des étudiants", page)
        self.lea.post("/campus/signaler", data={"place": "bu", "level": "4", "back": "/campus/bibliotheque"})
        page = self.lea.get("/campus/bibliotheque").get_data(as_text=True)
        self.assertIn("signalé par des étudiants", page)
        self.assertIn(">Plein<", page)
        self.assertEqual(self.reports(), [("bu:ensai", 4)])

    def test_reports_are_per_school_and_cannot_be_spoofed(self):
        self.lea.post("/campus/signaler", data={"place": "bu", "level": "4"})
        self.assertNotIn("signalé par des étudiants", self.tom.get("/campus/bibliotheque").get_data(as_text=True),
                         "autre école : autre bibliothèque")
        self.tom.post("/campus/signaler", data={"place": "bu:ensai", "level": "1"})
        self.tom.post("/campus/signaler", data={"place": "bu", "level": "7"})
        self.assertEqual(self.reports(), [("bu:ensai", 4)], "ni lieu inventé, ni niveau invalide")

    def test_cooldown_between_reports(self):
        self.lea.post("/campus/signaler", data={"place": "bu", "level": "2"})
        r = self.lea.post("/campus/signaler", data={"place": "bu", "level": "4", "back": "/campus/bibliotheque"}, follow_redirects=True)
        self.assertIn("déjà signalé", r.get_data(as_text=True))
        self.assertEqual(self.reports(), [("bu:ensai", 2)])

    def test_no_school_prompts_for_profile(self):
        c = self.app.test_client()
        c.post("/inscription", data={"email": "x@test.fr", "username": "xo", "password": "motdepasse1", "password2": "motdepasse1",
                                     "level": "autre", "school": "ab"})
        with self.app.app_context():
            get_db().execute("UPDATE users SET school = '' WHERE email = 'x@test.fr'")
            get_db().commit()
        self.assertIn("Compléter mon profil", c.get("/campus/bibliotheque").get_data(as_text=True))
        c.post("/campus/signaler", data={"place": "bu", "level": "2"})
        self.assertEqual(self.reports(), [])

    def test_restaurants_near_the_school(self):
        page = self.lea.get("/campus/restos").get_data(as_text=True)
        self.assertIn("Resto U&#39; Campus", page)
        self.assertNotIn("Resto lointain", page, "hors du rayon")
        self.assertIn("Ouvert ce midi", page.replace("Service du midi en cours", "Ouvert ce midi"))
        self.assertIn("km", page)
        self.assertIn("/campus/restos/1/menu.json", page)
        self.assertIn("CROUStillant", page)
        self.assertEqual(self.lea.get("/campus/restos?ville=Marseille").status_code, 200)

    def test_restaurants_without_known_city_or_data(self):
        c = self.user("zoe", "Zzzz Xxxx")
        self.assertIn("ville de ton école", c.get("/campus/restos").get_data(as_text=True))
        with patch.object(campus_web, "geocode_city", return_value=None):
            self.assertIn("Je ne trouve pas la ville", self.lea.get("/campus/restos?ville=Nullepart").get_data(as_text=True))
        with patch.object(campus_web, "restaurants", side_effect=campus_web.Unavailable("hors ligne")):
            page = self.lea.get("/campus/restos")
            self.assertEqual(page.status_code, 200)
            self.assertIn("momentanément indisponibles", page.get_data(as_text=True))

    def test_restaurant_page_with_menu_and_report(self):
        page = self.lea.get("/campus/restos/1").get_data(as_text=True)
        self.assertIn("Lasagne de légumes", page)
        self.assertIn("Sauté de dinde", page)
        self.assertIn("IZLY", page)
        self.assertEqual(self.lea.get("/campus/restos/99").status_code, 404)
        self.lea.post("/campus/signaler", data={"place": "crous:1", "level": "4", "back": "/campus/restos/1"})
        self.assertEqual(self.reports(), [("crous:1", 4)])
        self.assertIn("signalé par des étudiants", self.tom.get("/campus/restos/1").get_data(as_text=True),
                      "les signalements d'un restaurant sont partagés entre tous")
        self.tom.post("/campus/signaler", data={"place": "crous:99", "level": "2"})
        self.tom.post("/campus/signaler", data={"place": "crous:abc", "level": "2"})
        self.assertEqual(len(self.reports()), 1, "restaurant inconnu refusé")

    def test_menu_unavailable_does_not_break_the_page(self):
        with patch.object(campus_web, "menu", side_effect=campus_web.Unavailable("hors ligne")):
            page = self.lea.get("/campus/restos/1")
            self.assertEqual(page.status_code, 200)
            self.assertIn("momentanément indisponible", page.get_data(as_text=True))
            self.assertEqual(self.lea.get("/campus/restos/1/menu.json").get_json(), {"error": True})

    def test_menu_json(self):
        data = self.lea.get("/campus/restos/1/menu.json").get_json()
        self.assertEqual(data["meals"]["midi"][0]["dishes"], ["Lasagne de légumes", "Sauté de dinde"])

    def test_unconfigured_network_degrades_gracefully(self):
        for p in self.patches[1:]:
            p.stop()  # ENABLE_WEB est faux dans ce test : aucun appel réseau, pas de plantage
        try:
            self.assertEqual(self.lea.get("/campus/restos").status_code, 200)
            self.assertEqual(self.lea.get("/campus/restos/1").status_code, 404)
        finally:
            for p in self.patches[1:]:
                p.start()


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


class MenuApiTests(unittest.TestCase):
    """« Pas de menu publié » (404) n'est pas une panne ; une vraie panne reste une erreur."""

    def setUp(self):
        campus_web._cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "ENABLE_WEB": True, "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})

    def tearDown(self):
        campus_web._cache.clear()
        self.tmp.cleanup()

    def test_restaurant_without_published_menu(self):
        with self.app.app_context(), patch("requests.get", return_value=_Resp(404, {"success": False})):
            self.assertEqual(campus_web.menu(935), [])

    def test_real_outage_is_unavailable(self):
        with self.app.app_context(), patch("requests.get", return_value=_Resp(503, {})):
            with self.assertRaises(campus_web.Unavailable):
                campus_web.menu(936)

    def test_published_menu_is_returned(self):
        with self.app.app_context(), patch("requests.get", return_value=_Resp(200, {"success": True, "data": MENU})):
            self.assertEqual(campus_web.menu(937), MENU)


class WebDaoTests(unittest.TestCase):
    def test_stale_data_is_kept_when_the_service_fails(self):
        import requests
        campus_web._cache.clear()
        calls = []

        def load():
            calls.append(1)
            if len(calls) > 1:
                raise requests.ConnectionError("hors ligne")
            return ["ok"]
        self.assertEqual(campus_web._cached("k", 0, load), ["ok"])
        self.assertEqual(campus_web._cached("k", 0, load), ["ok"], "donnée périmée plutôt que rien")
        with self.assertRaises(campus_web.Unavailable):
            campus_web._cached("autre", 0, load)
        campus_web._cache.clear()


if __name__ == "__main__":
    unittest.main()
