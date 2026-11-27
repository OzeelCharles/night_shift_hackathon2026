"""Compteur de caféine : règles pures (business), cas d'usage (service) et pages."""
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta

from backend import create_app
from backend.business import caffeine
from backend.dao.db import get_db

NOON = datetime(2026, 11, 27, 12, 0)
ADULT = caffeine.limits_for("licence")


class BusinessTests(unittest.TestCase):
    def test_half_life(self):
        self.assertAlmostEqual(caffeine.active_mg([(NOON, 80)], NOON), 80)
        self.assertAlmostEqual(caffeine.active_mg([(NOON, 80)], NOON + timedelta(hours=5)), 40)
        self.assertAlmostEqual(caffeine.active_mg([(NOON, 80)], NOON + timedelta(hours=10)), 20)
        self.assertEqual(caffeine.active_mg([(NOON + timedelta(hours=1), 80)], NOON), 0, "pas encore bue")

    def test_limits_depend_on_level(self):
        self.assertEqual(caffeine.limits_for("college").daily, 150)
        self.assertEqual(caffeine.limits_for("lycee").daily, 150)
        self.assertEqual(caffeine.limits_for("master").daily, 400)
        self.assertEqual(caffeine.limits_for("inconnu").daily, 400)

    def test_tiers(self):
        self.assertEqual(caffeine.tier_for(0)[1], "Zen")
        self.assertEqual(caffeine.tier_for(0.2)[1], "Éveillé")
        self.assertEqual(caffeine.tier_for(0.5)[1], "Bien chargé")
        self.assertEqual(caffeine.tier_for(0.99)[1], "Hyperactif")
        self.assertEqual(caffeine.tier_for(1.0)[1], "Zone rouge")
        self.assertEqual(caffeine.tier_for(2.5)[1], "Zone rouge")

    def test_can_drink_when_nothing_taken(self):
        self.assertIsNone(caffeine.next_ok([], NOON, ADULT))

    def test_wait_until_active_level_has_dropped(self):
        # 240 mg actifs : il faut descendre à 120 mg (200 - 80), soit une demi-vie : 5 h
        intakes = [(NOON, 240)]
        when = caffeine.next_ok(intakes, NOON, ADULT)
        self.assertEqual(when, NOON + timedelta(hours=5))
        self.assertIsNone(caffeine.next_ok(intakes, when, ADULT), "à l'heure annoncée, on peut en reprendre")
        self.assertIsNotNone(caffeine.next_ok(intakes, when - timedelta(minutes=10), ADULT))

    def test_daily_limit_pushes_to_tomorrow(self):
        morning = datetime(2026, 11, 27, 8, 0)
        intakes = [(morning, 160), (morning, 160), (morning, 80)]
        status = caffeine.status(intakes, NOON, "licence")
        self.assertEqual(status["total"], 400)
        self.assertGreaterEqual(status["next_ok"], datetime(2026, 11, 28, 0, 0))
        self.assertFalse(status["can_drink"])

    def test_minor_gets_stricter_daily_limit(self):
        intakes = [(NOON, 80), (NOON, 80)]
        self.assertTrue(caffeine.status(intakes, NOON, "lycee")["over"])
        self.assertFalse(caffeine.status(intakes, NOON, "licence")["over"])

    def test_sleep_risk_uses_bedtime(self):
        evening = [(datetime(2026, 11, 27, 18, 0), 160)]  # 5 h avant 23 h : il reste 80 mg
        self.assertTrue(caffeine.status(evening, datetime(2026, 11, 27, 18, 30), "licence")["sleep_risk"])
        morning = [(datetime(2026, 11, 27, 8, 0), 80)]    # 15 h avant : 10 mg
        self.assertFalse(caffeine.status(morning, NOON, "licence")["sleep_risk"])
        late = caffeine.status(morning, datetime(2026, 11, 27, 23, 30), "licence")
        self.assertIsNone(late["at_bedtime"], "après l'heure du coucher, plus de projection")

    def test_next_night(self):
        bed, wake = caffeine.next_night(NOON, time(23), time(7))
        self.assertEqual((bed, wake), (datetime(2026, 11, 27, 23, 0), datetime(2026, 11, 28, 7, 0)))
        # en pleine nuit : c'est la nuit commencée la veille
        bed, wake = caffeine.next_night(datetime(2026, 11, 28, 3, 0), time(23), time(7))
        self.assertEqual(bed, datetime(2026, 11, 27, 23, 0))
        # le matin, après le réveil : la prochaine nuit
        bed, _ = caffeine.next_night(datetime(2026, 11, 28, 8, 0), time(23), time(7))
        self.assertEqual(bed, datetime(2026, 11, 28, 23, 0))
        # coucher après minuit : l'après-midi, c'est cette nuit-là (le lendemain à 1 h)
        bed, wake = caffeine.next_night(NOON, time(1), time(9))
        self.assertEqual((bed, wake), (datetime(2026, 11, 28, 1, 0), datetime(2026, 11, 28, 9, 0)))

    def test_sleep_loss_grows_with_caffeine_left_at_bedtime(self):
        none = caffeine.status([], NOON, "licence")
        self.assertEqual((none["sleep_planned"], none["sleep_lost"], none["sleep_estimated"]), (480, 0, 480))
        self.assertFalse(none["sleep_short"])
        # 320 mg à 18 h, coucher à 23 h : il reste 160 mg, soit 56 min perdues
        late = caffeine.status([(datetime(2026, 11, 27, 18, 0), 320)], datetime(2026, 11, 27, 18, 5), "licence")
        self.assertEqual(late["at_bedtime"], 160)  # exactement une demi-vie
        self.assertEqual(late["sleep_lost"], round(late["at_bedtime"] * caffeine.SLEEP_LOSS_MIN_PER_MG))
        self.assertEqual(late["sleep_estimated"], 480 - late["sleep_lost"])
        self.assertTrue(late["sleep_short"], "8 h prévues moins la perte : sous les 8 h recommandées")

    def test_short_night_and_minor_need(self):
        short = caffeine.status([], NOON, "licence", time(1), time(6))
        self.assertEqual(short["sleep_planned"], 300)
        self.assertTrue(short["sleep_short"])
        eight_hours = caffeine.status([], NOON, "lycee")  # 8 h prévues, mais un lycéen en a besoin de 9
        self.assertTrue(eight_hours["sleep_short"])

    def test_last_drink_time(self):
        bed = datetime(2026, 11, 27, 23, 0)
        cutoff, none_left = caffeine.last_drink_time([], bed, NOON)
        self.assertFalse(none_left)
        # 80 mg bus à `cutoff` laissent 50 mg au coucher : 5 * log2(80/50) = 3,39 h avant
        self.assertEqual(cutoff, datetime(2026, 11, 27, 19, 36))
        self.assertAlmostEqual(caffeine.active_mg([(cutoff, 80)], bed), caffeine.SLEEP_RESIDUAL_MG, delta=1.5)
        _, none_left = caffeine.last_drink_time([(NOON, 400)], bed, NOON)
        self.assertTrue(none_left, "déjà trop de caféine prévue au coucher")

    def test_in_night_has_no_projection(self):
        night = caffeine.status([(datetime(2026, 11, 27, 22, 0), 160)], datetime(2026, 11, 28, 2, 0), "licence")
        self.assertTrue(night["in_night"])
        self.assertIsNone(night["at_bedtime"])
        self.assertIsNone(night["cutoff"])


class AppTests(unittest.TestCase):
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

    def register(self, email="e@test.fr", level="licence"):
        self.client.post("/inscription", data={"email": email, "username": "Léa", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": level, "school": "ENSAI"})

    def rows(self):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute("SELECT * FROM caffeine_intakes ORDER BY id")]

    def test_requires_login(self):
        for method, url in (("get", "/cafeine"), ("post", "/cafeine/ajouter"), ("post", "/cafeine/1/supprimer")):
            r = getattr(self.client, method)(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/connexion", r.headers["Location"])

    def test_menu_entry_and_empty_page(self):
        self.register()
        page = self.client.get("/cafeine").get_data(as_text=True)
        self.assertIn(">Caféine</a>", page)
        self.assertIn("Zen", page)
        self.assertIn("0 mg</strong> sur 400 mg", page)
        self.assertIn("Oui, tu peux reprendre", page)
        self.assertNotIn("Série", page, "pas de série tant qu'aucune prise n'a été enregistrée")

    def test_add_catalog_drink(self):
        self.register()
        r = self.client.post("/cafeine/ajouter", data={"drink": "redbull_250"}, follow_redirects=True)
        self.assertIn("Red Bull 250 ml ajouté (80 mg).", r.get_data(as_text=True))
        self.assertEqual([(x["label"], x["mg"]) for x in self.rows()], [("Red Bull 250 ml", 80)])
        page = self.client.get("/cafeine").get_data(as_text=True)
        self.assertIn("80 mg</strong> sur 400 mg", page)
        self.assertIn("Éveillé", page)

    def test_add_custom_drink_and_validation(self):
        self.register()
        self.client.post("/cafeine/ajouter", data={"custom_label": "Boisson maison", "custom_mg": "120"})
        self.assertEqual([(x["label"], x["mg"]) for x in self.rows()], [("Boisson maison", 120)])
        for bad in ({}, {"drink": "inconnue"}, {"custom_mg": "0"}, {"custom_mg": "abc"}):
            r = self.client.post("/cafeine/ajouter", data=bad, follow_redirects=True)
            self.assertIn("Choisis une boisson", r.get_data(as_text=True), bad)
        self.assertEqual(len(self.rows()), 1, "rien d'enregistré pour une saisie invalide")
        self.client.post("/cafeine/ajouter", data={"custom_mg": "9999"})
        self.assertEqual(self.rows()[-1]["mg"], 500, "borné à 500 mg")

    def test_time_in_the_past_ok_in_the_future_refused(self):
        self.register()
        self.client.post("/cafeine/ajouter", data={"drink": "cafe_expresso", "time": "00:00"})
        self.assertEqual(self.rows()[0]["taken_at"], f"{date.today().isoformat()}T00:00")
        future = datetime.now() + timedelta(hours=2)
        if future.date() == date.today():  # n'a de sens que si la journée n'est pas bientôt finie
            r = self.client.post("/cafeine/ajouter", data={"drink": "the", "time": future.strftime("%H:%M")},
                                 follow_redirects=True)
            self.assertIn("dans le futur", r.get_data(as_text=True))
            self.assertEqual(len(self.rows()), 1)
        r = self.client.post("/cafeine/ajouter", data={"drink": "the", "time": "pas une heure"}, follow_redirects=True)
        self.assertIn("Heure invalide", r.get_data(as_text=True))

    def test_warning_when_daily_limit_crossed(self):
        self.register(level="lycee")  # seuil 150 mg
        self.client.post("/cafeine/ajouter", data={"drink": "redbull_250", "time": "00:00"})
        r = self.client.post("/cafeine/ajouter", data={"drink": "redbull_250", "time": "00:01"}, follow_redirects=True)
        page = r.get_data(as_text=True)
        self.assertIn("viens de dépasser le seuil recommandé", page)
        self.assertIn("Zone rouge", page)
        self.assertIn("sur 150 mg", page)

    def test_delete_own_intake_only(self):
        self.register()
        self.client.post("/cafeine/ajouter", data={"drink": "the"})
        intake_id = self.rows()[0]["id"]
        self.client.post("/deconnexion")
        self.register(email="autre@test.fr")
        self.client.post(f"/cafeine/{intake_id}/supprimer")
        self.assertEqual(len(self.rows()), 1, "on ne supprime pas la prise d'un autre")
        self.client.post("/deconnexion")
        self.client.post("/connexion", data={"email": "e@test.fr", "password": "motdepasse1"})
        r = self.client.post(f"/cafeine/{intake_id}/supprimer", follow_redirects=True)
        self.assertIn("Prise supprimée", r.get_data(as_text=True))
        self.assertEqual(self.rows(), [])

    def test_history_and_streak(self):
        self.register()
        two_days_ago = (date.today() - timedelta(days=2)).isoformat()
        with self.app.app_context():
            get_db().execute("INSERT INTO caffeine_intakes (user_id, label, mg, taken_at) "
                             "VALUES (2, 'Monster', 160, ?)", (f"{two_days_ago}T10:00",))
            get_db().commit()
        page = self.client.get("/cafeine").get_data(as_text=True)
        self.assertIn("160 mg</span>", page, "l'historique affiche le total du jour")
        self.assertIn("<strong>2 jour(s)</strong> sans caféine", page)

    def test_sleep_settings_saved_and_shown(self):
        self.register()
        page = self.client.get("/cafeine").get_data(as_text=True)
        self.assertIn('name="bedtime" value="23:00"', page)
        self.assertIn('name="wake_time" value="07:00"', page)
        r = self.client.post("/cafeine/sommeil", data={"bedtime": "01:30", "wake_time": "09:00"}, follow_redirects=True)
        page = r.get_data(as_text=True)
        self.assertIn("Horaires de sommeil enregistrés.", page)
        self.assertIn('name="bedtime" value="01:30"', page)
        self.assertIn("de nuit prévue (01:30 → 09:00)", page)
        self.assertIn("7 h 30", page.replace("\xa0", " "), "7 h 30 de nuit")

    def test_sleep_settings_validation(self):
        self.register()
        for bad in ({}, {"bedtime": "23:00"}, {"bedtime": "x", "wake_time": "07:00"},
                    {"bedtime": "23:00", "wake_time": "23:30"}, {"bedtime": "08:00", "wake_time": "07:00"}):
            r = self.client.post("/cafeine/sommeil", data=bad, follow_redirects=True)
            page = r.get_data(as_text=True)
            self.assertTrue("Indique ton heure" in page or "entre 3 h et 14 h" in page, bad)
        self.assertIn('name="bedtime" value="23:00"', self.client.get("/cafeine").get_data(as_text=True))

    def test_sleep_card_always_present(self):
        self.register()
        self.client.post("/cafeine/ajouter", data={"drink": "monster_500", "time": "00:00"})
        page = self.client.get("/cafeine").get_data(as_text=True)
        self.assertIn("Mon sommeil", page)
        self.assertIn("de nuit prévue", page)


if __name__ == "__main__":
    unittest.main()
