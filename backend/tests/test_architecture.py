"""Architecture : fonctionnalités enfichables, séparation des couches, toutes les pages s'affichent."""
import ast
import os
import pathlib
import tempfile
import unittest
from datetime import date, timedelta

from backend import create_app

APP_DIR = pathlib.Path(__file__).resolve().parent.parent  # le paquet backend


class _Base(unittest.TestCase):
    extra_features: list[str] = []

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "adminpass123"
        self.app = create_app({
            "TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
            "DATABASE": os.path.join(self.tmp.name, "t.sqlite"), "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up"),
            "EXTRA_FEATURES": self.extra_features,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def register(self):
        self.client.post("/inscription", data={"email": "e@test.fr", "username": "Léa", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})


class PluginTests(_Base):
    extra_features = ["backend.tests.fake_feature"]

    def test_feature_is_plugged_in(self):
        from backend.tests import fake_feature
        fake_feature.created.clear()
        self.register()
        page = self.client.get("/tableau-de-bord").get_data(as_text=True)
        self.assertIn(">Démo</a>", page, "entrée de menu ajoutée par le plugin")
        self.assertLess(page.index(">Mon agenda<"), page.index(">Démo<"))
        self.assertLess(page.index(">Démo<"), page.index(">Mes réglages<"), "ordre du menu respecté")
        self.assertIn("Page de démo pour Léa", self.client.get("/demo").get_data(as_text=True))
        r = self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                         "comfort": "3", "topics": "A"})
        self.assertEqual(len(fake_feature.created), 1, "le plugin reçoit l'événement « matière créée »")
        detail = self.client.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("La démo a vu passer une nouvelle matière.", detail, "le message du plugin est affiché")
        self.assertIn("Panneau de démo", detail)
        self.assertIn("Matière : Probabilités", detail)

    def test_plugins_do_not_leak_between_apps(self):
        other = create_app({"TESTING": True, "ENABLE_WEB": False,
                            "DATABASE": os.path.join(self.tmp.name, "o.sqlite"),
                            "UPLOAD_FOLDER": os.path.join(self.tmp.name, "o")})
        self.assertNotIn("demo", [f.name for f in other.extensions["features"]])
        self.assertNotIn("demo", other.blueprints)


class PagesTests(_Base):
    def test_home_topbar_and_encadrement_topbar(self):
        def topbar(html):
            return html[html.index('<header class="topbar'):html.index("</header>")]

        # visiteur : page de départ = présentation de Rivezli + « Encadrement »
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Ton année d'étudiant", page, "l'accueil présente Rivezli aux étudiants")
        self.assertNotIn("Un planning de révision qui tient", page, "la présentation du planificateur reste sur /encadrement")
        self.assertIn('href="/encadrement"', topbar(page))
        self.assertIn("Se connecter", topbar(page))
        self.assertIn("Un planning de révision qui tient", self.client.get("/encadrement").get_data(as_text=True),
                      "un visiteur voit la présentation du planificateur")

        # connecté : la page de départ n'a que la barre du haut (les espaces), sans seconde barre
        self.register()
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/encadrement"', topbar(home))
        self.assertNotIn('class="subbar"', home, "pas d'onglets sur la page de départ")
        r = self.client.get("/encadrement")
        self.assertTrue(r.headers["Location"].endswith("/tableau-de-bord"), "Encadrement s'ouvre sur « Aujourd'hui »")

        # dans l'espace Encadrement : même barre du haut (Encadrement surligné), onglets en dessous
        page = self.client.get("/tableau-de-bord").get_data(as_text=True).replace("&#39;", "'")
        bar = topbar(page)
        self.assertRegex(bar, r'href="/encadrement"\s+aria-current="page">Encadrement</a>', "espace surligné")
        self.assertIn("Se déconnecter", bar)
        self.assertNotIn("Planning", bar, "les onglets ne sont plus dans la barre du haut")
        sub = page[page.index('<nav class="subbar"'):]
        sub = sub[:sub.index("</nav>")]
        self.assertGreater(page.index('<nav class="subbar"'), page.index("</header>"), "seconde barre sous la première")
        self.assertIn('aria-current="page">Aujourd\'hui</a>', sub, "onglet « Aujourd'hui » actif")
        for tab in ("Planning", "Mon agenda", "Mes réglages"):
            self.assertIn(f">{tab}</a>", sub)

    def test_every_page_renders(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/connexion").status_code, 200)
        self.assertEqual(self.client.get("/inscription").status_code, 200)
        self.register()
        exam = (date.today() + timedelta(days=12)).isoformat()
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "exam", "exam_date": exam,
                                                     "comfort": "3", "topics": "A\nB"})
        self.client.post("/agenda/ajouter", data={"title": "CM Probabilités", "kind": "cours",
                                                  "date": (date.today() + timedelta(days=2)).isoformat(),
                                                  "start": "10:00", "end": "12:00", "repeat_weeks": "1"})
        for url in ["/tableau-de-bord", "/planning", "/agenda", "/preferences", "/profil", "/matieres/nouvelle",
                    "/matieres/1", "/matieres/1/modifier", "/planning/export.ics",
                    "/api/suggestions/matieres?q=pro", "/api/agenda/rattachement?titre=TD", "/api/ecoles?q=ens"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/matieres/999").status_code, 404)
        self.client.post("/deconnexion")
        self.client.post("/connexion", data={"email": "admin@test.fr", "password": "adminpass123"})
        for url in ["/admin/", "/admin/matieres", "/admin/utilisateurs/nouveau", "/admin/utilisateurs/2"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn(">Administration</a>", self.client.get("/admin/").get_data(as_text=True))


class HomeTests(_Base):
    """Page d'accueil : présentation des fonctionnalités, boutons de connexion et d'inscription."""

    def top(self, html):
        return html[html.index('<header class="topbar'):html.index("</header>")]

    def test_visitor_sees_features_and_sign_in_buttons(self):
        page = self.client.get("/").get_data(as_text=True)
        top = self.top(page)
        self.assertIn('href="/connexion"', top, "« Se connecter » en haut à droite")
        self.assertIn('href="/inscription"', top, "« Créer un compte » en haut à droite")
        self.assertLess(top.index("Se connecter"), top.index("Créer un compte"))
        self.assertIn("topbar-guest", top)
        for title in ("Un planning qui tient dans ta semaine", "Un atelier de révision", "Ton dossier scolaire",
                      "La BU et le resto U", "Ta recherche de stage", "Le compteur de caféine"):
            self.assertIn(title, page)
        self.assertIn('class="feature-body"', page)
        self.assertNotIn('<a class="feature-body"', page, "un visiteur ne voit pas des liens qui le renverraient à la connexion")
        after_features = page[page.index('id="cta-title"'):]
        self.assertIn('href="/inscription"', after_features, "fin de page : proposer de s'inscrire")
        self.assertIn('href="/connexion"', after_features, "… ou de se connecter")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_only_enabled_features_are_shown(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("La communauté", page, "la carte n'apparaît que si la fonctionnalité est activée")
        self.assertEqual(page.count('class="feature hl-'), len([f for f in self.app.extensions["features"] if f.name in
                                                              ("planning", "revision", "dossier", "campus", "stages", "cafeine")]))

    def test_logged_in_user_gets_links_and_dashboard_shortcut(self):
        self.register()
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('<a class="feature-body" href="/campus/"', page)
        self.assertIn('<a class="feature-body" href="/dossier/"', page)
        self.assertIn("Aller à mon tableau de bord", page)
        self.assertNotIn("Créer mon compte", page)
        self.assertNotIn("topbar-guest", self.top(page))


class LayeringTests(unittest.TestCase):
    """Chaque couche n'importe que les couches qui sont en dessous d'elle."""
    FORBIDDEN = {
        "business": ("flask", "backend.dao", "backend.services", "backend.controllers", "backend.core", "sqlite3", "requests"),
        "dao": ("backend.services", "backend.controllers"),
        "services": ("backend.controllers",),
    }

    def test_layers(self):
        for layer, forbidden in self.FORBIDDEN.items():
            for path in (APP_DIR / layer).glob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                        [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                    for name in names:
                        self.assertFalse(name.startswith(forbidden), f"{layer}/{path.name} importe {name}")


if __name__ == "__main__":
    unittest.main()
