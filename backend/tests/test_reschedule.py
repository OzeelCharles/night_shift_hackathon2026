"""« J'ai raté ma séance » : options simulées sans rien modifier, application après validation, confidentialité."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from backend import create_app
from backend.business import rescheduling as rules
from backend.dao.db import get_db


def call(name, **args):
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class RulesTests(unittest.TestCase):
    def test_rules(self):
        now = datetime(2026, 11, 28, 15, 0)
        since, now_iso, end_of_day = rules.window(now)
        self.assertEqual((since[:10], now_iso, end_of_day), ("2026-11-14", "2026-11-28T15:00", "2026-11-29T00:00"))
        self.assertEqual(rules.resume_from(["2026-11-28T10:00"], now), datetime(2026, 11, 29),
                         "séance ratée aujourd'hui : on reprend demain")
        self.assertIsNone(rules.resume_from(["2026-11-26T10:00"], now))
        self.assertEqual(rules.choose_options({"replacer": {"missing": 0}, "reorganiser": {"missing": 0}}), ["replacer"])
        self.assertEqual(rules.choose_options({"replacer": {"missing": 90}, "reorganiser": {"missing": 30},
                                               "plus_de_temps": {"missing": 30}}), ["replacer", "reorganiser"],
                         "une option n'est proposée que si elle fait mieux que les précédentes")
        today = date.today()
        blockers = rules.exam_blockers([{"name": "Proba", "goal": "exam", "exam_date": (today + timedelta(days=1)).isoformat()},
                                        {"name": "Éco", "goal": "exam", "exam_date": (today + timedelta(days=9)).isoformat()}],
                                       today)
        self.assertEqual(len(blockers), 1)
        self.assertIn("demain", blockers[0])


class RescheduleAppTests(unittest.TestCase):
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

    def tearDown(self):
        self.key.stop()
        self.tmp.cleanup()

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def run_sql(self, sql, *args):
        with self.app.app_context():
            get_db().execute(sql, args)
            get_db().commit()

    def login_as(self, email):
        self.client.post("/deconnexion")
        self.client.post("/inscription", data={"email": email, "username": email.split("@")[0], "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})
        self.client.post("/connexion", data={"email": email, "password": "motdepasse1"})

    def missed_session(self):
        """Matière avec examen dans 12 jours ; sa première séance est déplacée à hier, non faite (ratée)."""
        exam = (date.today() + timedelta(days=12)).isoformat()
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "exam", "exam_date": exam,
                                                     "comfort": "3", "topics": "Lois discrètes\nVecteurs gaussiens"})
        first = self.q("SELECT id, topic FROM sessions ORDER BY start LIMIT 1")[0]
        y = date.today() - timedelta(days=1)
        self.run_sql("UPDATE sessions SET start = ?, end = ? WHERE id = ?", f"{y}T10:00", f"{y}T10:50", first["id"])
        return first

    def prepare(self, **args):
        from backend.services import assistant_tools
        with self.app.test_request_context():
            from backend.dao import users
            user = users.get(self.q("SELECT id FROM users WHERE email = 'lea@test.fr'")[0]["id"])
            out = assistant_tools.preparer_rattrapage(user, **args)
            get_db().commit()
            return out

    def test_prepare_changes_nothing_then_apply_replans(self):
        self.login_as("lea@test.fr")
        missed = self.missed_session()
        before = self.q("SELECT id, start, end, topic FROM sessions ORDER BY id")
        out = self.prepare(matiere="proba")
        self.assertEqual(self.q("SELECT id, start, end, topic FROM sessions ORDER BY id"), before,
                         "préparer ne modifie rien (simulation annulée)")
        self.assertEqual(len(out["seances_a_rattraper"]), 1)
        self.assertIn(missed["topic"], out["seances_a_rattraper"][0])
        replacer = out["action"]["options"][0]
        self.assertEqual(replacer["key"], "replacer")
        self.assertTrue(any(missed["topic"] in d for d in replacer["details"]), "la séance ratée est replacée")
        self.assertEqual(replacer["warning"], "", "assez de temps avant l'examen")
        action_id = out["action"]["id"]

        r = self.client.post(f"/assistant/actions/{action_id}", json={"choice": "replacer"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("C'est fait", r.get_json()["message"]["content"])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM sessions WHERE id = ?", missed["id"])[0]["n"], 0,
                         "la séance ratée est retirée")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        replaced = self.q("SELECT start FROM sessions WHERE topic = ? AND kind = 'study' AND start >= ?",
                          missed["topic"], date.today().isoformat())
        self.assertTrue(replaced, "le chapitre raté a une nouvelle séance à venir")
        self.assertEqual(self.q("SELECT status, choice FROM chat_actions")[0], {"status": "done", "choice": "replacer"})
        # une proposition ne s'applique qu'une fois
        again = self.client.post(f"/assistant/actions/{action_id}", json={"choice": "replacer"})
        self.assertEqual(again.status_code, 422)
        self.assertIn("déjà été traitée", again.get_json()["error"])
        self.assertTrue(tomorrow)

    def test_tight_planning_offers_more_time(self):
        self.login_as("lea@test.fr")
        uid = self.q("SELECT id FROM users WHERE email = 'lea@test.fr'")[0]["id"]
        self.client.get("/preferences")   # crée la ligne des préférences
        self.run_sql("UPDATE preferences SET daily_max_minutes = 60, rest_days = '' WHERE user_id = ?", uid)
        exam = (date.today() + timedelta(days=4)).isoformat()
        topics = "\n".join(f"Chapitre {i}" for i in range(1, 9))
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "exam", "exam_date": exam,
                                                     "comfort": "2", "topics": topics})
        first = self.q("SELECT id FROM sessions ORDER BY start LIMIT 1")[0]
        y = date.today() - timedelta(days=1)
        self.run_sql("UPDATE sessions SET start = ?, end = ? WHERE id = ?", f"{y}T10:00", f"{y}T10:50", first["id"])
        out = self.prepare()
        keys = [o["key"] for o in out["action"]["options"]]
        self.assertTrue(out["action"]["options"][0]["warning"], "replacer laisse du travail sans créneau")
        self.assertIn("plus_de_temps", keys, "plus de temps par jour : moins de travail sans créneau")
        r = self.client.post(f"/assistant/actions/{out['action']['id']}", json={"choice": "plus_de_temps"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("2 h 00", r.get_json()["message"]["content"])
        self.assertEqual(self.q("SELECT daily_max_minutes FROM preferences WHERE user_id = ?", uid)[0]["daily_max_minutes"],
                         120, "le temps maximum par jour est augmenté (choix de l'étudiant)")

    def test_nothing_to_catch_up_and_privacy(self):
        self.login_as("lea@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})
        self.assertIn("à jour", self.prepare()["resultat"])
        self.missed_session()
        action_id = self.prepare()["action"]["id"]
        self.login_as("tom@test.fr")
        r = self.client.post(f"/assistant/actions/{action_id}", json={"choice": "replacer"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("introuvable", r.get_json()["error"], "on ne peut pas valider la proposition d'un autre")
        self.assertEqual(self.q("SELECT status FROM chat_actions")[0]["status"], "pending")

    def test_assistant_flow_with_buttons(self):
        self.login_as("lea@test.fr")
        self.missed_session()
        replies = [{"content": "", "tool_calls": [call("preparer_rattrapage", matiere="Probabilités")]},
                   {"content": "Je te propose de replacer la séance demain. Clique sur « Choisir ».", "tool_calls": []}]
        sent = []

        def fake(messages, tools=None, light=False, max_tokens=1500):
            sent.append(json.loads(json.dumps(messages)))
            return replies.pop(0)

        with mock.patch("backend.dao.llm.chat", fake):
            msg = self.client.post("/assistant/messages", json={"text": "J'ai raté ma séance de proba"}).get_json()["message"]
        self.assertEqual(len(msg["actions"]), 1)
        self.assertEqual(msg["actions"][0]["status"], "pending")
        tool_msg = next(m for m in sent[1] if m["role"] == "tool")
        self.assertIn("Rien n'est encore modifié", tool_msg["content"])
        self.assertNotIn('"action"', tool_msg["content"], "la proposition n'est pas envoyée au modèle")
        # annuler : rien ne change, la conversation le dit, et la carte s'affiche comme traitée au rechargement
        before = self.q("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
        r = self.client.post(f"/assistant/actions/{msg['actions'][0]['id']}/annuler")
        self.assertIn("je ne change rien", r.get_json()["message"]["content"])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM sessions")[0]["n"], before)
        messages = self.client.get("/assistant/conversation").get_json()["messages"]
        self.assertEqual(messages[1]["actions"][0]["status"], "cancelled")
        self.assertEqual(messages[-1]["content"], "D'accord, je ne change rien à ton planning.")


if __name__ == "__main__":
    unittest.main()
