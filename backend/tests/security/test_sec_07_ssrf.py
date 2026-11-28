"""SEC-07 — Requêtes sortantes (SSRF). Constats F-01 (lien d'offre de stage) et F-02 (rebinding DNS, PDF).

Aucune connexion externe : un garde remplace la résolution DNS et bloque toute connexion hors boucle locale ;
un serveur local (127.0.0.1) joue la « page interne » et compte les requêtes qu'il reçoit.
Comportement attendu (cahier SEC-07) : une URL qui vise une adresse non publique est refusée
sans qu'aucune connexion ne parte vers cette adresse, y compris après redirection ou rebinding DNS.
"""
import json
import unittest

from ._outils import AppSecuTestCase, GardeReseau, ServeurLocal

SECRET = "PAGE-INTERNE-SECRETE"
PAGE_INTERNE = (
    "<html><head><title>" + SECRET + "</title>"
    '<script type="application/ld+json">'
    + json.dumps({"@type": "JobPosting", "title": SECRET, "description": "jeton-interne-0123456789",
                  "hiringOrganization": {"name": "Intranet"}})
    + "</script></head><body>admin</body></html>"
).encode()
PAGES = {"/admin": (200, {"Content-Type": "text/html; charset=utf-8"}, PAGE_INTERNE)}
HOTE_PUBLIC_IP = "8.8.8.8"  # adresse publique simulée : le garde redirige la connexion vers un serveur local


class SsrfOffreDeStageTests(AppSecuTestCase):
    """F-01 : POST /stages/offres avec un lien seul fait télécharger la page par le serveur."""

    config = {"ENABLE_WEB": True}  # le téléchargement du lien n'a lieu que si le web est activé (cas réel)

    def setUp(self):
        super().setUp()
        self.inscrire("victime@test.fr")

    def _vider_offres(self):
        with self.app.app_context():
            from backend.dao.db import get_db
            get_db().execute("DELETE FROM stg_offers")
            get_db().commit()

    def _poster(self, url, garde, serveur=None):
        """Chaque essai part d'un état propre : aucune offre, aucune connexion, aucune requête reçue."""
        self._vider_offres()
        garde.connexions.clear()
        if serveur is not None:
            serveur.requetes.clear()
        return self.client.post("/stages/offres", data={"url": url}, follow_redirects=True)

    def _verifier_refus(self, url, serveur, garde, page):
        self.assertEqual(serveur.requetes, [], f"{url} : le serveur interne a été appelé ({serveur.requetes})")
        self.assertEqual(garde.connexions_internes(), [], f"{url} : connexion vers une adresse interne")
        self.assertNotIn(SECRET, page, f"{url} : le contenu de la page interne revient à l'utilisateur")
        offres = self.q("SELECT title, description FROM stg_offers")
        self.assertFalse(any(SECRET in (o["title"] + o["description"]) for o in offres),
                         f"{url} : la page interne a été enregistrée dans l'offre")
        self.assertEqual(offres, [], f"{url} : un lien vers une adresse interne doit être refusé")

    def test_lien_vers_boucle_locale_refuse_sans_connexion(self):
        with ServeurLocal(PAGES) as serveur:
            port = serveur.port
            urls = [f"http://127.0.0.1:{port}/admin", f"http://localhost:{port}/admin",
                    f"http://[::1]:{port}/admin", f"http://0x7f000001:{port}/admin",
                    f"http://2130706433:{port}/admin", f"http://127.1:{port}/admin",
                    f"http://[::ffff:127.0.0.1]:{port}/admin", f"http://0.0.0.0:{port}/admin"]
            with GardeReseau() as garde:
                for url in urls:
                    with self.subTest(url=url):
                        page = self._poster(url, garde, serveur).get_data(as_text=True)
                        self._verifier_refus(url, serveur, garde, page)

    def test_adresses_privees_et_metadonnees_cloud_refusees(self):
        urls = ["http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://192.168.1.1/admin",
                "http://172.16.0.1/", "http://[fd00::1]/", "http://100.64.0.1/"]
        with ServeurLocal(PAGES) as serveur, GardeReseau() as garde:
            for url in urls:
                with self.subTest(url=url):
                    page = self._poster(url, garde, serveur).get_data(as_text=True)
                    self._verifier_refus(url, serveur, garde, page)

    def test_redirection_d_un_hote_public_vers_la_boucle_locale(self):
        with ServeurLocal(PAGES) as interne:
            cible = f"http://127.0.0.1:{interne.port}/admin"
            with ServeurLocal(defaut=(302, {"Location": cible}, b"")) as public:
                detours = {(HOTE_PUBLIC_IP, 80): ("127.0.0.1", public.port)}
                with GardeReseau(dns={"offres-publiques.example": [HOTE_PUBLIC_IP]}, detours=detours) as garde:
                    page = self._poster("http://offres-publiques.example/offre/42", garde).get_data(as_text=True)
                self.assertEqual(interne.requetes, [], "la redirection vers 127.0.0.1 a été suivie")
                self.assertEqual(garde.connexions_internes(), [])
                self.assertNotIn(SECRET, page)
                offres = self.q("SELECT title, description FROM stg_offers")
                self.assertFalse(any(SECRET in (o["title"] + o["description"]) for o in offres))

    def test_schemas_non_http_refuses(self):
        """Non-régression : file:// et gopher:// sont refusés avant toute connexion."""
        with GardeReseau() as garde:
            for url in ("file:///etc/passwd", "gopher://127.0.0.1:25/_HELO", "ftp://127.0.0.1/",
                        "dict://127.0.0.1:11211/"):
                with self.subTest(url=url):
                    self._poster(url, garde)
                    self.assertEqual(garde.connexions, [])
                    self.assertEqual(self.q("SELECT COUNT(*) AS n FROM stg_offers")[0]["n"], 0)


class RebindingDnsPdfTests(AppSecuTestCase):
    """F-02 : pdf_text() vérifie l'hôte par une première résolution, puis requests résout à nouveau."""

    PDF = b"%PDF-1.4\n% faux pdf interne\n"

    def _pdf_text(self, url):
        from backend.plugins.revision import sources
        with self.app.app_context():
            return sources.pdf_text(url)

    def test_rebinding_dns_ne_permet_pas_d_atteindre_la_boucle_locale(self):
        appels = {"n": 0}

        def rebind():
            appels["n"] += 1
            return [HOTE_PUBLIC_IP] if appels["n"] == 1 else ["127.0.0.1"]

        with ServeurLocal(defaut=(200, {"Content-Type": "application/pdf"}, self.PDF)) as interne:
            # On ne peut pas écouter sur le port 80 : 127.0.0.1:80 est détourné vers le serveur interne,
            # ce qui revient exactement à « un service interne écoute sur 127.0.0.1:80 ».
            garde = GardeReseau(dns={"rebind.example": rebind},
                                internes={("127.0.0.1", 80): ("127.0.0.1", interne.port)})
            with garde:
                try:
                    self._pdf_text("http://rebind.example/cours.pdf")
                except Exception:  # une erreur réseau est une réponse acceptable ; seule la connexion compte
                    pass
            self.assertEqual(interne.requetes, [], "le serveur interne a reçu la requête (rebinding DNS)")
            self.assertEqual(garde.connexions_internes(), [],
                             "connexion vers 127.0.0.1 après une première résolution publique")

    def test_adresses_privees_refusees_sans_connexion(self):
        """Non-régression du contrôle d'hôte existant."""
        with GardeReseau(dns={"interne.example": ["10.1.2.3"]}) as garde:
            for url in ("http://127.0.0.1/secret.pdf", "http://[::1]/x.pdf", "http://169.254.169.254/x.pdf",
                        "http://interne.example/x.pdf", "file:///etc/passwd", "http://2130706433/x.pdf"):
                with self.subTest(url=url):
                    self.assertEqual(self._pdf_text(url), "")
            self.assertEqual(garde.connexions_internes(), [])



# ---------------------------------------------------------------- vérification de la correction (manche 3)
from ._outils import ServeurBrut  # noqa: E402

ENTETE_HTML = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"


class VariantesSsrfTests(SsrfOffreDeStageTests):
    """Variantes de F-01 rejouées contre safe_http (via POST /stages/offres)."""

    # les tests de la classe mère ne sont pas rejoués ici (ils tournent déjà une fois)
    test_lien_vers_boucle_locale_refuse_sans_connexion = None
    test_adresses_privees_et_metadonnees_cloud_refusees = None
    test_redirection_d_un_hote_public_vers_la_boucle_locale = None
    test_schemas_non_http_refuses = None

    def _via_hote_public(self, repondre_ou_pages, chemin="/offre"):
        """POST /stages/offres vers un hôte « public » simulé par un serveur local. Renvoie (page, serveur, garde)."""
        serveur = (ServeurBrut(repondre_ou_pages) if callable(repondre_ou_pages)
                   else ServeurLocal(repondre_ou_pages))
        with serveur:
            detours = {(HOTE_PUBLIC_IP, 80): ("127.0.0.1", serveur.port)}
            with GardeReseau(dns={"offres-publiques.example": [HOTE_PUBLIC_IP]}, detours=detours) as g:
                page = self._poster("http://offres-publiques.example" + chemin, g).get_data(as_text=True)
        return page, serveur, g

    def test_dns_qui_melange_public_et_prive(self):
        with ServeurLocal(PAGES) as interne:
            with GardeReseau(dns={"mixte.example": [HOTE_PUBLIC_IP, "127.0.0.1"]},
                             internes={("127.0.0.1", 80): ("127.0.0.1", interne.port)}) as g:
                page = self._poster("http://mixte.example/admin", g, interne).get_data(as_text=True)
                self._verifier_refus("mixte.example", interne, g, page)

    def test_adresses_ipv6_qui_cachent_la_boucle_locale(self):
        for ip in ("::ffff:127.0.0.1", "2002:7f00:1::1", "64:ff9b::7f00:1", "2001:0:4136:e378:8000:63bf:80ff:fffe",
                   "::127.0.0.1", "fe80::1", "fc00::1"):
            with self.subTest(ip=ip), ServeurLocal(PAGES) as interne:
                with GardeReseau(dns={"v6.example": [ip]}) as g:
                    page = self._poster("http://v6.example/admin", g, interne).get_data(as_text=True)
                    self._verifier_refus(ip, interne, g, page)
                    self.assertEqual(g.connexions, [], f"{ip} : une connexion est partie")

    def test_point_final_et_majuscules_dans_l_url(self):
        with ServeurLocal(PAGES) as interne:
            with GardeReseau(internes={("127.0.0.1", 80): ("127.0.0.1", interne.port)}) as g:
                for url in ("http://localhost./admin", "HTTP://127.0.0.1/admin", "HtTp://LOCALHOST/admin",
                            "http://127.0.0.1.:80/admin", "http://[::1]:80/admin", "http://127.0.0.1:0080/admin"):
                    with self.subTest(url=url):
                        page = self._poster(url, g, interne).get_data(as_text=True)
                        self._verifier_refus(url, interne, g, page)

    def test_redirections_vers_identifiants_port_ou_schema_interdits(self):
        cibles = ["http://user:mdp@127.0.0.1/admin", "http://127.0.0.1:{port}/admin", "http://8.8.4.4:8080/admin",
                  "file:///etc/passwd", "http://localhost./admin", "HTTP://[::1]/admin", "//127.0.0.1/admin",
                  "http://0x7f000001/admin", "/../../admin"]
        with ServeurLocal(PAGES) as interne:
            for cible in cibles:
                cible = cible.format(port=interne.port)
                with self.subTest(redirection=cible), ServeurLocal(defaut=(302, {"Location": cible}, b"")) as public:
                    detours = {(HOTE_PUBLIC_IP, 80): ("127.0.0.1", public.port)}
                    with GardeReseau(dns={"offres-publiques.example": [HOTE_PUBLIC_IP]}, detours=detours,
                                     internes={("127.0.0.1", 80): ("127.0.0.1", interne.port)}) as g:
                        page = self._poster("http://offres-publiques.example/o", g, interne).get_data(as_text=True)
                    self.assertEqual(interne.requetes, [], f"redirection vers {cible} suivie")
                    self.assertEqual(g.connexions_internes(), [])
                    self.assertNotIn(("8.8.4.4", 8080), g.connexions, "connexion vers un port interdit")
                    self.assertNotIn(SECRET, page)

    def test_au_plus_3_redirections(self):
        """Chaîne de 4 redirections entre hôtes publics : la 4e n'est pas suivie."""
        pages = {f"/r{i}": (302, {"Location": f"http://offres-publiques.example/r{i + 1}"}, b"") for i in range(4)}
        pages["/r4"] = PAGES["/admin"]  # page finale, atteinte seulement après 4 redirections
        page, public, _ = self._via_hote_public(pages, "/r0")
        self.assertLessEqual(len(public.requetes), 4, public.requetes)
        self.assertNotIn("/r4", public.requetes, "4e redirection suivie")
        self.assertNotIn(SECRET, page)

    def test_reponse_de_6_mo_sans_content_length(self):
        def repondre(conn, arret):
            conn.sendall(ENTETE_HTML + b"<html><head><title>" + SECRET.encode() + b"</title></head><body>")
            bloc = b"x" * 65536
            for _ in range(6 * 16):  # 6 Mo
                if arret.is_set():
                    return
                conn.sendall(bloc)

        page, public, _ = self._via_hote_public(repondre, "/gros")
        self.assertEqual(public.connexions, 1)
        self.assertNotIn(SECRET, page, "réponse de plus de 5 Mo lue jusqu'au bout")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM stg_offers")[0]["n"], 0)

    def _goutte_a_goutte(self, debut: bytes):
        import time as _time

        def repondre(conn, arret):
            conn.sendall(debut)
            fin = _time.monotonic() + 16
            while _time.monotonic() < fin and not arret.is_set():
                conn.sendall(b"X")
                _time.sleep(0.5)

        t0 = _time.monotonic()
        self._via_hote_public(repondre, "/lent")
        return _time.monotonic() - t0

    def test_corps_au_compte_gouttes_limite_a_10_s_au_total(self):
        """F-028 : le délai n'est appliqué qu'à chaque lecture ; un serveur qui envoie un octet toutes les 0,5 s
        (jamais 10 s de silence) tient la requête sortante bien au-delà des 10 s au total."""
        duree = self._goutte_a_goutte(ENTETE_HTML + b"<html><head><title>" + SECRET.encode() + b"</title>")
        self.assertLess(duree, 12, f"requête sortante tenue {duree:.1f} s (limite : 10 s au total)")

    def test_entetes_au_compte_gouttes_limites_a_10_s_au_total(self):
        """F-028 (variante) : même attaque pendant l'envoi des en-têtes."""
        duree = self._goutte_a_goutte(b"HTTP/1.1 200 OK\r\nX-Lent: ")
        self.assertLess(duree, 12, f"requête sortante tenue {duree:.1f} s pendant les en-têtes")


class VariantesRebindingTests(RebindingDnsPdfTests):
    """Variantes de F-02 sur pdf_text (qui suit maintenant jusqu'à 3 redirections via safe_http)."""

    test_rebinding_dns_ne_permet_pas_d_atteindre_la_boucle_locale = None
    test_adresses_privees_refusees_sans_connexion = None

    def test_redirection_publique_vers_la_boucle_locale(self):
        with ServeurLocal(defaut=(200, {"Content-Type": "application/pdf"}, self.PDF)) as interne:
            with ServeurLocal(defaut=(302, {"Location": "http://127.0.0.1/cours.pdf"}, b"")) as public:
                g = GardeReseau(dns={"pdf.example": [HOTE_PUBLIC_IP]},
                                detours={(HOTE_PUBLIC_IP, 80): ("127.0.0.1", public.port)},
                                internes={("127.0.0.1", 80): ("127.0.0.1", interne.port)})
                with g:
                    try:
                        self._pdf_text("http://pdf.example/cours.pdf")
                    except Exception:  # une erreur réseau est acceptable ; seule la connexion compte
                        pass
            self.assertEqual(interne.requetes, [])
            self.assertEqual(g.connexions_internes(), [])

    def test_dns_mixte_et_rebinding_inverse(self):
        appels = {"n": 0}

        def inverse():
            appels["n"] += 1
            return ["127.0.0.1"] if appels["n"] == 1 else [HOTE_PUBLIC_IP]

        for nom, dns in (("mixte", {"x.example": [HOTE_PUBLIC_IP, "10.0.0.5"]}), ("inverse", {"x.example": inverse})):
            with self.subTest(dns=nom), ServeurLocal(defaut=(200, {}, self.PDF)) as interne:
                g = GardeReseau(dns=dns, internes={("127.0.0.1", 80): ("127.0.0.1", interne.port)})
                with g:
                    try:
                        self._pdf_text("http://x.example/c.pdf")
                    except Exception:
                        pass
                self.assertEqual(interne.requetes, [])
                self.assertEqual(g.connexions_internes(), [])



class VariantesDelaiTotalTests(VariantesSsrfTests):
    """Variantes de F-028 : délai total sur une chaîne de redirections lentes, goutte-à-goutte après redirection."""

    test_dns_qui_melange_public_et_prive = None
    test_adresses_ipv6_qui_cachent_la_boucle_locale = None
    test_point_final_et_majuscules_dans_l_url = None
    test_redirections_vers_identifiants_port_ou_schema_interdits = None
    test_au_plus_3_redirections = None
    test_reponse_de_6_mo_sans_content_length = None
    test_corps_au_compte_gouttes_limite_a_10_s_au_total = None
    test_entetes_au_compte_gouttes_limites_a_10_s_au_total = None

    def test_redirections_lentes_limitees_a_10_s_au_total(self):
        """3 redirections qui mettent chacune 4 s à répondre (aucune n'atteint 10 s seule) : 10 s au total."""
        import time as _time

        def repondre(conn, arret):
            _time.sleep(4)
            if not arret.is_set():
                conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://offres-publiques.example/suite\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")

        t0 = _time.monotonic()
        self._via_hote_public(repondre, "/lent")
        self.assertLess(_time.monotonic() - t0, 12)

    def test_goutte_a_goutte_apres_une_redirection(self):
        import time as _time
        etat = {"n": 0}

        def repondre(conn, arret):
            etat["n"] += 1
            if etat["n"] == 1:
                conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: /page\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            conn.sendall(ENTETE_HTML + b"<title>x</title>")
            fin = _time.monotonic() + 16
            while _time.monotonic() < fin and not arret.is_set():
                conn.sendall(b"X")
                _time.sleep(0.5)

        t0 = _time.monotonic()
        self._via_hote_public(repondre, "/r")
        self.assertLess(_time.monotonic() - t0, 12)


if __name__ == "__main__":
    unittest.main()
