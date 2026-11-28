"""RAG sur les supports de cours : découpage, recherche hybride, citations, confidentialité.

Les tests utilisent le repli par hachage (RAG_EMBEDDINGS désactivé en test) : pas de modèle à charger.
"""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from backend import create_app
from backend.business import assistant as rules
from backend.business import chunking, retrieval
from backend.dao.db import get_db

COURSE = """# Chapitre 1 Processus stationnaires
Un processus est faiblement stationnaire si son espérance est constante et si sa fonction d'autocovariance
ne dépend que de l'écart entre deux dates. Le bruit blanc est l'exemple le plus simple de processus stationnaire.

# Chapitre 2 Processus autorégressifs
Un processus AR(p) s'écrit X_t = phi_1 X_{t-1} + ... + phi_p X_{t-p} + epsilon_t. Il est stationnaire lorsque
les racines du polynôme caractéristique sont de module strictement supérieur à un. La fonction
d'autocorrélation partielle d'un AR(p) s'annule après le retard p, ce qui permet d'identifier l'ordre.

# Chapitre 3 Modèles GARCH
Les modèles GARCH décrivent une volatilité conditionnelle qui varie dans le temps, typique des rendements financiers.
"""


def call(name, **args):
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ChunkingTests(unittest.TestCase):
    def test_headings_pages_and_overlap(self):
        long_text = " ".join(f"Phrase numéro {i} sur la stationnarité." for i in range(120))
        pages = [(1, "CHAPITRE 1 STATIONNARITÉ\n" + long_text), (2, "Chapitre 2 Estimation\nLe maximum de vraisemblance.")]
        chunks = chunking.chunk_pages(pages)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(chunks[0].heading, "CHAPITRE 1 STATIONNARITÉ")
        self.assertEqual(chunks[-1].heading, "Chapitre 2 Estimation", "un titre commence un nouveau passage")
        self.assertEqual(chunks[-1].page, 2)
        self.assertTrue(all(len(c.text.split()) <= chunking.MAX_WORDS for c in chunks))
        first, second = chunks[0].text.split(), chunks[1].text.split()
        self.assertEqual(first[-chunking.OVERLAP_WORDS:], second[:chunking.OVERLAP_WORDS], "chevauchement de 30 mots")
        # pas de passage fait uniquement du chevauchement
        self.assertEqual(len({c.text for c in chunks}), len(chunks))

    def test_latex_pdf_cleanup(self):
        self.assertEqual(chunking.fix_accents("R´egression lin´eaire, mod`ele, probl`eme, gar¸con, ˆetre, c¸a, naˆıf"),
                         "Régression linéaire, modèle, problème, garçon, être, ça, naîf")
        footer = "F. Lavancier (ENSAI) Régression linéaire 12 / 300"
        slides = [("Moindres carrés", "La droite minimise la somme des carrés des résidus."),
                  ("Hypothèses", "Les erreurs sont centrées, de même variance et non corrélées."),
                  ("Coefficient de détermination", "Le R2 mesure la part de variance expliquée."),
                  ("Test de Fisher", "On compare un modèle et un sous-modèle emboîté."),
                  ("Sommaire", "Moindres carrés, hypothèses, tests."),
                  ("Sélection de variables", "Les critères AIC et BIC pénalisent les gros modèles."),
                  ("Sommaire", "Moindres carrés, hypothèses, tests.")]
        pages = [(i, f"{t}\n{body}\n{footer.replace('12', str(i))}") for i, (t, body) in enumerate(slides, 1)]
        chunks = chunking.chunk_pages(pages)
        self.assertTrue(all("Lavancier" not in c.text and "Lavancier" not in c.heading for c in chunks),
                        "pied de page répété retiré (et jamais pris pour un titre)")
        # diaporama : une diapositive = un passage, titré par sa première ligne
        self.assertEqual([(c.page, c.heading) for c in chunks][:2], [(1, "Moindres carrés"), (2, "Hypothèses")])
        self.assertEqual([c.heading for c in chunks].count("Sommaire"), 1, "diapositive de plan répétée : gardée une fois")
        self.assertFalse(chunking.is_heading("P U A T V"), "étiquettes de figure : pas un titre")
        self.assertFalse(chunking.is_heading("A1 A2 A3 A4 A5"))
        self.assertTrue(chunking.is_heading("MODÈLE LINÉAIRE GÉNÉRALISÉ"))

    def test_retrieval_rules(self):
        q = retrieval.fts_query("Comment estimer les paramètres d'un processus autorégressif ?")
        self.assertIn('"autoregress"*', q)
        self.assertNotIn('"les"', q, "mots vides retirés")
        self.assertEqual([i for i, _ in retrieval.rrf([[1, 2, 3], [3, 1]])][:2], [1, 3])
        rows = [{"document_id": 7, "position": p, "heading": h} for p, h in ((3, "A"), (4, "A"), (5, "B"))]
        used = set()
        self.assertEqual([p["position"] for p in retrieval.context(rows[1], rows, used)], [3, 4],
                         "voisins de la même section seulement")
        self.assertEqual([p["position"] for p in retrieval.context(rows[2], rows, used)], [5],
                         "le passage d'une autre section reste disponible")
        self.assertEqual(retrieval.context(rows[0], rows, used), [], "rien n'est répété")
        sources = [{"source": "S1"}, {"source": "S2"}]
        answer, cited = retrieval.filter_citations("Vrai [S2]. Inventé [S7].", sources)
        self.assertEqual(answer, "Vrai [S2]. Inventé .")
        self.assertEqual(cited, [{"source": "S2"}])
        self.assertEqual(retrieval.filter_citations("Sans citation.", sources)[1], sources)
        text, _, labelled = rules.split_result({"passages": [{"texte": "a"}], "sources": [{"url": "/d"}]}, 2)
        self.assertEqual(labelled[0]["source"], "S3", "numérotation à la suite des sources déjà données")
        self.assertIn('"source": "S3"', text)


class RagAppTests(unittest.TestCase):
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
        self.client.post("/inscription", data={"email": email, "username": email.split("@")[0], "password": "Carnet-Revision-2026",
                                               "password2": "Carnet-Revision-2026", "level": "master", "school": "ENSAI"})
        self.client.post("/connexion", data={"email": email, "password": "Carnet-Revision-2026"})

    def add_course(self, name="Séries temporelles", text=COURSE, filename="cours.md"):
        self.client.post("/matieres/nouvelle", data={"name": name, "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})
        sid = self.q("SELECT id FROM subjects WHERE name = ?", name)[0]["id"]
        self.client.post(f"/matieres/{sid}/documents", content_type="multipart/form-data",
                         data={"documents": (io.BytesIO(text.encode()), filename)})
        return sid

    def search(self, question, matiere=""):
        from backend.services import assistant_tools
        with self.app.test_request_context():
            from backend.dao import users
            user = users.get(self.q("SELECT id FROM users WHERE email = 'lea@test.fr'")[0]["id"])
            out = assistant_tools.chercher_dans_mes_cours(user, question=question, matiere=matiere)
            get_db().commit()
            return out

    def test_search_finds_the_right_passage_with_its_source(self):
        self.login_as("lea@test.fr")
        self.add_course()
        out = self.search("autocorrélation partielle d'un processus autorégressif", "series temporelles")
        self.assertTrue(out["passages"], out)
        top = out["passages"][0]
        self.assertIn("autocorrélation partielle", top["texte"])
        self.assertEqual((top["document"], top["matiere"]), ("cours.md", "Séries temporelles"))
        self.assertIn("Chapitre 2", top["section"])
        self.assertTrue(out["sources"][0]["url"].startswith("/documents/"))
        self.assertNotIn("document_id", top, "seuls les champs utiles sont envoyés au modèle")
        indexed = self.q("SELECT model, chunks FROM doc_index")
        self.assertEqual(indexed[0]["model"], "hash512")
        self.assertGreater(indexed[0]["chunks"], 0)

    def test_scope_ambiguity_and_privacy(self):
        self.login_as("lea@test.fr")
        self.add_course()
        self.add_course("Économétrie", "# Chapitre 1 Régression\nLe modèle autorégressif à retards échelonnés "
                        "en économétrie relie y_t à ses valeurs passées et à x_t.", "eco.md")
        out = self.search("modèle autorégressif valeurs passées")
        self.assertIn("attention", out, "passages de deux matières : l'assistant doit demander laquelle")
        only = self.search("modèle autorégressif valeurs passées", "économétrie")
        self.assertEqual({p["matiere"] for p in only["passages"]}, {"Économétrie"}, "recherche limitée à la matière")
        # Tom ne trouve rien des supports de Léa
        self.login_as("tom@test.fr")
        from backend.services import rag_service
        tom = self.q("SELECT id FROM users WHERE email = 'tom@test.fr'")[0]["id"]
        with self.app.test_request_context():
            self.assertEqual(rag_service.search(tom, "processus autorégressif")["passages"], [])

    def test_deleting_a_document_removes_its_passages(self):
        self.login_as("lea@test.fr")
        self.add_course()
        self.search("GARCH volatilité")
        doc = self.q("SELECT id FROM documents")[0]["id"]
        self.assertGreater(self.q("SELECT COUNT(*) AS n FROM doc_chunks")[0]["n"], 0)
        self.client.post(f"/documents/{doc}/supprimer")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM doc_chunks")[0]["n"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM doc_chunks_fts WHERE doc_chunks_fts MATCH 'GARCH'")[0]["n"], 0,
                         "index plein texte nettoyé aussi")

    def test_assistant_cites_sources_and_drops_invented_ones(self):
        self.login_as("lea@test.fr")
        self.add_course()
        replies = [{"content": "", "tool_calls": [call("chercher_dans_mes_cours",
                                                      question="Quand un AR(p) est-il stationnaire ?",
                                                      matiere="Séries temporelles")]},
                   {"content": "Il est stationnaire si les racines sont hors du disque unité [S1]. Voir aussi [S8].",
                    "tool_calls": []}]
        sent = []

        def fake(messages, tools=None, light=False, max_tokens=1500):
            sent.append(json.loads(json.dumps(messages)))
            return replies.pop(0)

        with mock.patch("backend.dao.llm.chat", fake):
            r = self.client.post("/assistant/messages", json={"text": "Quand un AR(p) est-il stationnaire ?"})
        msg = r.get_json()["message"]
        self.assertIn("[S1]", msg["content"])
        self.assertNotIn("[S8]", msg["content"], "citation inventée retirée")
        self.assertEqual([s["source"] for s in msg["sources"]], ["S1"])
        self.assertEqual(msg["sources"][0]["document"], "cours.md")
        tool_msg = next(m for m in sent[1] if m["role"] == "tool")
        self.assertIn("racines du polynôme", tool_msg["content"], "le modèle a reçu le passage du cours")
        reloaded = self.client.get("/assistant/conversation").get_json()["messages"][-1]
        self.assertEqual(reloaded["sources"], msg["sources"], "les sources restent dans l'historique")

    def test_no_documents(self):
        self.login_as("lea@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})
        out = self.search("loi normale")
        self.assertIn("aucun support", out["resultat"])


if __name__ == "__main__":
    unittest.main()
