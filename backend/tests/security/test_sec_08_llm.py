"""SEC-08 — Fonctions IA (constat F-09). Le LLM est un faux (FauxGroq) : aucune requête ne part.

1. Une réponse inexploitable (JSON malformé, mauvaise forme, champs de 1 Mo, nombre de questions ou de choix
   hors consigne) est rejetée : repli sur le générateur par règles, jamais de 500, rien de non échappé.
2. Quota : au plus 20 générations LLM par utilisateur et par jour (QCM, exercices, notebooks, lettres confondus).
"""
import io
import json
import unittest

from ._outils import CLE_FACTICE, AppSecuTestCase, FauxGroq

COURS = """# Variables aléatoires
On appelle variable aléatoire toute fonction définie sur l'univers à valeurs réelles.
Une variable aléatoire discrète est une variable qui prend un nombre fini ou dénombrable de valeurs.
La loi de probabilité est la donnée des probabilités de chaque valeur possible.
La fonction de répartition caractérise entièrement la loi de la variable.
# Espérance et variance
L'espérance est la moyenne des valeurs pondérée par leurs probabilités.
La variance mesure la dispersion des valeurs autour de l'espérance.
L'écart-type est la racine carrée de la variance.
Le théorème de König-Huygens donne la variance comme espérance du carré moins le carré de l'espérance.
# Lois usuelles
La loi binomiale est la loi du nombre de succès dans une répétition d'épreuves de Bernoulli indépendantes.
La loi de Poisson est une loi discrète qui modélise le nombre d'événements rares dans un intervalle.
La loi normale est une loi continue dont la densité a une forme de cloche.
"""
CHAPITRES = ["Variables aléatoires", "Espérance et variance", "Lois usuelles"]
MO = 1024 * 1024
MAX_CHAMP = 10_000  # bien au-delà d'un énoncé ou d'une explication normale


def question(i, **extra):
    q = {"chapter": "Lois usuelles", "prompt": f"Question {i} sur la loi normale ?", "quote": "",
         "options": [f"Bonne {i}", f"Faux {i}a", f"Faux {i}b", f"Faux {i}c"], "explanation": "Voir le cours."}
    q.update(extra)
    return q


class _AvecIa(AppSecuTestCase):
    config = {"REVISION_AI": True, "STAGES_AI": True, "GROQ_API_KEY": CLE_FACTICE, "PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "\n".join(CHAPITRES)})
        self.client.post("/matieres/1/documents", data={"documents": (io.BytesIO(COURS.encode()), "cours.md")},
                         content_type="multipart/form-data")
        self.client.post("/reviser/1/niveau", data={"difficulte": "moyen"})

    def qcm(self, contenu):
        """Crée un QCM avec un LLM qui répond `contenu`. Renvoie (réponse, questions stockées, appels au LLM)."""
        with FauxGroq(lambda body: contenu) as groq:
            r = self.client.post("/reviser/1/qcm", data={"chapitre": "", "difficulte": "moyen"})
        rows = self.q("SELECT questions FROM rev_quizzes WHERE user_id = ? ORDER BY id DESC LIMIT 1", self.uid)
        return r, (json.loads(rows[0]["questions"]) if rows else []), groq.appels


class ValidationReponseLlmTests(_AvecIa):
    def _verifier_repli(self, contenu, cas):
        r, questions, appels = self.qcm(contenu)
        self.assertTrue(appels, f"{cas} : le LLM n'a pas été appelé (le test ne prouve rien)")
        self.assertNotEqual(r.status_code, 500, f"{cas} : erreur serveur")
        self.assertTrue(questions, f"{cas} : aucun QCM créé (le repli sur le générateur n'a pas eu lieu)")
        for q in questions:
            for k, v in q.items():
                self.assertLessEqual(len(json.dumps(v, ensure_ascii=False)), MAX_CHAMP, f"{cas} : champ {k} accepté")
        return questions

    def test_json_malforme(self):
        self._verifier_repli('{"questions": [ {"prompt": "coupé', "JSON coupé")

    def test_json_de_mauvaise_forme(self):
        cas = {"questions texte": {"questions": "pas une liste"},
               "question sans options": {"questions": [{"prompt": "x"}] * 10},
               "options texte": {"questions": [question(i, options="abcd") for i in range(10)]},
               "types inattendus": {"questions": [question(i, prompt=None, options=[1, 2, 3, 4]) for i in range(10)]},
               "liste au lieu d'objet": [1, 2, 3]}
        for nom, contenu in cas.items():
            with self.subTest(cas=nom):
                self._verifier_repli(json.dumps(contenu), nom)

    def test_champs_de_1_mo(self):
        geant = "x" * MO
        contenu = {"questions": [question(i, prompt=geant, explanation=geant) for i in range(10)]}
        self._verifier_repli(json.dumps(contenu), "champs de 1 Mo")

    def test_nombre_de_questions_et_de_choix_hors_consigne(self):
        """Niveau moyen : 10 questions à 4 choix. 60 questions à 9 choix ne doivent pas être prises telles quelles."""
        contenu = {"questions": [question(i, options=[f"Choix {i}-{k}" for k in range(9)]) for i in range(60)]}
        questions = self._verifier_repli(json.dumps(contenu), "60 questions à 9 choix")
        self.assertLessEqual(len(questions), 10)
        self.assertTrue(all(len(q["options"]) == 4 for q in questions),
                        f"nombres de choix : {sorted({len(q['options']) for q in questions})}")

    def test_html_du_llm_affiche_echappe(self):
        contenu = {"questions": [question(i, prompt=f"<script>alert({i})</script>?",
                                          options=["<img src=x onerror=alert(1)>", "b", "c", "d"]) for i in range(10)]}
        r, _, _ = self.qcm(json.dumps(contenu))
        page = self.client.get(r.headers.get("Location", "/reviser/qcm/1")).get_data(as_text=True)
        self.assertNotIn("<script>alert(", page)
        self.assertNotIn("<img src=x onerror", page)

    def test_exercices_et_notebook_de_mauvaise_forme(self):
        for url, contenu in (("/reviser/1/exercices", {"exercises": "texte"}),
                             ("/reviser/1/exercices", {"exercises": [{"title": "x"}]}),
                             ("/reviser/1/notebooks", {"title": "x", "intro": "", "setup_code": "",
                                                       "exercises": [{"title": "x"}]}),
                             ("/reviser/1/notebooks", {"title": "x", "intro": "", "setup_code": "",
                                                       "exercises": "texte"})):
            with self.subTest(route=url, contenu=json.dumps(contenu)[:60]):
                with FauxGroq(lambda body, c=contenu: json.dumps(c)) as groq:
                    r = self.client.post(url, data={"chapitre": "", "difficulte": "moyen"})
                self.assertTrue(groq.appels)
                self.assertNotEqual(r.status_code, 500, f"{url} : erreur serveur")

    def test_lettre_geante(self):
        with FauxGroq(lambda body: "Madame, Monsieur,\n" + "Je suis motivé. " * 70_000) as groq:
            r = self.client.post("/stages/lettres", data={"title": "Assistant", "company": "Cabinet"})
        self.assertTrue(groq.appels)
        self.assertNotEqual(r.status_code, 500)
        longueurs = [x["n"] for x in self.q("SELECT length(content) AS n FROM stg_letters")]
        self.assertTrue(longueurs)
        self.assertLessEqual(max(longueurs), 12_000, "lettre de 1 Mo enregistrée (l'édition limite à 12 000)")


class QuotaTests(_AvecIa):
    """F-09 : aucune limite de générations LLM par utilisateur (coût)."""

    def test_21e_generation_du_jour_refusee(self):
        bon_qcm = json.dumps({"questions": [question(i) for i in range(10)]})
        exercices = json.dumps({"exercises": [{"title": "Exo", "chapter": "Lois usuelles",
                                               "statement": "Énoncé complet.", "solution": "Corrigé."}]})

        def reponse(body):
            consigne = json.dumps(body.get("messages", []), ensure_ascii=False)
            if "QCM" in consigne:
                return bon_qcm
            if "exercices" in consigne:
                return exercices
            return "Madame, Monsieur,\n" + "Je suis motivé pour ce stage. " * 20

        statuts = []
        with FauxGroq(reponse) as groq:
            for i in range(21):
                if i % 3 == 0:
                    r = self.client.post("/reviser/1/qcm", data={"chapitre": "", "difficulte": "moyen"})
                elif i % 3 == 1:
                    r = self.client.post("/stages/lettres", data={"title": f"Poste {i}", "company": "Cabinet"})
                else:
                    r = self.client.post("/reviser/1/exercices", data={"chapitre": "", "difficulte": "moyen"})
                statuts.append(r.status_code)
        self.assertNotIn(500, statuts)
        self.assertLessEqual(len(groq.appels), 20, f"{len(groq.appels)} appels au LLM le même jour pour un utilisateur")

    def test_quota_propre_a_chaque_utilisateur(self):
        """Témoin : un autre utilisateur garde ses générations (le quota n'est pas global)."""
        bon_qcm = json.dumps({"questions": [question(i) for i in range(10)]})
        autre = self.app.test_client()
        self.inscrire("autre@test.fr", client=autre)
        autre.post("/matieres/nouvelle", data={"name": "Statistiques", "goal": "a_jour", "weekly_hours": "2",
                                               "comfort": "3", "topics": "\n".join(CHAPITRES)})
        autre.post("/matieres/2/documents", data={"documents": (io.BytesIO(COURS.encode()), "c.md")},
                   content_type="multipart/form-data")
        autre.post("/reviser/2/niveau", data={"difficulte": "moyen"})
        with FauxGroq(lambda body: bon_qcm) as groq:
            for _ in range(20):
                self.client.post("/reviser/1/qcm", data={"chapitre": "", "difficulte": "moyen"})
            avant = len(groq.appels)
            autre.post("/reviser/2/qcm", data={"chapitre": "", "difficulte": "moyen"})
        self.assertEqual(len(groq.appels), avant + 1, "le quota d'un utilisateur ne doit pas bloquer les autres")


if __name__ == "__main__":
    unittest.main()
