"""Assistant : boucle d'outils, historique (fenêtre + résumé), graphiques, confidentialité. Groq est simulé."""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from backend import create_app
from backend.business import assistant as rules
from backend.dao.db import get_db


def call(name, **args):
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeGroq:
    """Rejoue des réponses préparées et garde les conversations reçues."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, tools=None, light=False, max_tokens=1500):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools, "light": light})
        if light:
            return {"content": "Résumé : on a parlé de probabilités.", "tool_calls": []}
        reply = self.replies.pop(0) if self.replies else "D'accord."
        return reply if isinstance(reply, dict) else {"content": reply, "tool_calls": []}


class RulesTests(unittest.TestCase):
    def test_history_window(self):
        msgs = [{"id": i, "role": "user", "content": str(i)} for i in range(1, 11)]
        self.assertEqual(rules.split_history(msgs, 0), ([], msgs), "10 messages : pas encore de résumé")
        msgs = [{"id": i, "role": "user", "content": str(i)} for i in range(1, 15)]
        old, recent = rules.split_history(msgs, 0)
        self.assertEqual([m["id"] for m in old], list(range(1, 7)))
        self.assertEqual(len(recent), rules.RECENT_MESSAGES)
        self.assertEqual(rules.split_history(msgs, 6), ([], msgs[6:]), "les messages déjà résumés ne sont plus envoyés")

    def test_chart_and_results(self):
        c = rules.chart("Notes", [{"label": "1 nov.", "value": 10}, {"label": "8 nov.", "value": 20}])
        self.assertEqual(len(c["points"]), 2)
        self.assertLess(c["points"][1]["y"], c["points"][0]["y"], "20/20 est plus haut que 10/20")
        self.assertEqual(c["points"][1]["text"], "20 / 20")
        text, spec, sources = rules.split_result({"titre": "x", "chart": c})
        self.assertEqual((spec, sources), (c, []))
        self.assertNotIn('"points"', text, "le graphique n'est pas renvoyé au modèle")
        self.assertEqual(rules.parse_arguments('{"matiere": "proba"}'), {"matiere": "proba"})
        self.assertEqual(rules.parse_arguments("pas du json"), {})
        self.assertEqual(rules.title_from("  Qu'est-ce que   je fais aujourd'hui ?  "), "Qu'est-ce que je fais aujourd'hui ?")


class EnvFileTests(unittest.TestCase):
    def test_env_file_is_read_without_overriding_the_terminal(self):
        from backend.config import load_env
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# commentaire\nRIVEZLI_TEST_A = gsk_abc\nRIVEZLI_TEST_B=\"sk-ant-x\"\n"
                        "RIVEZLI_TEST_EMPTY=\nRIVEZLI_TEST_KEEP=fichier\n")
            with mock.patch.dict(os.environ, {"RIVEZLI_TEST_KEEP": "terminal"}):
                load_env(path)
                self.assertEqual(os.environ["RIVEZLI_TEST_A"], "gsk_abc")
                self.assertEqual(os.environ["RIVEZLI_TEST_B"], "sk-ant-x")
                self.assertNotIn("RIVEZLI_TEST_EMPTY", os.environ)
                self.assertEqual(os.environ["RIVEZLI_TEST_KEEP"], "terminal")
            load_env(os.path.join(tmp, "absent.env"))  # pas de fichier : rien ne se passe


class AssistantAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False, "ASSISTANT_AI": True, "ASSISTANT_PROVIDER": "groq",
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

    def login_as(self, email):
        self.client.post("/deconnexion")
        self.client.post("/inscription", data={"email": email, "username": email.split("@")[0], "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})
        self.client.post("/connexion", data={"email": email, "password": "motdepasse1"})

    def ask(self, fake, text, **extra):
        with mock.patch("backend.dao.llm.chat", fake):
            return self.client.post("/assistant/messages", json={"text": text, **extra})

    def test_today_uses_the_planning_tool(self):
        self.login_as("lea@test.fr")
        exam = (date.today() + timedelta(days=10)).isoformat()
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "exam", "exam_date": exam,
                                                     "comfort": "2", "topics": "Lois discrètes\nVecteurs gaussiens"})
        fake = FakeGroq({"content": "", "tool_calls": [call("programme_du_jour")]},
                        "Aujourd'hui : Lois discrètes en Probabilités.")
        r = self.ask(fake, "Qu'est-ce que je fais aujourd'hui ?")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["message"]["content"], "Aujourd'hui : Lois discrètes en Probabilités.")
        # le modèle a reçu les outils, puis le résultat réel de l'outil
        self.assertIn("programme_du_jour", {t["function"]["name"] for t in fake.calls[0]["tools"]})
        tool_msg = next(m for m in fake.calls[1]["messages"] if m["role"] == "tool")
        result = json.loads(tool_msg["content"])
        self.assertEqual(result["examens_a_venir"][0]["matiere"], "Probabilités")
        self.assertEqual(result["examens_a_venir"][0]["dans_jours"], 10)
        self.assertIn("Vecteurs gaussiens", result["examens_a_venir"][0]["chapitres_pas_encore_etudies"])
        self.assertIn("Aujourd'hui :", fake.calls[0]["messages"][0]["content"], "date du jour dans les consignes")
        # la conversation est enregistrée et rechargée
        msgs = self.client.get("/assistant/conversation").get_json()["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])

    def test_history_is_sent_back_and_summarized(self):
        self.login_as("lea@test.fr")
        fake = FakeGroq()
        self.ask(fake, "Je révise les probabilités.")
        self.ask(fake, "Et la deuxième partie ?")
        sent = [m["content"] for m in fake.calls[-1]["messages"] if m["role"] in ("user", "assistant")]
        self.assertEqual(sent, ["Je révise les probabilités.", "D'accord.", "Et la deuxième partie ?"],
                         "la question précédente fait partie du contexte")
        for i in range(6):
            self.ask(fake, f"Question {i}")
        self.assertTrue(any(c["light"] for c in fake.calls), "les anciens messages sont résumés (modèle léger)")
        conv = self.q("SELECT summary, summarized_upto FROM chat_conversations")[0]
        self.assertIn("probabilités", conv["summary"])
        last = fake.calls[-1]["messages"]
        self.assertTrue(any(m["role"] == "system" and "Résumé" in m["content"] for m in last))
        self.assertLessEqual(len([m for m in last if m["role"] in ("user", "assistant")]), rules.RECENT_MESSAGES + 1)
        # nouvelle conversation : on repart de zéro
        self.client.post("/assistant/nouvelle")
        self.assertEqual(self.client.get("/assistant/conversation").get_json()["messages"], [])

    def test_grade_chart(self):
        self.login_as("lea@test.fr")
        old = (date.today() - timedelta(days=7)).isoformat()
        self.client.post("/dossier/notes", data={"course": "Économétrie", "value": "9", "day": old})
        self.client.post("/dossier/notes", data={"course": "Économétrie", "value": "14"})
        fake = FakeGroq({"content": "", "tool_calls": [call("graphique_notes", matiere="econometrie")]},
                        "Tu progresses : de 9 à 14.")
        msg = self.ask(fake, "Montre mes notes en économétrie").get_json()["message"]
        self.assertEqual(len(msg["charts"]), 1)
        self.assertEqual([p["value"] for p in msg["charts"][0]["points"]], [9, 14])
        reloaded = self.client.get("/assistant/conversation").get_json()["messages"][-1]
        self.assertEqual(reloaded["charts"], msg["charts"], "le graphique reste dans l'historique")

    def test_broken_tool_does_not_break_the_chat(self):
        self.login_as("lea@test.fr")
        fake = FakeGroq({"content": "", "tool_calls": [call("notes")]}, "Je n'ai pas accès à tes notes pour l'instant.")
        with mock.patch("backend.services.dossier_service.courses", side_effect=RuntimeError("panne")):
            r = self.ask(fake, "Mes notes ?")
        self.assertEqual(r.status_code, 200)
        tool_msg = next(m for m in fake.calls[1]["messages"] if m["role"] == "tool")
        self.assertIn("indisponible", tool_msg["content"])

    def test_quota_error_is_readable_and_nothing_is_saved(self):
        from backend.dao.llm import LLMError
        self.login_as("lea@test.fr")
        with mock.patch("backend.dao.llm.chat", side_effect=LLMError("Limite gratuite de Groq atteinte.")):
            r = self.client.post("/assistant/messages", json={"text": "Bonjour"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Limite gratuite", r.get_json()["error"])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_messages")[0]["n"], 0)

    def test_conversations_are_private(self):
        self.login_as("lea@test.fr")
        self.ask(FakeGroq("Réponse pour Léa."), "Mon secret de révision")
        self.login_as("tom@test.fr")
        self.assertEqual(self.client.get("/assistant/conversation").get_json()["messages"], [])
        self.ask(FakeGroq(), "Bonjour")
        self.assertNotIn("Mon secret", json.dumps(self.client.get("/assistant/conversation").get_json()))
        # supprimer le compte supprime ses conversations
        self.client.post("/deconnexion")
        self.client.post("/connexion", data={"email": "admin@test.fr", "password": "adminpass123"})
        lea = self.q("SELECT id FROM users WHERE email = 'lea@test.fr'")[0]["id"]
        self.client.post(f"/admin/utilisateurs/{lea}/supprimer")
        self.assertNotIn("Mon secret", json.dumps(self.q("SELECT content FROM chat_messages")))

    def send_file(self, fake, filename, data=b"contenu", text=""):
        import io
        with mock.patch("backend.dao.llm.chat", fake):
            return self.client.post("/assistant/messages", content_type="multipart/form-data",
                                    data={"text": text, "page": "", "file": (io.BytesIO(data), filename)})

    def test_photo_is_read_then_explained(self):
        self.app.config["VISION_AI"] = True
        self.login_as("lea@test.fr")
        reading = "Montrer que $X_t = \\phi X_{t-1} + \\varepsilon_t$ est stationnaire si $|\\phi| < 1$.\nMatière probable : Séries temporelles"
        fake = FakeGroq("On utilise la racine du polynôme $1 - \\phi z$…", "Ensuite on calcule la variance.")
        with mock.patch("backend.dao.groq.api_key", return_value="gsk_test"), \
                mock.patch("backend.dao.vision.ask", return_value=reading) as ask:
            r = self.send_file(fake, "exo.jpg", b"\xff\xd8\xff photo")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(ask.call_args.args[0], b"\xff\xd8\xff photo", "la photo est lue par le modèle de vision")
        question = fake.calls[0]["messages"][-1]["content"]
        self.assertTrue(question.startswith("Explique-moi ce document."), "question par défaut sans texte")
        self.assertIn("[Pièce jointe : photo « exo.jpg »]", question)
        self.assertIn("|\\phi| < 1", question, "le contenu lu de la photo est donné à l'assistant")
        self.assertEqual(r.get_json()["question"]["attachment"], {"name": "exo.jpg", "kind": "image"})
        # la question suivante garde un extrait du document dans l'historique
        self.ask(fake, "Et la variance ?")
        history = [m["content"] for m in fake.calls[-1]["messages"] if m["role"] == "user"]
        self.assertIn("|\\phi| < 1", history[0])
        msgs = self.client.get("/assistant/conversation").get_json()["messages"]
        self.assertEqual(msgs[0]["attachment"], {"name": "exo.jpg", "kind": "image"})
        self.assertNotIn("excerpt", json.dumps(msgs), "l'extrait n'est pas renvoyé à la page")

    def test_pdf_text_is_extracted(self):
        self.login_as("lea@test.fr")
        fake = FakeGroq("Le modèle AR(p) s'écrit $$X_t = \\sum_{i=1}^p \\phi_i X_{t-i} + \\varepsilon_t$$")
        text = "Chapitre 3. Processus autorégressifs. " * 400
        with mock.patch("backend.business.extractor.extract", return_value=(text, [])):
            r = self.send_file(fake, "cours.pdf", b"%PDF-1.4 ...", text="Résume ce chapitre")
        self.assertEqual(r.status_code, 200, r.get_json())
        question = fake.calls[0]["messages"][-1]["content"]
        self.assertIn("[Pièce jointe : PDF « cours.pdf » (extrait du début)]", question)
        self.assertLess(len(question), rules.MAX_ATTACHMENT_CHARS + 300, "PDF tronqué pour tenir dans le quota")
        # PDF scanné (sans texte) : message clair, rien n'est envoyé
        with mock.patch("backend.business.extractor.extract", return_value=("", [])):
            r = self.send_file(FakeGroq(), "scan.pdf", b"%PDF-1.4")
        self.assertEqual(r.status_code, 422)
        self.assertIn("photo des pages", r.get_json()["error"])

    def test_history_is_erased_at_logout(self):
        self.login_as("lea@test.fr")
        self.ask(FakeGroq(), "Je révise les séries temporelles")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_messages")[0]["n"], 2)
        self.client.post("/deconnexion")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_conversations")[0]["n"], 0, "effacé à la déconnexion")
        self.client.post("/connexion", data={"email": "lea@test.fr", "password": "motdepasse1"})
        self.assertEqual(self.client.get("/assistant/conversation").get_json()["messages"], [])

    def test_history_is_erased_when_the_browser_was_closed(self):
        self.login_as("lea@test.fr")
        self.ask(FakeGroq(), "Je révise les séries temporelles")
        self.assertEqual(len(self.client.get("/assistant/conversation").get_json()["messages"]), 2,
                         "même session : l'historique reste d'une page à l'autre")
        # navigateur fermé : le cookie de session disparaît, l'étudiant se reconnecte
        browser = self.app.test_client()
        browser.post("/connexion", data={"email": "lea@test.fr", "password": "motdepasse1"})
        self.assertEqual(browser.get("/assistant/conversation").get_json()["messages"], [])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_messages")[0]["n"], 0, "ancienne session effacée en base")

    def test_second_pdf_starts_a_fresh_conversation(self):
        self.login_as("lea@test.fr")
        fake = FakeGroq("Résumé du premier cours.", "Précision.", "Résumé du second cours.")
        with mock.patch("backend.business.extractor.extract", return_value=("Cours 1 : processus ARMA et stationnarité. " * 5, [])):
            self.send_file(fake, "cours1.pdf", b"%PDF-1.4")
        self.ask(fake, "Et la stationnarité ?")
        with mock.patch("backend.business.extractor.extract", return_value=("Cours 2 : modèles GARCH et volatilité. " * 5, [])):
            r = self.send_file(fake, "cours2.pdf", b"%PDF-1.4")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["reset"])
        sent = json.dumps(fake.calls[-1]["messages"], ensure_ascii=False)
        self.assertIn("GARCH", sent, "le second PDF est bien lu")
        self.assertNotIn("ARMA", sent, "le premier PDF et la conversation précédente sont oubliés")
        msgs = self.client.get("/assistant/conversation").get_json()["messages"]
        self.assertEqual([m["attachment"] and m["attachment"]["name"] for m in msgs], ["cours2.pdf", None])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_conversations")[0]["n"], 1)

    def test_attachment_survives_the_server_closing_uploads(self):
        """Le serveur ferme les fichiers envoyés dès que la route a répondu, avant la diffusion de la réponse."""
        import io
        from werkzeug.datastructures import FileStorage
        from backend.core.tools import params, text_param
        from backend.services import assistant_service
        original = FileStorage(stream=io.BytesIO(b"%PDF-1.4 contenu"), filename="cours.pdf", content_type="application/pdf")
        with self.app.test_request_context():
            copy = assistant_service.prepare_stream(original)
            assistant_service.prepare_stream(None)
        original.stream.close()   # ce que fait Werkzeug
        self.assertEqual(copy.read(), b"%PDF-1.4 contenu")
        self.assertEqual((copy.filename, copy.mimetype), ("cours.pdf", "application/pdf"))
        schema = params(("question",), question=text_param("q"), matiere=text_param("m"))
        self.assertEqual(schema["properties"]["matiere"]["type"], ["string", "null"], "paramètre facultatif : null accepté")
        self.assertEqual(schema["properties"]["question"]["type"], "string")
        # « matiere": null » envoyé par le modèle : ignoré, la recherche se fait sans matière
        self.login_as("lea@test.fr")
        fake = FakeGroq({"content": "", "tool_calls": [call("notes", matiere=None)]}, "Pas encore de note.")
        r = self.ask(fake, "Mes notes ?")
        tool_msg = next(m for m in fake.calls[1]["messages"] if m["role"] == "tool")
        self.assertIn("moyenne_generale_sur_20", tool_msg["content"], "null traité comme « non renseigné »")
        self.assertEqual(r.status_code, 200)

    def test_unsupported_file_is_refused(self):
        self.login_as("lea@test.fr")
        fake = FakeGroq()
        r = self.send_file(fake, "virus.exe", b"MZ")
        self.assertEqual(r.status_code, 422)
        self.assertIn("Formats acceptés", r.get_json()["error"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_messages")[0]["n"], 0)

    def test_history_is_erased_when_the_server_restarts_or_after_inactivity(self):
        self.login_as("lea@test.fr")
        self.ask(FakeGroq(), "Je révise les séries temporelles")
        # application fermée puis relancée (même base) : l'historique de la session précédente disparaît
        create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                    "DATABASE": self.app.config["DATABASE"], "UPLOAD_FOLDER": self.app.config["UPLOAD_FOLDER"]})
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM chat_conversations")[0]["n"], 0)
        # même cookie de session (navigateur qui le restaure), mais conversation inactive depuis plus de 2 h
        self.ask(FakeGroq(), "Nouvelle question")
        with self.app.app_context():
            get_db().execute("UPDATE chat_conversations SET updated_at = datetime('now', 'localtime', '-3 hours')")
            get_db().commit()
        self.assertEqual(self.client.get("/assistant/conversation").get_json()["messages"], [])

    def test_groq_rate_limit_waits_then_answers(self):
        from backend.dao.llm import RateLimited
        self.login_as("lea@test.fr")
        replies = [RateLimited("limite", 0.01), {"content": "Réponse après attente.", "tool_calls": []}]
        budgets = []

        def fake(messages, tools=None, light=False, max_tokens=1500):
            budgets.append(max_tokens)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with mock.patch("backend.dao.llm.chat", fake):   # réponse diffusée : lue pendant la simulation
            r = self.client.post("/assistant/messages/flux", json={"text": "Bonjour"})
            events = [json.loads(c[6:]) for c in r.get_data(as_text=True).split("\n\n") if c.startswith("data: ")]
        self.assertIn("Limite gratuite de Groq", events[0]["text"], "l'attente est annoncée")
        self.assertEqual(events[-1]["message"]["content"], "Réponse après attente.")
        from backend.dao import llm
        self.assertTrue(all(llm.GROQ_MIN_ANSWER <= b <= llm.GROQ_MAX_ANSWER for b in budgets))
        long_prompt = [{"role": "user", "content": "x" * 24000}]
        self.assertEqual(llm.groq_answer_budget(long_prompt, None), llm.GROQ_MIN_ANSWER,
                         "question longue : réponse demandée plus courte pour tenir dans la limite par minute")

    def test_claude_streams_the_answer_and_uses_tools(self):
        from types import SimpleNamespace as NS
        self.app.config["ASSISTANT_PROVIDER"] = "anthropic"
        self.login_as("lea@test.fr")
        calls = []
        rounds = [
            NS(stop_reason="tool_use", deltas=[],
               content=[NS(type="tool_use", id="tu_1", name="programme_du_jour", input={})]),
            NS(stop_reason="end_turn", deltas=["Rien de prévu ", "aujourd'hui, **Léa**."],
               content=[NS(type="text", text="Rien de prévu aujourd'hui, **Léa**.")]),
        ]

        class Stream:
            def __init__(self, kwargs):
                calls.append(json.loads(json.dumps(kwargs, default=lambda o: getattr(o, "__dict__", str(o)))))
                self.msg = rounds.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter([NS(type="text", text=t) for t in self.msg.deltas])

            def get_final_message(self):
                return self.msg

        client = NS(beta=NS(messages=NS(stream=lambda **kw: Stream(kw))))
        with mock.patch("backend.dao.llm.anthropic_key", return_value="sk-ant-test"), \
                mock.patch("backend.dao.llm._client", return_value=client):
            r = self.client.post("/assistant/messages/flux", json={"text": "Qu'est-ce que je fais aujourd'hui ?"})
            events = [json.loads(line[6:]) for line in r.get_data(as_text=True).split("\n\n") if line.startswith("data: ")]
        self.assertEqual(r.mimetype, "text/event-stream")
        self.assertEqual([e["type"] for e in events], ["status", "delta", "delta", "done"])
        self.assertEqual(events[0]["text"], "Je regarde ton planning…")
        self.assertEqual(events[-1]["message"]["content"], "Rien de prévu aujourd'hui, **Léa**.")
        first, second = calls
        self.assertEqual(first["model"], "claude-opus-5-5")
        self.assertEqual(first["output_config"], {"effort": "low"})
        self.assertEqual(first["fallbacks"], "default")
        self.assertEqual(first["system"][0]["cache_control"], {"type": "ephemeral"}, "consignes mises en cache")
        self.assertIn("programme_du_jour", {t["name"] for t in first["tools"]})
        self.assertIn("input_schema", first["tools"][0])
        result = second["messages"][-1]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", "tu_1"))
        self.assertIn("aucune_seance_prevue", result["content"], "le résultat réel de l'outil est renvoyé à Claude")
        self.assertEqual(len(self.client.get("/assistant/conversation").get_json()["messages"]), 2)

    def test_claude_reads_photos_itself_and_flags_cut_answers(self):
        from types import SimpleNamespace as NS
        self.app.config["ASSISTANT_PROVIDER"] = "anthropic"
        self.login_as("lea@test.fr")
        sent = []

        class Stream:
            def __init__(self, kwargs):
                sent.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter([])

            def get_final_message(self):
                return NS(stop_reason="max_tokens", content=[NS(type="text", text="Début de l'explication")])

        client = NS(beta=NS(messages=NS(stream=lambda **kw: Stream(kw))))
        with mock.patch("backend.dao.llm.anthropic_key", return_value="sk-ant-test"), \
                mock.patch("backend.dao.llm._client", return_value=client), \
                mock.patch("backend.dao.vision.shrink", return_value=(b"jpeg", "image/jpeg")), \
                mock.patch("backend.dao.vision.ask") as groq_vision:
            r = self.send_file(FakeGroq(), "exo.jpg", b"\xff\xd8\xff photo")
        self.assertEqual(r.status_code, 200, r.get_json())
        groq_vision.assert_not_called()
        blocks = sent[0]["messages"][-1]["content"]
        self.assertEqual(blocks[0]["type"], "image", "la photo est envoyée telle quelle à Claude")
        self.assertEqual(blocks[0]["source"]["media_type"], "image/jpeg")
        self.assertIn("Réponse coupée", r.get_json()["message"]["content"], "une réponse coupée est signalée")

    def test_not_configured_and_bubble(self):
        self.assertEqual(self.client.get("/assistant/conversation").status_code, 302, "compte obligatoire")
        self.assertNotIn('id="assistant"', self.client.get("/").get_data(as_text=True))
        self.login_as("lea@test.fr")
        for url in ["/", "/tableau-de-bord", "/dossier/"]:
            self.assertIn('id="assistant"', self.client.get(url).get_data(as_text=True), url)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('accept="image/*,.heic,.heif,.pdf,application/pdf"', page, "joindre une photo ou un PDF")
        self.assertIn("KaTeX", page, "formules rendues par KaTeX")
        self.assertIn("marked/", page, "Markdown rendu par marked")
        self.assertIn("dompurify/", page, "HTML nettoyé par DOMPurify avant affichage")
        self.assertIn("$$", rules.RULES, "l'assistant écrit ses formules en LaTeX")
        with mock.patch("backend.dao.llm.groq_key", return_value=""):
            data = self.client.get("/assistant/conversation").get_json()
            self.assertFalse(data["available"])
            self.assertEqual(data["notice"], "", "rien d'alarmant à l'ouverture : l'accueil invite à poser sa question")
            reply = self.client.post("/assistant/messages", json={"text": "Salut"})
            self.assertEqual(reply.status_code, 422)
            self.assertIn("ANTHROPIC_API_KEY=", reply.get_data(as_text=True), "le guide de configuration n'apparaît qu'à l'envoi")
            self.assertIn("fichier .env", reply.get_data(as_text=True))
    def test_welcome_greets_the_student_and_invites_a_question(self):
        self.login_as("lea@test.fr")
        page = self.client.get("/").get_data(as_text=True)
        welcome = page[page.index('class="assistant-welcome"'):page.index('class="assistant-file"')]
        self.assertIn("Salut", welcome, "accueil personnalisé")
        self.assertIn("question", welcome, "le chatbot invite à poser sa question")
        self.assertIn('class="chip"', welcome, "des suggestions cliquables")
        self.assertNotIn("pas encore branché", page, "aucun avertissement sur la clé à l'ouverture")


if __name__ == "__main__":
    unittest.main()
