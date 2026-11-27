"""Atelier de révision : règles pures (fiches, QCM, notation, exercices) et parcours complet dans l'application."""
import io
import os
import re
import tempfile
import unittest
from unittest import mock

from backend import create_app
from backend.plugins.revision import generator as gen

COURSE = """Chapitre 1 : Variables aléatoires
On appelle variable aléatoire toute fonction définie sur l'univers à valeurs réelles.
Une variable aléatoire discrète est une variable qui prend un nombre fini ou dénombrable de valeurs.
La loi de probabilité est la donnée des probabilités de chaque valeur possible.
La fonction de répartition caractérise entièrement la loi de la variable.
Exercice 1. Calculer la loi de X lorsque l'on lance deux dés équilibrés et que X est la somme des faces.
Chapitre 2 : Espérance et variance
L'espérance est la moyenne des valeurs pondérée par leurs probabilités.
La variance mesure la dispersion des valeurs autour de l'espérance.
L'écart-type est la racine carrée de la variance.
Théorème (König-Huygens) : la variance est égale à l'espérance du carré moins le carré de l'espérance.
V(X) = E(X²) - E(X)²
Exercice 2 : Montrer que la variance est positive et en déduire que E(X²) est supérieure à E(X)².
Chapitre 3 : Lois usuelles
La loi binomiale est la loi du nombre de succès dans une répétition d'épreuves de Bernoulli indépendantes.
La loi de Poisson est une loi discrète qui modélise le nombre d'événements rares dans un intervalle.
La loi normale est une loi continue dont la densité a une forme de cloche.
Correction de l'exercice 1
La somme vaut entre 2 et 12 avec probabilité (6-|k-7|)/36.
"""
TOPICS = ["Variables aléatoires", "Espérance et variance", "Lois usuelles"]


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.chapters = gen.chapters_of(COURSE, TOPICS)

    def test_course_is_split_by_chapter(self):
        self.assertEqual(list(self.chapters), TOPICS)
        self.assertIn("variance", self.chapters["Espérance et variance"])
        self.assertNotIn("binomiale", self.chapters["Espérance et variance"])
        self.assertNotIn("deux dés", "".join(self.chapters.values()), "les exercices restent hors des fiches")
        self.assertNotIn("Correction", "".join(self.chapters.values()))
        self.assertEqual(list(gen.chapters_of(COURSE, TOPICS + ["V(X) = E(X²) - E(X)²"])), TOPICS,
                         "une formule prise pour un titre n'est pas un chapitre")

    def test_sheets_contain_definitions_theorems_and_formulas(self):
        sheets = {s["chapter"]: s for s in gen.build_sheets(self.chapters, lambda ch: "moyen")}
        terms = [d["term"].lower() for d in sheets["Lois usuelles"]["definitions"]]
        self.assertIn("loi binomiale", terms)
        esp = sheets["Espérance et variance"]
        self.assertEqual(esp["properties"][0]["term"], "König-Huygens")
        self.assertIn("V(X) = E(X²) - E(X)²", esp["formulas"])
        self.assertFalse(esp["recall"])
        self.assertTrue(gen.build_sheet("x", COURSE, "difficile")["recall"], "fiches à retourner en difficile")

    def test_quiz_follows_difficulty(self):
        for difficulty, cfg in gen.SETTINGS.items():
            quiz = gen.build_quiz(self.chapters, difficulty, seed=1)
            self.assertGreaterEqual(len(quiz), 6, difficulty)
            self.assertLessEqual(len(quiz), cfg["questions"])
            for q in quiz:
                self.assertTrue(0 <= q["answer"] < len(q["options"]))
                self.assertEqual(len(set(o.lower() for o in q["options"])), len(q["options"]), q)
                if q["type"] in ("definition", "cloze"):
                    self.assertEqual(len(q["options"]), cfg["options"])
                    self.assertIn(gen.BLANK, q["quote"])

    def test_quiz_on_one_chapter(self):
        quiz = gen.build_quiz(self.chapters, "moyen", seed=3, only="Lois usuelles")
        self.assertTrue(quiz)
        self.assertEqual({q["chapter"] for q in quiz}, {"Lois usuelles"})

    def test_grading_out_of_20_with_penalty_in_hard_mode(self):
        qs = [{"answer": 0, "chapter": "A", "options": ["a", "b"]} for _ in range(4)]
        self.assertEqual(gen.grade(qs, [0, 0, 0, 0], "moyen")["score"], 20)
        self.assertEqual(gen.grade(qs, [0, 0, 1, None], "moyen")["score"], 10)
        self.assertEqual(gen.grade(qs, [0, 0, 1, None], "difficile")["score"], 8.75)  # (2 - 0,25) / 4 × 20
        self.assertEqual(gen.grade(qs, [1, 1, 1, 1], "difficile")["score"], 0, "jamais sous 0")

    def test_level_advice(self):
        self.assertEqual(gen.trend([12, 17, 18, 16]), "up")
        self.assertEqual(gen.trend([5, 7, 8]), "down")
        self.assertIsNone(gen.trend([17, 18]))
        self.assertEqual(gen.harder("moyen", 1), "difficile")
        self.assertIsNone(gen.harder("difficile", 1))

    def test_exercises_and_their_solution(self):
        ex = gen.extract_exercises(COURSE)
        self.assertEqual([e["number"] for e in ex], ["1", "2"])
        self.assertIn("deux dés", ex[0]["statement"])
        self.assertIn("(6-|k-7|)/36", ex[0]["solution"])
        self.assertEqual(gen.estimate_difficulty(ex[0]["statement"]), "facile")
        self.assertEqual(gen.estimate_difficulty(ex[1]["statement"]), "difficile")
        self.assertEqual(gen.assign_chapter(ex[1]["statement"], TOPICS), "Espérance et variance")


class RevisionPagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.client = self.app.test_client()
        self.client.post("/inscription", data={"email": "e@test.fr", "username": "Léa", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "licence", "school": "ENSAI"})
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "2", "topics": "\n".join(TOPICS)})
        self.client.post("/matieres/1/documents",
                         data={"documents": (io.BytesIO(COURSE.encode("utf-8")), "cours.txt")},
                         content_type="multipart/form-data")

    def tearDown(self):
        self.tmp.cleanup()

    def page(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.get_data(as_text=True)

    def test_full_journey(self):
        topbar = lambda html: html.split('<header class="topbar">')[1].split("</header>")[0]
        self.assertIn(">Réviser</a>", topbar(self.page("/tableau-de-bord")), "onglet de la barre du haut")
        self.assertIn(">Réviser</a>", topbar(self.page("/")), "visible depuis la page de garde")
        revise = self.page("/reviser")
        self.assertRegex(topbar(revise), r'aria-current="page">Réviser</a>', "onglet surligné dans l'atelier")
        self.assertNotIn('class="subbar"', revise, "pas de seconde barre vide")
        self.assertIn("Choisir mon niveau", self.page("/matieres/1"), "panneau sur la page de la matière")
        self.assertIn("Choisir mon niveau", self.page("/reviser"))

        # Le niveau est demandé avant tout : fiches et QCM renvoient vers la question
        hub = self.page("/reviser/1")
        self.assertIn("Quel niveau de difficulté pour Probabilités ?", hub)
        self.assertIn('value="facile" required checked', hub, "proposition d'après l'aisance (2 = difficile)")
        self.assertIn("/reviser/1#niveau", self.client.get("/reviser/1/fiches").headers["Location"])
        self.assertIn("/reviser/1#niveau", self.client.post("/reviser/1/qcm", data={"difficulte": "moyen"}).headers["Location"])

        # Niveau de la matière + un chapitre réglé à part
        self.client.post("/reviser/1/niveau", data={"difficulte": "moyen", "chap_2": "difficile"})
        hub = self.page("/reviser/1")
        self.assertIn("Difficulté : Moyen", hub)
        self.assertIn('data-level="difficile">Lois usuelles', hub)

        sheets = self.page("/reviser/1/fiches")
        self.assertIn("König-Huygens", sheets)
        self.assertIn("rv-card-flip", self.page("/reviser/1/fiches?chapitre=Lois%20usuelles"),
                      "chapitre en difficile : cartes à retourner")

        # QCM : création, copie, note, carnet de notes
        r = self.client.post("/reviser/1/qcm", data={"chapitre": "", "difficulte": "moyen"})
        quiz_url = r.headers["Location"]
        self.assertIn("/reviser/qcm/", quiz_url)
        quiz = self.page(quiz_url)
        n = len(set(re.findall(r'name="q(\d+)"', quiz)))
        self.assertGreaterEqual(n, 6)
        self.client.post(quiz_url, data={f"q{i}": "0" for i in range(n)})
        done = self.page(quiz_url)
        self.assertIn("/20", done)
        self.assertIn("Bonne réponse", done)
        hub = self.page("/reviser/1")
        self.assertIn("Carnet de notes (1)", hub)
        self.assertIn("rv-chart", hub)
        self.assertIn("Réussite par chapitre", hub)
        # une copie rendue ne peut pas être rendue à nouveau
        self.client.post(quiz_url, data={f"q{i}": "1" for i in range(n)})
        self.assertIn("Carnet de notes (1)", self.page("/reviser/1"))

        # Exercices tirés des supports (internet désactivé dans les tests)
        self.client.post("/reviser/1/exercices", data={"chapitre": "", "difficulte": "moyen"})
        hub = self.page("/reviser/1")  # tous les exercices sont affichés par défaut
        self.assertIn("Exercices (2)", hub)
        self.assertIn("deux dés", hub)
        self.assertIn("Voir le corrigé", hub)

        # Suppression de la matière : tout part en cascade
        self.client.post("/matieres/1/supprimer")
        self.assertEqual(self.client.get("/reviser/1").status_code, 404)

    def test_other_users_data_is_private(self):
        self.client.post("/reviser/1/niveau", data={"difficulte": "facile"})
        quiz_url = self.client.post("/reviser/1/qcm", data={"difficulte": "facile"}).headers["Location"]
        self.client.post("/deconnexion")
        self.client.post("/inscription", data={"email": "f@test.fr", "username": "Tom", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "licence", "school": "ENSAI"})
        self.assertEqual(self.client.get("/reviser/1").status_code, 404)
        self.assertEqual(self.client.get(quiz_url).status_code, 404)


class RevisionWebTests(unittest.TestCase):
    """Sources externes simulées (aucun accès réseau pendant les tests)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": True,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.client = self.app.test_client()
        from backend.services import resource_service
        with mock.patch.object(resource_service, "refresh_async", return_value=False):
            self.client.post("/inscription", data={"email": "e@test.fr", "username": "Léa", "password": "motdepasse1",
                                                   "password2": "motdepasse1", "level": "licence", "school": "ENSAI"})
            self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                         "comfort": "3", "topics": "\n".join(TOPICS)})
        self.client.post("/reviser/1/niveau", data={"difficulte": "moyen"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_wikipedia_when_no_course_material(self):
        from backend.plugins.revision import sources
        with mock.patch.object(sources, "wikipedia_text", return_value=COURSE) as wiki:
            self.assertIn("s'appuient sur Wikipédia", self.client.get("/reviser/1").get_data(as_text=True))
            self.assertIn("loi binomiale", self.client.get("/reviser/1/fiches").get_data(as_text=True).lower())
            self.assertEqual(wiki.call_count, 1, "texte gardé en cache")

    def test_exercises_from_online_pdfs(self):
        from backend.plugins.revision import sources
        found = [{"url": "https://exemple.fr/td-proba.pdf", "title": "TD", "snippet": "", "source": "exemple.fr"}]
        with mock.patch.object(sources, "wikipedia_text", return_value=""), \
                mock.patch.object(sources, "search_exercise_pdfs", return_value=found) as search, \
                mock.patch.object(sources, "pdf_text", return_value=COURSE):
            r = self.client.post("/reviser/1/exercices", data={"chapitre": "Lois usuelles", "difficulte": "difficile"},
                                 follow_redirects=True)
        self.assertEqual(search.call_args.args[:3], ("Probabilités", "Lois usuelles", "difficile"))
        page = r.get_data(as_text=True)
        self.assertIn("2 nouvel(s) exercice(s) importé(s) depuis 1 PDF en ligne.", page)
        page = self.client.get("/reviser/1?exos=toutes").get_data(as_text=True)
        self.assertIn('href="https://exemple.fr/td-proba.pdf"', page)

    def test_downloads_refuse_private_addresses(self):
        from backend.plugins.revision import sources
        self.assertEqual(sources.pdf_text("http://127.0.0.1/secret.pdf"), "")
        self.assertEqual(sources.pdf_text("file:///etc/passwd"), "")


if __name__ == "__main__":
    unittest.main()
