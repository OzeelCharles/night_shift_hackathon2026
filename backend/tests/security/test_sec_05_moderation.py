"""SEC-05 — Filtre de mots interdits (constat F-12) : tests d'acceptation exigés par le cahier.

Le cahier demande un module pur `backend/business/moderation.py` et une liste `backend/data/mots_interdits.txt`,
appliqués aux textes visibles par d'autres personnes, d'abord le nom d'utilisateur. Les tests passent par
l'application (inscription et profil) pour ne pas imposer de nom de fonction ; seuls les deux chemins de
fichiers, fixés par le cahier, sont vérifiés directement.

Les termes injurieux ci-dessous sont des données de test, nécessaires pour vérifier le filtre.
"""
import os
import re
import unicodedata
import unittest

from ._outils import MOT_DE_PASSE, AppSecuTestCase

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

VRAIS_POSITIFS = ["connard", "Connard", "CONNARD", "salope", "enculé", "encule", "pute", "sale con", "Gros connard 93",
                  "nazi"]
DEGUISES = ["c0nn@rd", "C0NN4RD", "c.o.n.n.a.r.d", "c.o.n", "s@l0pe", "e n c u l é", "connnnnnard", "CoNnArD",
            "p-u-t-e", "s_a_l_o_p_e", "€ncul€", "c-0-n-n-4-r-d"]
FAUX_POSITIFS = ["Concarneau", "analyse", "Scunthorpe", "Constance", "computer", "salopette", "Raconteur", "Conan",
                 "Léa Dupont", "Deconinck"]


def _replie(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", texte)


class FiltreNomUtilisateurTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.n = 0

    def _inscrire(self, nom):
        self.n += 1
        email = f"u{self.n}@test.fr"
        r = self.inscrire(email, client=self.app.test_client(), username=nom, follow_redirects=True,
                          environ_base={"REMOTE_ADDR": f"192.0.2.{self.n}"})  # une IP par essai : pas de 429
        cree = bool(self.q("SELECT 1 FROM users WHERE email = ?", email))
        messages = " ".join(re.findall(r'<div class="flash[^"]*"[^>]*>\s*<div>(.*?)</div>', r.get_data(as_text=True), re.S))
        return cree, messages

    def test_vrais_positifs_refuses(self):
        for nom in VRAIS_POSITIFS:
            with self.subTest(nom=nom):
                cree, _ = self._inscrire(nom)
                self.assertFalse(cree, f"nom d'utilisateur accepté : {nom!r}")

    def test_variantes_deguisees_refusees(self):
        for nom in DEGUISES:
            with self.subTest(nom=nom):
                cree, _ = self._inscrire(nom)
                self.assertFalse(cree, f"variante déguisée acceptée : {nom!r}")

    def test_faux_positifs_acceptes(self):
        for nom in FAUX_POSITIFS:
            with self.subTest(nom=nom):
                cree, messages = self._inscrire(nom)
                self.assertTrue(cree, f"nom légitime refusé : {nom!r} ({messages})")

    def test_message_clair_sans_repeter_le_mot(self):
        for nom in ("connard", "c0nn@rd", "salope"):
            with self.subTest(nom=nom):
                cree, messages = self._inscrire(nom)
                self.assertFalse(cree)
                self.assertTrue(messages.strip(), "aucun message affiché")
                self.assertNotIn(_replie(nom), _replie(messages), "le message répète le mot refusé")

    def test_profil_refuse_aussi_les_mots_interdits(self):
        cree, _ = self._inscrire("Eleve Correct")
        self.assertTrue(cree)
        client = self.app.test_client()
        self.connecter("u1@test.fr", MOT_DE_PASSE, client=client)
        for nom in ("connard", "c.o.n", "s@l0pe"):
            with self.subTest(nom=nom):
                client.post("/profil", data={"username": nom, "level": "licence", "school": "ENSAI"})
                self.assertEqual(self.q("SELECT username FROM users WHERE email = 'u1@test.fr'")[0]["username"],
                                 "Eleve Correct")


class FichiersExigesTests(unittest.TestCase):
    def test_module_et_liste_existent(self):
        self.assertTrue(os.path.isfile(os.path.join(RACINE, "business", "moderation.py")),
                        "backend/business/moderation.py manquant")
        liste = os.path.join(RACINE, "data", "mots_interdits.txt")
        self.assertTrue(os.path.isfile(liste), "backend/data/mots_interdits.txt manquant")


if __name__ == "__main__":
    unittest.main()
