"""Version mobile (appli installable) et mise en ligne : manifeste, icônes, service worker, page hors ligne, production."""
import json
import os
import re
import struct
import tempfile
import unittest
from unittest import mock

from werkzeug.middleware.proxy_fix import ProxyFix

from backend import config, create_app


def make_app(tmp, **extra):
    os.environ["ADMIN_EMAIL"] = "admin@test.fr"
    os.environ["ADMIN_PASSWORD"] = "adminpass123"
    settings = {"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                "DATABASE": os.path.join(tmp, "t.sqlite"), "UPLOAD_FOLDER": os.path.join(tmp, "up")}
    settings.update(extra)
    return create_app(settings)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "pas un PNG"
    return struct.unpack(">II", data[16:24])


class PwaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = make_app(self.tmp.name)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self):
        self.client.post("/inscription", data={"email": "lea@test.fr", "username": "Léa", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})

    # ------------------------------------------------------------------ manifeste et icônes
    def test_manifest_describes_an_installable_app(self):
        reply = self.client.get("/manifest.webmanifest")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.mimetype, "application/manifest+json")
        m = json.loads(reply.get_data(as_text=True))
        self.assertEqual((m["name"], m["short_name"], m["display"], m["lang"]), ("Rivezli", "Rivezli", "standalone", "fr"))
        self.assertEqual(m["scope"], "/")
        self.assertEqual(m["start_url"], "/tableau-de-bord", "l'appli s'ouvre sur « Aujourd'hui »")
        self.assertRegex(m["theme_color"], r"^#[0-9a-f]{6}$")
        self.assertRegex(m["background_color"], r"^#[0-9a-f]{6}$")

    def test_icons_exist_with_the_declared_size_and_a_maskable_one(self):
        m = self.client.get("/manifest.webmanifest").get_json(force=True)
        by_purpose = {}
        for icon in m["icons"]:
            reply = self.client.get(icon["src"])
            self.assertEqual(reply.status_code, 200, icon["src"])
            declared = tuple(int(n) for n in icon["sizes"].split("x"))
            self.assertEqual(png_size(reply.get_data()), declared, icon["src"])
            by_purpose.setdefault(icon["purpose"], set()).add(declared)
        self.assertIn((192, 192), by_purpose["any"])
        self.assertIn((512, 512), by_purpose["any"])
        self.assertIn((512, 512), by_purpose["maskable"], "icône rognable par Android sans couper le logo")

    def test_shortcuts_lead_to_real_pages(self):
        m = self.client.get("/manifest.webmanifest").get_json(force=True)
        self.assertGreaterEqual(len(m["shortcuts"]), 3)
        for s in m["shortcuts"]:
            self.assertIn(self.client.get(s["url"]).status_code, (200, 302), s["url"])

    # ------------------------------------------------------------------ service worker
    def test_service_worker_is_served_from_the_root_and_always_revalidated(self):
        reply = self.client.get("/sw.js")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.mimetype, "text/javascript")
        self.assertEqual(reply.headers["Cache-Control"], "no-cache")
        self.assertEqual(reply.headers["Service-Worker-Allowed"], "/")
        self.assertRegex(reply.get_data(as_text=True), r'const CACHE = "rivezli-[0-9a-f]{10}";')

    def test_service_worker_never_keeps_personal_pages(self):
        self.login()
        source = self.client.get("/sw.js").get_data(as_text=True)
        precache = json.loads(re.search(r"const PRECACHE = (\[.*?\]);", source, re.S).group(1))
        self.assertIn("/hors-ligne", precache)
        for url in precache:
            self.assertTrue(url == "/hors-ligne" or url.startswith("/static/"), f"page personnelle précachée : {url}")
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(source.count("cache.put"), 1, "une seule écriture en cache : les fichiers statiques")
        navigate = source[source.index('request.mode === "navigate"'):source.index("FONT_HOSTS.includes")]
        self.assertNotIn("put", navigate, "les pages de l'application viennent toujours du réseau")
        self.assertIn('request.method !== "GET"', source, "formulaires et assistant : jamais interceptés")
        self.assertIn("/static/video/", source, "la vidéo, lue par morceaux, n'est pas mise en cache")

    def test_cache_is_renewed_when_a_precached_file_changes(self):
        from backend.controllers import pwa
        with self.app.test_request_context():
            before = pwa._version()
            self.assertEqual(before, pwa._version(), "empreinte stable")
            real_open = open

            def patched(path, mode="r", *a, **k):
                handle = real_open(path, mode, *a, **k)
                if str(path).endswith("style.css") and "b" in mode:
                    import io
                    return io.BytesIO(handle.read() + b"/* modified */")
                return handle

            with mock.patch("builtins.open", patched):
                self.assertNotEqual(before, pwa._version())

    # ------------------------------------------------------------------ page hors ligne
    def test_offline_page_is_public_and_shows_nothing_personal(self):
        guest = self.client.get("/hors-ligne")
        self.assertEqual(guest.status_code, 200)
        self.login()
        page = self.client.get("/hors-ligne").get_data(as_text=True)
        self.assertIn("Tu es hors ligne", page)
        self.assertIn("Réessayer", page)
        self.assertNotIn("Léa", page, "aucune donnée de compte dans une page mise en cache")
        self.assertNotIn("topbar", page, "pas d'en-tête de l'application")

    # ------------------------------------------------------------------ balises de la page
    def test_pages_declare_the_app_for_phones(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', page)
        self.assertEqual(len(re.findall(r'<meta name="theme-color"', page)), 2, "thème clair et thème sombre")
        for tag in ('name="apple-mobile-web-app-capable"', 'name="mobile-web-app-capable"', 'rel="apple-touch-icon"',
                    'name="viewport"', 'src="/static/pwa.js"', 'data-sw="/sw.js"'):
            self.assertIn(tag, page)

    def test_install_box_is_hidden_until_the_browser_can_install(self):
        page = self.client.get("/").get_data(as_text=True)
        box = page[page.index('class="install"'):page.index('class="donate"')]
        self.assertIn("data-install-box hidden", page)
        self.assertIn("data-install hidden", box)
        self.assertIn("Sur l'écran d'accueil", box.replace("&#39;", "'"), "geste à faire sur iPhone")
        self.assertLess(page.index('id="cta-title"'), page.index('id="install-title"'))
        self.assertLess(page.index('id="install-title"'), page.index('id="donate-title"'), "les dons restent la dernière section")

    def test_app_still_works_without_the_mobile_feature(self):
        features = [f for f in config.FEATURES if not f.endswith(".pwa")]
        with tempfile.TemporaryDirectory() as tmp:
            page = make_app(tmp, FEATURES=features).test_client().get("/")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('rel="manifest"', page.get_data(as_text=True))

    def test_browser_security_headers_everywhere(self):
        for path in ("/", "/connexion", "/sw.js", "/static/style.css"):
            h = self.client.get(path).headers
            self.assertEqual(h["X-Content-Type-Options"], "nosniff", path)
            self.assertEqual(h["X-Frame-Options"], "DENY", path)
            self.assertIn("Referrer-Policy", h, path)
            self.assertNotIn("Strict-Transport-Security", h, "HSTS seulement en production")


class ProductionTests(unittest.TestCase):
    ENV = {"RIVEZLI_ENV": "production", "SECRET_KEY": "x" * 64}

    def build(self, tmp, **env):
        with mock.patch.dict(os.environ, {**self.ENV, **env}):
            return make_app(tmp)

    def test_production_secures_cookies_and_enables_hsts(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.build(tmp)
            self.assertTrue(app.config["PRODUCTION"])
            self.assertTrue(app.config["SESSION_COOKIE_SECURE"], "cookie de session seulement en https")
            self.assertEqual(app.config["PREFERRED_URL_SCHEME"], "https")
            self.assertEqual(app.config["SECRET_KEY"], "x" * 64)
            self.assertIn("max-age=", app.test_client().get("/").headers["Strict-Transport-Security"])

    def test_production_refuses_to_start_without_a_secret_key(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"RIVEZLI_ENV": "production"}):
            os.environ.pop("SECRET_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                make_app(tmp)

    def test_development_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"RIVEZLI_ENV": ""}):
            app = make_app(tmp)
            self.assertFalse(app.config["PRODUCTION"])
            self.assertFalse(app.config["SESSION_COOKIE_SECURE"], "le site marche en http en local")
            self.assertNotIsInstance(app.wsgi_app, ProxyFix)

    def test_proxy_headers_are_trusted_only_in_production_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(self.build(tmp).wsgi_app, ProxyFix)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIsInstance(self.build(tmp, TRUST_PROXY="0").wsgi_app, ProxyFix)

    def test_admin_creation_survives_two_processes_starting_together(self):
        """gunicorn lance plusieurs processus : sur une base vide, tous veulent créer l'administrateur au même instant."""
        from backend.dao.db import scalar
        with tempfile.TemporaryDirectory() as tmp:
            app = self.build(tmp)
            with app.app_context():
                from backend.services import auth_service
                with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "admin@test.fr"}), \
                        mock.patch.object(auth_service.dao, "any_admin", return_value=False), \
                        mock.patch.object(auth_service.dao, "by_email", return_value=None):
                    auth_service.ensure_admin()          # l'autre processus a gagné la course : ne doit pas lever d'erreur
                self.assertEqual(scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'"), 1)

    def test_data_directory_can_be_moved_to_a_durable_disk(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as disk:
            with mock.patch.dict(os.environ, {**self.ENV, "INSTANCE_DIR": disk}):
                app = create_app({"TESTING": True, "ENABLE_WEB": False})
            self.assertEqual(app.instance_path, os.path.abspath(disk))
            self.assertTrue(app.config["DATABASE"].startswith(os.path.abspath(disk)))
            self.assertTrue(app.config["UPLOAD_FOLDER"].startswith(os.path.abspath(disk)))
            self.assertTrue(os.path.isfile(os.path.join(disk, "revision.sqlite")))


if __name__ == "__main__":
    unittest.main()
