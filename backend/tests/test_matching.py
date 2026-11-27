import unittest

from backend.business.matching import THRESHOLD, best_match, clean_title, similarity
from backend.business.resource_rules import level_score, rank_for_level, school_keyword


class MatchingTests(unittest.TestCase):
    SAME = [("Proba", "Probabilités"), ("CM Probabilités - Amphi B", "Probabilités"), ("Maths", "Mathématiques"),
            ("TD Droit des contrats B204", "Droit des contrats"), ("Stats", "Statistiques"),
            ("Biologie cell.", "Biologie cellulaire"), ("Machine Learning", "ML"), ("Probabilité", "Probabilités"),
            ("Économétrie", "econometrie")]
    DIFFERENT = [("Probabilités", "Statistiques"), ("Analyse 1", "Analyse 2"), ("Droit des contrats", "Droit pénal"),
                 ("Sport", "Probabilités"), ("Économie", "Économétrie"), ("Statistique", "Statique"),
                 ("Chimie organique", "Chimie analytique"), ("Physique", "Physique chimie")]

    def test_same_course(self):
        for a, b in self.SAME:
            self.assertGreaterEqual(similarity(a, b), THRESHOLD, (a, b))

    def test_different_courses(self):
        for a, b in self.DIFFERENT:
            self.assertLess(similarity(a, b), THRESHOLD, (a, b))

    def test_symmetric_and_bounded(self):
        for a, b in self.SAME + self.DIFFERENT:
            self.assertEqual(similarity(a, b), similarity(b, a))
            self.assertTrue(0 <= similarity(a, b) <= 1)

    def test_best_match(self):
        subjects = [{"name": "Probabilités"}, {"name": "Droit des contrats"}]
        match, score = best_match("TD proba", subjects)
        self.assertEqual(match["name"], "Probabilités")
        self.assertEqual(best_match("Sport", subjects)[0], None)

    def test_clean_title(self):
        self.assertEqual(clean_title("CM Probabilités - Amphi B"), "Probabilités")
        self.assertEqual(clean_title("TD Droit des contrats (groupe 2)"), "Droit des contrats")
        self.assertEqual(clean_title("Analyse 1 | B204"), "Analyse 1")


class RealTimetableTests(unittest.TestCase):
    """Intitulés réels d'un emploi du temps ENSAI (codes de cours, enseignants, salles)."""

    def test_course_codes_and_teachers_are_ignored(self):
        self.assertEqual(clean_title("Séries temporelles 1 2ASTA03 - Marie-Lorraine CHAU"), "Séries temporelles 1")
        self.assertEqual(clean_title("Régression linéaire et généralisée 2ASTA02 - Frédé"),
                         "Régression linéaire et généralisée")
        self.assertEqual(clean_title("Anglais 2AHUM01 -"), "Anglais")
        self.assertGreaterEqual(similarity("Séries temporelles 1 2ASTA03 - Marie-Lorraine CHAU", "Séries temporelles 1"),
                                THRESHOLD)
        self.assertGreaterEqual(similarity("Econométrie et inférence causale 2AECO02 - Arouna", "Économétrie et inférence causale"),
                                THRESHOLD)
        self.assertLess(similarity("Séries temporelles 1 2ASTA03 -", "Séries temporelles 2"), THRESHOLD)
        self.assertEqual(similarity("Présentation des filières de 3A M347 - Christophe", "Présentation des filières de 3A"), 1.0,
                         "la salle M347 n'est pas un numéro de cours")

    def test_non_course_events(self):
        from backend.business.agenda_analysis import is_course, is_holiday
        for t in ["Vacances scolaires - Holidays  -", "Férié - Bank holiday  -"]:
            self.assertTrue(is_holiday(t), t)
            self.assertFalse(is_course(t), t)
        for t in ["Présentation des filières de 3A M347 - Christophe", "Career sessions   -",
                  "Rencontre parrain de promotion 2A Ingénieurs  -"]:
            self.assertFalse(is_course(t), t)
        for t in ["Séries temporelles 1 2ASTA03 -", "Théorie de l'information", "Intégration numérique",
                  "Anglais 2AHUM01 - Amany MESSOUS", "Ponts thermiques"]:
            self.assertTrue(is_course(t), t)
            self.assertFalse(is_holiday(t), t)


class LevelRankingTests(unittest.TestCase):
    def test_level_score(self):
        self.assertGreater(level_score("Probabilités - cours de Master 1", "master"), 0)
        self.assertLess(level_score("LE COURS : Probabilités - Seconde", "master"), 0)
        self.assertEqual(level_score("Probabilités, le cours", "master"), 0)

    def test_school_keyword(self):
        self.assertEqual(school_keyword("Lycée Chateaubriand"), "chateaubriand")
        self.assertEqual(school_keyword("Université de Rennes"), "rennes")
        self.assertEqual(school_keyword("ENSAI"), "ensai")

    def test_rank(self):
        items = [{"title": "Probabilités 3e brevet"}, {"title": "Probabilités, introduction"},
                 {"title": "Probabilités L2 université"}]
        ranked = [i["title"] for i in rank_for_level(items, "licence")]
        self.assertEqual(ranked[0], "Probabilités L2 université")
        self.assertEqual(ranked[-1], "Probabilités 3e brevet")


class ResourceOrderTests(unittest.TestCase):
    def test_pdfs_come_before_wikipedia(self):
        from unittest import mock
        from backend.dao import web
        from backend.services import resource_service

        def fake_ddg(query, limit=5):
            if "filetype:pdf" in query and "ENSAI" in query:
                return [{"title": "PDF Poly proba M1 - Ensai", "url": "https://ensai.fr/poly.pdf", "snippet": "",
                         "source": "ensai.fr", "kind": "web"}]
            if "filetype:pdf" in query:
                return [{"title": "Cours de probabilités", "url": "https://univ.fr/proba.pdf", "snippet": "",
                         "source": "univ.fr", "kind": "web"}]
            return [{"title": "Page de cours", "url": "https://site.fr/proba", "snippet": "", "source": "site.fr",
                     "kind": "web"}]

        wiki = [{"title": "Wikipédia : Probabilité", "url": "https://fr.wikipedia.org/wiki/Probabilit%C3%A9",
                 "snippet": "", "source": "Wikipédia", "kind": "web"}]
        with mock.patch.object(web, "duckduckgo_search", side_effect=fake_ddg), \
                mock.patch.object(web, "wikipedia_search", return_value=wiki), \
                mock.patch.object(web, "youtube_search", return_value=[]):
            found, errors = resource_service.find_resources("Probabilités", ["Lois"], level="master", school="ENSAI")
        web = [r for r in found if r["kind"] == "web"]
        self.assertEqual(errors, [])
        self.assertEqual(web[0]["url"], "https://ensai.fr/poly.pdf", "le PDF de l'établissement d'abord")
        self.assertEqual(web[0]["title"], "Poly proba M1 - Ensai", "préfixe « PDF » retiré du titre")
        self.assertEqual(web[1]["url"], "https://univ.fr/proba.pdf")
        self.assertEqual(web[-1]["source"], "Wikipédia", "Wikipédia en dernier")
        self.assertLess([r["url"] for r in web].index("https://site.fr/proba"),
                        [r["url"] for r in web].index(wiki[0]["url"]))


class EmbeddingTests(unittest.TestCase):
    def test_unit_vectors_and_cosine(self):
        from backend.business.embeddings import DIM, cosine, embed
        v = embed("Probabilités")
        self.assertEqual(len(v), DIM)
        self.assertAlmostEqual(sum(x * x for x in v), 1.0, places=6)
        self.assertAlmostEqual(cosine(v, embed("probabilites")), 1.0, places=6)
        self.assertGreater(cosine(embed("prob"), v), cosine(embed("droit"), v))

    def test_rank_partial_typing(self):
        from backend.business.embeddings import rank
        courses = ["Probabilités", "Droit des contrats", "Statistique inférentielle", "Macroéconomie", "Anglais"]
        self.assertEqual(rank("prob", courses)[0][0], "Probabilités")
        self.assertEqual(rank("macro eco", courses)[0][0], "Macroéconomie")
        self.assertEqual(rank("stat", courses)[0][0], "Statistique inférentielle")
        self.assertEqual(rank("dro", courses)[0][0], "Droit des contrats")
        self.assertEqual(rank("contrat", courses)[0][0], "Droit des contrats")
        self.assertEqual(rank("zzzz", courses), [])


class ExamDetectionTests(unittest.TestCase):
    def test_is_exam_and_course_name(self):
        from backend.business.agenda_analysis import course_name, is_exam
        cases = {"Partiel Probabilités": "Probabilités", "DS de maths": "Maths",
                 "Examen final - Droit des contrats": "Droit des contrats", "Contrôle continu Anglais": "Anglais"}
        for title, name in cases.items():
            self.assertTrue(is_exam(title), title)
            self.assertEqual(course_name(title), name)
        self.assertFalse(is_exam("CM Probabilités - Amphi B"))
        self.assertFalse(is_exam("Cours de statistique"))

    def test_detect_exam_date(self):
        from datetime import date, timedelta
        from backend.business.agenda_analysis import detect_exam_date
        d1 = (date.today() + timedelta(days=20)).isoformat()
        d2 = (date.today() + timedelta(days=9)).isoformat()
        past = (date.today() - timedelta(days=3)).isoformat()
        events = [{"title": "Partiel Probabilités", "start": f"{d1}T09:00"},
                  {"title": "DS proba", "start": f"{d2}T14:00"},
                  {"title": "Examen Probabilités", "start": f"{past}T09:00"},
                  {"title": "Partiel Droit", "start": f"{d2}T09:00"}]
        found, ev = detect_exam_date("Probabilités", events)
        self.assertEqual(found.isoformat(), d2, "la première épreuve à venir")
        self.assertEqual(detect_exam_date("Anglais", events), (None, None))


class SchoolSearchTests(unittest.TestCase):
    def test_france_wide_list(self):
        from backend.services import suggestion_service
        self.assertGreater(suggestion_service.school_count(), 15000, "collèges, lycées et supérieur de toute la France")
        self.assertEqual(suggestion_service.schools("ensai")[0]["value"], "ENSAI")
        self.assertIn("Université de Rennes", [r["value"] for r in suggestion_service.schools("univ renn", "licence")])
        lycee = suggestion_service.schools("chateaubriand", "lycee")[0]
        self.assertEqual(lycee["kind"], "Lycée")
        self.assertIn("(", lycee["value"], "homonymes : la ville est ajoutée")
        self.assertEqual(suggestion_service.schools("a"), [], "au moins deux lettres")


if __name__ == "__main__":
    unittest.main()
