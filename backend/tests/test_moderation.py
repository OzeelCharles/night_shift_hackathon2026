"""business/moderation (SEC-05) : règles pures, sur la vraie liste embarquée. Les termes injurieux sont des données
de test, nécessaires pour vérifier le filtre."""
import unittest

from backend.business import moderation
from backend.dao import banned_words


class ModerationTests(unittest.TestCase):
    terms = banned_words.terms()

    def test_refused(self):
        for text in ("connards", "Sales cons", "ta gueule", "TA-GUEULE", "grosconnard", "n.a.z.i", "P|_|te",
                     "s4l0p3", "Encul3", "b i t e", "nazis", "FUCKING"):
            with self.subTest(text=text):
                self.assertTrue(moderation.contains_banned(text, self.terms))

    def test_accepted(self):
        for text in ("Concarneau", "salopettes", "Constantin", "Bitencourt", "Violette", "Culture G", "Tagore",
                     "Raconteurs", "Scunthorpe", "Analyste data", "Léa", "Jean-Baptiste Durand", ""):
            with self.subTest(text=text):
                self.assertFalse(moderation.contains_banned(text, self.terms))

    def test_message_does_not_repeat_the_word(self):
        self.assertEqual(moderation.errors("connard", self.terms), [moderation.MESSAGE])
        self.assertNotIn("connard", moderation.MESSAGE.lower())


if __name__ == "__main__":
    unittest.main()
