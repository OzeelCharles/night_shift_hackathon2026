"""SEC-04 — CSRF et redirections.

- Tout POST sans jeton, avec un jeton vide ou avec le jeton d'une autre session répond 400 (parcours de app.url_map).
- Aucune route GET ne modifie la base.
- `next` (et les autres retours) n'envoient jamais vers un autre site, une fois l'URL interprétée par un navigateur.
"""
import io
import json
import re
import unittest
from urllib.parse import quote, urljoin, urlsplit

from ._outils import ADMIN_EMAIL, ADMIN_MDP, MOT_DE_PASSE, AppSecuTestCase

HOTE = "rivezli.test"
# Charges du cahier (les 5 premières) et variantes qu'un navigateur interprète comme un autre hôte
CHARGES_CAHIER = ["//evil.com", "/\\evil.com", "https://evil.com", "%2F%2Fevil.com", "/admin/..//evil.com"]
VARIANTES = ["\\/evil.com", "/\\/evil.com", "/\t/evil.com", "/\n/evil.com", " //evil.com", "///evil.com",
             "/%5Cevil.com", "/%09/evil.com", "https:evil.com", "javascript:alert(1)", "\\\\evil.com"]


def destination_navigateur(location: str) -> str:
    """Hôte vers lequel un navigateur (analyseur d'URL WHATWG) irait avec cet en-tête Location."""
    loc = location.strip(" \x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13"
                         "\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f")
    loc = re.sub(r"[\t\n\r]", "", loc).replace("\\", "/")  # schéma « spécial » http(s) : \ vaut /
    absolue = urljoin(f"http://{HOTE}/connexion", loc)
    if urlsplit(absolue).scheme not in ("http", "https"):
        return "schema:" + urlsplit(absolue).scheme
    return urlsplit(absolue).hostname or ""


def jeton(page: str) -> str:
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', page)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- CSRF
class CsrfTests(AppSecuTestCase):
    config = {"WTF_CSRF_DISABLED": False, "PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.tok = jeton(self.client.get("/inscription").get_data(as_text=True))
        self.assertTrue(self.tok, "aucun jeton CSRF dans le formulaire d'inscription")
        self.client.post("/inscription", data={"email": "eleve@test.fr", "username": "Eleve test",
                                               "password": MOT_DE_PASSE, "password2": MOT_DE_PASSE,
                                               "level": "licence", "school": "ENSAI", "_csrf": self.tok})
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]
        self.tok = jeton(self.client.get("/profil").get_data(as_text=True))
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A\nB", "_csrf": self.tok,
                                                     "documents": (io.BytesIO(b"# A\ntexte"), "c.md")},
                         content_type="multipart/form-data")
        self.assertEqual(len(self.q("SELECT id FROM subjects")), 1, "préparation : matière non créée")
        # jeton valide d'une autre session (autre visiteur)
        self.autre = self.app.test_client()
        self.tok_autre = jeton(self.autre.get("/connexion").get_data(as_text=True))
        self.assertTrue(self.tok_autre)
        self.assertNotEqual(self.tok, self.tok_autre)

    def _instantane(self):
        tables = [r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: sorted(json.dumps(r, sort_keys=True, default=str) for r in self.q(f"SELECT * FROM {t}")) for t in tables}

    def _routes_post(self):
        for rule in self.app.url_map.iter_rules():
            if "POST" in rule.methods:
                url = rule.rule
                for a in rule.arguments:
                    url = re.sub(rf"<(?:int:)?{a}>", "1", url)
                yield url

    def test_tout_post_sans_jeton_valide_renvoie_400(self):
        routes = list(self._routes_post())
        self.assertGreater(len(routes), 40)
        avant = self._instantane()
        formulaire = {"name": "X", "title": "X", "content": "X", "email": "x@test.fr", "password": "X" * 12}
        for url in routes:
            for nom, extra, entetes in (("sans jeton", {}, {}), ("jeton vide", {"_csrf": ""}, {}),
                                        ("jeton d'une autre session", {"_csrf": self.tok_autre}, {}),
                                        ("en-tête d'une autre session", {}, {"X-CSRF-Token": self.tok_autre})):
                with self.subTest(route=url, cas=nom):
                    r = self.client.post(url, data={**formulaire, **extra}, headers=entetes)
                    self.assertEqual(r.status_code, 400, f"POST {url} ({nom}) : {r.status_code}")
        self.assertEqual(avant, self._instantane(), "la base a changé malgré des POST sans jeton valide")

    def test_connexion_et_inscription_sans_jeton_refusees(self):
        """CSRF de connexion : un site tiers ne peut pas connecter la victime au compte de l'attaquant."""
        visiteur = self.app.test_client()
        r = visiteur.post("/connexion", data={"email": "eleve@test.fr", "password": MOT_DE_PASSE})
        self.assertEqual(r.status_code, 400)
        r = visiteur.post("/inscription", data={"email": "n@test.fr", "username": "Nouveau", "password": MOT_DE_PASSE,
                                                "password2": MOT_DE_PASSE, "level": "licence", "school": "ENSAI"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = 'n@test.fr'")[0]["n"], 0)

    def test_jeton_valide_accepte(self):
        """Témoin : avec le bon jeton, la même requête passe (le test ci-dessus ne refuse pas tout)."""
        r = self.client.post("/stages/postes", data={"title": "Juriste", "location": "Rennes", "_csrf": self.tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.q("SELECT * FROM stg_wishes")), 1)


# ---------------------------------------------------------------- GET sans effet
class GetSansEffetTests(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        c = self.client
        c.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2", "comfort": "3",
                                           "topics": "Suites\nLimites", "documents": (io.BytesIO(
                                               "# Suites\nUne suite converge.\n# Limites\nLa limite existe.".encode()),
                                               "c.md")}, content_type="multipart/form-data")
        c.post("/agenda/ajouter", data={"title": "Cours d'analyse", "kind": "cours", "subject": "1",
                                        "date": "2030-01-07", "start": "10:00", "end": "12:00", "repeat_weeks": "1"})
        c.post("/stages/offres", data={"title": "Offre", "description": "Texte"})
        c.post("/stages/lettres", data={"offer_id": "1"})
        c.post("/cafeine/ajouter", data={"drink": "the"})
        c.post("/reviser/1/niveau", data={"difficulte": "moyen"})
        # on visite une première fois chaque page : les créations paresseuses légitimes (préférences par défaut…)
        # ont lieu ici ; la seconde visite ne doit plus rien changer
        for url in self._urls_get():
            c.get(url, query_string={"q": "a", "titre": "Cours", "type": "cours", "niveau": "licence"})

    def _urls_get(self):
        for rule in self.app.url_map.iter_rules():
            if "GET" in rule.methods and rule.endpoint != "static" and rule.rule not in ("/deconnexion",):
                url = rule.rule
                for a in rule.arguments:
                    url = re.sub(rf"<(?:int:)?{a}>", "1", url)
                yield url

    def _instantane(self):
        tables = [r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: sorted(json.dumps(r, sort_keys=True, default=str) for r in self.q(f"SELECT * FROM {t}")) for t in tables}

    def test_aucune_route_get_ne_modifie_la_base(self):
        for url in self._urls_get():
            avant = self._instantane()
            with self.subTest(route=url):
                self.client.get(url, query_string={"q": "a", "titre": "Cours", "type": "cours", "niveau": "licence",
                                                   "telecharger": "1", "chapitre": "Suites"})
                apres = self._instantane()
                changees = [t for t in avant if avant[t] != apres.get(t)]
                self.assertEqual(changees, [], f"GET {url} modifie : {changees}")


# ---------------------------------------------------------------- redirections
class RedirectionsTests(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.client.post("/deconnexion")

    def test_safe_next_refuse_les_charges_du_cahier(self):
        from backend.core.security import safe_next
        for charge in CHARGES_CAHIER + VARIANTES:
            with self.subTest(charge=charge):
                from urllib.parse import unquote
                for valeur in (charge, unquote(charge)):
                    self.assertEqual(safe_next(valeur, "/defaut"), "/defaut", f"safe_next accepte {valeur!r}")
        self.assertEqual(safe_next("/planning?semaine=2#x", "/defaut"), "/planning?semaine=2#x", "témoin interne")

    def test_next_apres_connexion_reste_sur_le_site(self):
        for charge in CHARGES_CAHIER + VARIANTES:
            with self.subTest(charge=charge):
                self.client.post("/deconnexion")
                r = self.client.post("/connexion?next=" + quote(charge, safe="%"),
                                     data={"email": "eleve@test.fr", "password": MOT_DE_PASSE})
                self.assertNotEqual(r.status_code, 500)
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers["Location"]
                    self.assertEqual(destination_navigateur(loc), HOTE, f"next={charge!r} -> Location {loc!r}")

    def test_next_des_formulaires_reste_sur_le_site(self):
        """Mêmes charges sur les autres routes qui suivent un « next » ou un « back » envoyé par formulaire."""
        self.connecter("eleve@test.fr")
        self.client.post("/agenda/ajouter", data={"title": "Occupé", "kind": "autre", "date": "2030-01-07",
                                                  "start": "10:00", "end": "11:00", "repeat_weeks": "1"})
        routes = [("/agenda/1/supprimer", "next"), ("/seances/1/fait", "next"), ("/seances/1/supprimer", "next"),
                  ("/profil/infos", "next"), ("/campus/signaler", "back")]
        for url, champ in routes:
            for charge in ("/\\evil.com", "/\t/evil.com", "//evil.com"):
                with self.subTest(route=url, charge=charge):
                    r = self.client.post(url, data={champ: charge})
                    self.assertNotEqual(r.status_code, 500)
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers["Location"]
                        self.assertEqual(destination_navigateur(loc), HOTE, f"{url} {champ}={charge!r} -> {loc!r}")

    def test_retour_admin_reste_sur_le_site(self):
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        for charge in ("/admin/../\\evil.com", "/admin\\..\\..\\evil.com", "/admin/..//evil.com", "/admin@evil.com"):
            with self.subTest(charge=charge):
                r = self.client.post("/admin/matieres/999/supprimer", data={"next": charge})
                self.assertNotEqual(r.status_code, 500)
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers["Location"]
                    self.assertEqual(destination_navigateur(loc), HOTE, f"{charge!r} -> {loc!r}")

    def test_injection_d_en_tete_par_next(self):
        r = self.client.post("/connexion?next=/%0d%0aSet-Cookie:%20pirate=1",
                             data={"email": "eleve@test.fr", "password": MOT_DE_PASSE})
        self.assertNotEqual(r.status_code, 500)
        self.assertNotIn("pirate", r.headers.get("Set-Cookie", ""))



# ---------------------------------------------------------------- vérification des corrections (manche 2)
class VariantesRedirectionsTests(RedirectionsTests):
    """Variantes de F-025 et F-026 : doubles encodages, autres caractères, autres routes."""

    test_safe_next_refuse_les_charges_du_cahier = None
    test_next_apres_connexion_reste_sur_le_site = None
    test_next_des_formulaires_reste_sur_le_site = None
    test_retour_admin_reste_sur_le_site = None
    test_injection_d_en_tete_par_next = None

    CHARGES = ["/%252F%252Fevil.com", "/%25252F%25252Fevil.com", "/%2509/evil.com", "/%5C%5Cevil.com",
               "/%E3%80%82/evil.com", "/　/evil.com", "/ /evil.com", "/%0B/evil.com", "/%00/evil.com",
               "/%2e%2e//evil.com", "/./evil.com", "/;/evil.com", "/@evil.com", "/%40evil.com", "/／／evil.com",
               "/%EF%BC%8F%EF%BC%8Fevil.com", "/%0D%0ALocation:%20//evil.com", "/%E2%80%AE//evil.com"]

    def test_variantes_apres_connexion(self):
        for charge in self.CHARGES:
            with self.subTest(charge=charge):
                self.client.post("/deconnexion")
                r = self.client.post("/connexion?next=" + quote(charge, safe="%/"),
                                     data={"email": "eleve@test.fr", "password": MOT_DE_PASSE})
                self.assertNotEqual(r.status_code, 500)
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers["Location"]
                    self.assertEqual(destination_navigateur(loc), HOTE, f"{charge!r} -> {loc!r}")
                    self.assertNotIn("\n", loc)

    def test_variantes_sur_les_formulaires(self):
        self.connecter("eleve@test.fr")
        self.client.post("/agenda/ajouter", data={"title": "Occupé", "kind": "autre", "date": "2030-01-07",
                                                  "start": "10:00", "end": "11:00", "repeat_weeks": "1"})
        routes = [("/profil/infos", "next"), ("/campus/signaler", "back"), ("/agenda/1/supprimer", "next")]
        for url, champ in routes:
            for charge in ("/%252F%252Fevil.com", "/ /evil.com", "/%0D%0Aevil", "/／／evil.com"):
                with self.subTest(route=url, charge=charge):
                    r = self.client.post(url, data={champ: charge})
                    self.assertNotEqual(r.status_code, 500)
                    if r.status_code in (301, 302, 303, 307, 308):
                        self.assertEqual(destination_navigateur(r.headers["Location"]), HOTE)

    def test_chemins_internes_toujours_acceptes(self):
        """Témoin : la correction ne casse pas les retours légitimes."""
        from backend.core.security import safe_next
        for ok in ("/planning", "/planning?semaine=2", "/matieres/1#supports", "/campus/restos?ville=Rennes",
                   "/reviser/1/fiches?chapitre=Lois%20usuelles"):
            with self.subTest(next=ok):
                self.assertEqual(safe_next(ok, "/defaut"), ok)


class VariantesRechercheAdminTests(AppSecuTestCase):
    """Variantes de F-027 : recherche admin longue, jokers, octet nul."""

    config = {"PROPAGATE_EXCEPTIONS": False}

    def test_recherches_extremes(self):
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        for q in ("%" * 100, "_" * 101, "x" * 101, "%" * 60000, "\x00" * 200, "é" * 50000, "[" * 1000):
            with self.subTest(q=repr(q[:12]), longueur=len(q)):
                r = self.client.get("/admin/", query_string={"q": q})
                self.assertNotEqual(r.status_code, 500)


if __name__ == "__main__":
    unittest.main()
