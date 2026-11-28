"""SEC-09 — En-têtes HTTP, cookies, ressources externes et dépendances (constats F-05 et F-10).

Les en-têtes sont vérifiés sur /, /connexion, une page connectée et une réponse 404. Les tests « CSP
compatible » vérifient que la politique ne casse pas les pages : sans 'unsafe-inline', chaque script en ligne
doit porter le nonce de la réponse, et aucun attribut d'événement (onload=, onsubmit=…) ne doit rester.
"""
import os
import re
import unittest

from ._outils import AppSecuTestCase

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ATTENDUS = {
    "X-Content-Type-Options": lambda v: v.lower() == "nosniff",
    "X-Frame-Options": lambda v: v.upper() == "DENY",
    "Referrer-Policy": lambda v: v.lower() == "strict-origin-when-cross-origin",
    "Permissions-Policy": lambda v: all(f"{x}=()" in v.replace(" ", "") for x in ("camera", "microphone", "geolocation")),
}


def directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        mots = part.split()
        if mots:
            out[mots[0].lower()] = mots[1:]
    return out


class EntetesTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.visiteur = self.app.test_client()

    def _reponses(self):
        return {"/ (visiteur)": self.visiteur.get("/"), "/connexion": self.visiteur.get("/connexion"),
                "/tableau-de-bord (connecté)": self.client.get("/tableau-de-bord"),
                "404": self.client.get("/page-qui-n-existe-pas")}

    def test_entetes_de_securite_presents(self):
        for nom, r in self._reponses().items():
            for entete, ok in ATTENDUS.items():
                with self.subTest(page=nom, entete=entete):
                    v = r.headers.get(entete)
                    self.assertIsNotNone(v, f"{nom} : {entete} absent")
                    self.assertTrue(ok(v), f"{nom} : {entete}: {v}")

    def test_content_security_policy(self):
        for nom, r in self._reponses().items():
            with self.subTest(page=nom):
                csp = r.headers.get("Content-Security-Policy")
                self.assertIsNotNone(csp, f"{nom} : Content-Security-Policy absent")
                d = directives(csp)
                self.assertEqual(d.get("default-src"), ["'self'"])
                self.assertEqual(d.get("frame-ancestors"), ["'none'"])
                self.assertEqual(d.get("base-uri"), ["'self'"])
                self.assertEqual(d.get("form-action"), ["'self'"])
                self.assertIn("script-src", d)
                self.assertNotIn("'unsafe-eval'", d["script-src"])
                self.assertNotIn("*", d["script-src"])
                self.assertFalse(any(s in ("http:", "https:", "data:") for s in d["script-src"]),
                                 "script-src trop large")

    def test_csp_compatible_avec_les_pages(self):
        """Si script-src n'autorise pas 'unsafe-inline' : chaque <script> en ligne porte le nonce de la réponse,
        et aucun attribut d'événement ne subsiste (sinon la page casse sous la CSP)."""
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A\nB"})
        pages = ["/", "/connexion", "/inscription", "/tableau-de-bord", "/agenda", "/matieres/1", "/matieres/nouvelle",
                 "/dossier/", "/reviser/1", "/stages", "/campus/restos", "/profil"]
        for url in pages:
            client = self.visiteur if url in ("/connexion", "/inscription") else self.client
            r = client.get(url)
            csp = directives(r.headers.get("Content-Security-Policy", ""))
            script_src = csp.get("script-src", csp.get("default-src"))
            if script_src is None or "'unsafe-inline'" in script_src:
                continue  # pas de CSP (test précédent) ou politique permissive (test précédent)
            nonces = {s[len("'nonce-"):-1] for s in script_src if s.startswith("'nonce-")}
            page = r.get_data(as_text=True)
            with self.subTest(page=url):
                for balise in re.findall(r"<script\b[^>]*>", page):
                    if "src=" not in balise:
                        m = re.search(r'nonce="([^"]+)"', balise)
                        self.assertTrue(m and m.group(1) in nonces, f"{url} : script en ligne sans nonce : {balise}")
                evenements = re.findall(r"<[a-z]+\b[^>]*\son[a-z]+\s*=", page, re.I)
                self.assertEqual(evenements, [], f"{url} : attributs d'événement bloqués par la CSP")

    def test_cookie_de_session(self):
        """Non-régression : HttpOnly et SameSite=Lax."""
        r = self.app.test_client().post("/inscription", data={"email": "c@test.fr", "username": "Eleve c",
                                                              "password": "Tisane-Velo-Granit-42",
                                                              "password2": "Tisane-Velo-Granit-42",
                                                              "level": "licence", "school": "ENSAI"})
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)


def _gabarits():
    for base in (os.path.join(RACINE, "frontend", "templates"), os.path.join(RACINE, "backend", "plugins")):
        for d, _, fs in os.walk(base):
            for f in fs:
                if f.endswith(".html"):
                    chemin = os.path.join(d, f)
                    yield os.path.relpath(chemin, RACINE).replace(os.sep, "/"), open(chemin, encoding="utf-8").read()


class RessourcesExternesTests(unittest.TestCase):
    def test_scripts_et_feuilles_externes_avec_integrity(self):
        """F-10 : KaTeX (cdnjs) sans integrity ni crossorigin. Les polices Google (CSS générée à la volée,
        sans SRI possible) sont hors de ce test."""
        manquants = []
        for rel, txt in _gabarits():
            for balise in re.findall(r"<(?:script|link)\b[^>]*>", txt, re.S):
                url = re.search(r'(?:src|href)="(https?:)?//([^"/]+)', balise)
                if not url or url.group(2) in ("fonts.googleapis.com", "fonts.gstatic.com"):
                    continue
                if balise.startswith("<link") and not re.search(r'rel="(stylesheet|preload|modulepreload)"', balise):
                    continue  # preconnect, icônes…
                if "integrity=" not in balise or "crossorigin" not in balise:
                    manquants.append((rel, " ".join(balise.split())[:90]))
        self.assertEqual(manquants, [], "ressources externes sans SRI")


class DependancesTests(unittest.TestCase):
    def test_versions_epinglees(self):
        """F-032 : requirements.txt n'épingle aucune version (>=)."""
        lignes = [l.split("#")[0].strip() for l in open(os.path.join(RACINE, "requirements.txt"), encoding="utf-8")]
        lignes = [l for l in lignes if l and not l.startswith("-")]
        self.assertTrue(lignes)
        non_epinglees = [l for l in lignes if not re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[A-Za-z0-9_.\-+]+", l)]
        self.assertEqual(non_epinglees, [], "dépendances non épinglées (==)")


if __name__ == "__main__":
    unittest.main()
