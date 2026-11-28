"""Assistant et vie étudiante : bibliothèque, restos U, caféine, recherche de stage (propositions validées par bouton)."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from backend import create_app
from backend.dao.db import get_db


def call(name, **args):
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


RESTAURANTS = [
    {"code": 1, "nom": "RU Ker Lann", "type": "Restaurant", "adresse": "Campus de Ker Lann", "latitude": 48.05,
     "longitude": -1.74, "jours_ouvert": [{"jour": j, "ouverture": {"midi": True}} for j in
                                          ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")]},
    {"code": 2, "nom": "Cafétéria Beaulieu", "type": "Cafétéria", "adresse": "Rennes", "latitude": 48.07,
     "longitude": -1.72, "jours_ouvert": []},
]
MENU = [{"date": datetime.now().strftime("%d-%m-%Y"), "repas": [
    {"type": "midi", "categories": [{"libelle": "Plat", "plats": [{"libelle": "Lasagnes"}, {"libelle": "Falafels"}]}]}]}]


class LifeToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False, "ASSISTANT_AI": True,
                               "ASSISTANT_PROVIDER": "groq",
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.client = self.app.test_client()
        self.key = mock.patch("backend.dao.llm.groq_key", return_value="gsk_test")
        self.key.start()
        self.client.post("/inscription", data={"email": "lea@test.fr", "username": "Léa", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})
        self.client.post("/connexion", data={"email": "lea@test.fr", "password": "motdepasse1"})

    def tearDown(self):
        self.key.stop()
        self.tmp.cleanup()

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def tool(self, name, **args):
        with self.app.test_request_context():
            from backend.dao import users
            user = users.get(self.q("SELECT id FROM users WHERE email = 'lea@test.fr'")[0]["id"])
            out = self.app.extensions["tools"][name].run(user, **args)
            get_db().commit()
            return out

    def ask(self, replies, text):
        sent = []

        def fake(messages, tools=None, light=False, max_tokens=1500):
            sent.append(json.loads(json.dumps(messages)))
            return replies.pop(0)

        with mock.patch("backend.dao.llm.chat", fake):
            msg = self.client.post("/assistant/messages", json={"text": text}).get_json()["message"]
        return msg, next(m for m in sent[1] if m["role"] == "tool")["content"]

    # ------------------------------------------------------------ campus
    def test_library(self):
        out = self.tool("bibliotheque")
        self.assertEqual(out["etablissement"], "ENSAI")
        self.assertIn("affluence_maintenant", out)
        self.assertEqual(out["links"][0]["items"][0]["url"], "/campus/bibliotheque")

    def test_restos_u_with_menu(self):
        with mock.patch("backend.dao.campus_web.geocode_city", return_value=(48.05, -1.74, "Bruz")), \
                mock.patch("backend.dao.campus_web.restaurants", return_value=RESTAURANTS), \
                mock.patch("backend.dao.campus_web.menu", return_value=MENU):
            out = self.tool("restos_u")
            asked = self.tool("restos_u", restaurant="beaulieu")
        self.assertEqual(out["autour_de"], "Bruz")
        self.assertEqual(out["restaurants"][0]["nom"], "RU Ker Lann", "le plus proche d'abord")
        self.assertEqual(out["menu_du_jour"]["restaurant"], "RU Ker Lann", "menu du plus proche ouvert")
        self.assertEqual(out["menu_du_jour"]["menus"]["midi"]["Plat"], ["Lasagnes", "Falafels"])
        self.assertEqual([i["url"] for i in out["links"][0]["items"]], ["/campus/restos/1", "/campus/restos/2"])
        self.assertEqual(asked["menu_du_jour"]["restaurant"], "Cafétéria Beaulieu", "menu du restaurant demandé")

    def test_restos_u_offline(self):
        self.assertIn("indisponibles", self.tool("restos_u")["erreur"], "ENABLE_WEB désactivé : erreur lisible")

    # ------------------------------------------------------------ caféine
    def test_caffeine(self):
        self.client.post("/cafeine/ajouter", data={"drink": "monster_500"})
        out = self.tool("cafeine")
        self.assertEqual(out["aujourd_hui_mg"], 160)
        self.assertEqual(out["seuil_quotidien_mg"], 400)
        self.assertEqual(len(out["prises_du_jour"]), 1)
        self.assertIn("Monster 500 ml", out["prises_du_jour"][0])
        self.assertEqual(out["teneurs_des_boissons_mg"]["Expresso"], 65)
        self.assertIn("pas un avis médical", out["reperes"])

    # ------------------------------------------------------------ stages
    def test_propose_internships_then_add_with_button(self):
        self.client.post("/profil", data={"phone": "0600000000"})  # coordonnées : jamais envoyées au modèle
        empty = self.tool("proposer_stages", description="un stage en data")
        self.assertIn("rappelle proposer_stages", empty["consigne"])
        self.assertNotIn("action", empty)

        replies = [{"content": "", "tool_calls": [call("proposer_stages", description="stage en data à Rennes",
                                                       postes="Data analyst ; Chargé d'études statistiques",
                                                       lieu="Rennes")]},
                   {"content": "Voici deux pistes : clique sur les recherches sous ma réponse.", "tool_calls": []}]
        msg, tool_msg = self.ask(replies, "Je cherche un stage en data à Rennes")
        self.assertEqual([g["title"] for g in msg["links"]], ["Data analyst · Rennes", "Chargé d'études statistiques · Rennes"])
        self.assertIn("Indeed", [i["label"] for i in msg["links"][0]["items"]])
        self.assertNotIn("https://", tool_msg, "les adresses ne sont pas envoyées au modèle")
        self.assertNotIn("lea@test.fr", tool_msg)
        self.assertNotIn("0600000000", tool_msg)
        self.assertEqual(self.client.get("/assistant/conversation").get_json()["messages"][1]["links"], msg["links"],
                         "les liens restent affichés au rechargement")

        action = msg["actions"][0]
        self.assertEqual(action["options"][0]["details"], ["Data analyst · Rennes", "Chargé d'études statistiques · Rennes"])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM stg_wishes")[0]["n"], 0, "rien n'est ajouté avant le clic")
        r = self.client.post(f"/assistant/actions/{action['id']}", json={"choice": "ajouter"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("2 poste(s) ajouté(s)", r.get_json()["message"]["content"])
        self.assertEqual([(w["title"], w["location"]) for w in self.q("SELECT title, location FROM stg_wishes ORDER BY id")],
                         [("Data analyst", "Rennes"), ("Chargé d'études statistiques", "Rennes")])
        self.assertEqual(self.client.post(f"/assistant/actions/{action['id']}", json={"choice": "ajouter"}).status_code, 422)

        again = self.tool("proposer_stages", postes="Data analyst", lieu="Rennes")
        self.assertNotIn("action", again, "poste déjà dans la liste : pas de bouton")

    def test_cancel_and_follow_up(self):
        out = self.tool("proposer_stages", postes="Assistant juridique", lieu="Lyon")
        r = self.client.post(f"/assistant/actions/{out['action']['id']}/annuler")
        self.assertEqual(r.get_json()["message"]["content"], "D'accord, je n'ajoute rien à ta liste.")
        self.assertEqual(self.client.post(f"/assistant/actions/{out['action']['id']}/annuler").status_code, 422,
                         "une proposition déjà traitée ne se traite plus")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM stg_wishes")[0]["n"], 0)

        self.client.post("/stages/offres", data={"title": "Stage juriste", "company": "Cabinet X", "location": "Lyon"})
        follow = self.tool("ma_recherche_de_stage")
        self.assertEqual(follow["offres_suivies"][0]["statut"], "À postuler")
        self.assertEqual(follow["links"][0]["items"][0]["url"], "/stages")


if __name__ == "__main__":
    unittest.main()
