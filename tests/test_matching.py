import unittest

from app.matching import THRESHOLD, best_match, clean_title, similarity
from app.resources import level_score, rank_for_level, school_keyword


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


if __name__ == "__main__":
    unittest.main()
