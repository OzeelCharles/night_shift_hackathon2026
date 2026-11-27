"""Architecture : fonctionnalités enfichables, séparation des couches, toutes les pages s'affichent."""
import ast
import os
import pathlib
import tempfile
import unittest
from datetime import date, timedelta

from app import create_app

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


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
    extra_features = ["tests.fake_feature"]

    def test_feature_is_plugged_in(self):
        from tests import fake_feature
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


class LayeringTests(unittest.TestCase):
    """Chaque couche n'importe que les couches qui sont en dessous d'elle."""
    FORBIDDEN = {
        "business": ("flask", "app.dao", "app.services", "app.controllers", "app.core", "sqlite3", "requests"),
        "dao": ("app.services", "app.controllers"),
        "services": ("app.controllers",),
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
