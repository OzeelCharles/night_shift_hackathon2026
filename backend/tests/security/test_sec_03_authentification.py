"""SEC-03 — Authentification et sessions. Constats F-03 (pas de limite de tentatives) et F-04 (énumération).

Tout se passe dans le client de test Flask (aucune requête réseau). Les adresses IP sont simulées par REMOTE_ADDR.
"""
import re
import statistics
import time
import unittest
from datetime import timedelta
from unittest import mock

from ._outils import ADMIN_EMAIL, ADMIN_MDP, MOT_DE_PASSE, AppSecuTestCase

FAUX = "Mauvais-Mot-De-Passe-0"


def flashes(page: str) -> list[str]:
    """Messages affichés à l'utilisateur (bandeaux flash)."""
    return [re.sub(r"\s+", " ", m).strip() for m in re.findall(r'<div class="flash[^"]*"[^>]*>\s*<div>(.*?)</div>', page, re.S)]


class LimiteDeTentativesTests(AppSecuTestCase):
    """F-03 : 5 échecs par compte et par IP en 15 minutes, puis 429 ; 5 inscriptions par IP et par heure."""

    def setUp(self):
        super().setUp()
        self.inscrire("cible@test.fr")
        self.client.post("/deconnexion")

    def _connexion(self, email, password, ip="198.51.100.7", client=None):
        return (client or self.client).post("/connexion", data={"email": email, "password": password},
                                            environ_base={"REMOTE_ADDR": ip})

    def test_sixieme_echec_sur_un_compte_renvoie_429(self):
        for i in range(5):
            r = self._connexion("cible@test.fr", FAUX)
            self.assertNotEqual(r.status_code, 429, f"bloqué trop tôt (essai {i + 1})")
        r = self._connexion("cible@test.fr", FAUX)
        self.assertEqual(r.status_code, 429, "6e échec de connexion : 429 attendu")
        # Le blocage vaut aussi pour le bon mot de passe (sinon la force brute continue)
        r = self._connexion("cible@test.fr", MOT_DE_PASSE)
        self.assertEqual(r.status_code, 429)
        self.assertNotEqual(self.client.get("/tableau-de-bord").status_code, 200, "connecté malgré le blocage")

    def test_force_brute_d_un_compte_depuis_plusieurs_ip(self):
        """Limite par compte : changer d'IP à chaque essai ne contourne pas le blocage."""
        for i in range(5):
            self._connexion("cible@test.fr", FAUX, ip=f"198.51.100.{10 + i}")
        r = self._connexion("cible@test.fr", FAUX, ip="198.51.100.99")
        self.assertEqual(r.status_code, 429)

    def test_pulverisation_sur_plusieurs_comptes_depuis_une_ip(self):
        """Limite par IP : un mot de passe essayé sur 6 comptes différents depuis une même IP."""
        for i in range(5):
            self._connexion(f"victime{i}@test.fr", FAUX, ip="198.51.100.50")
        r = self._connexion("victime5@test.fr", FAUX, ip="198.51.100.50")
        self.assertEqual(r.status_code, 429)

    def test_mille_tentatives_ne_trouvent_pas_le_mot_de_passe(self):
        """Attaque du cahier : boucle de tentatives (bornée à 60 ici), le bon mot de passe en dernier."""
        for i in range(60):
            self._connexion("cible@test.fr", f"essai-{i:04d}")
        r = self._connexion("cible@test.fr", MOT_DE_PASSE)
        self.assertEqual(r.status_code, 429)

    def test_sixieme_inscription_depuis_une_ip_renvoie_429(self):
        statuts = []
        for i in range(6):
            c = self.app.test_client()
            r = c.post("/inscription", data={"email": f"bot{i}@test.fr", "username": f"Robot {i}",
                                             "password": MOT_DE_PASSE, "password2": MOT_DE_PASSE,
                                             "level": "licence", "school": "ENSAI"},
                       environ_base={"REMOTE_ADDR": "198.51.100.200"})
            statuts.append(r.status_code)
        self.assertNotIn(429, statuts[:5], "les 5 premières inscriptions doivent passer")
        self.assertEqual(statuts[5], 429, "6e inscription depuis la même IP dans l'heure : 429 attendu")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email LIKE 'bot%'")[0]["n"], 5)


class EnumerationDeComptesTests(AppSecuTestCase):
    """F-04 : messages et temps de réponse ne révèlent pas si un compte existe ou est désactivé."""

    def setUp(self):
        super().setUp()
        self.inscrire("existe@test.fr")
        self.inscrire("desactive@test.fr", client=self.app.test_client())
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        uid = self.q("SELECT id FROM users WHERE email = 'desactive@test.fr'")[0]["id"]
        self.client.post(f"/admin/utilisateurs/{uid}", data={"email": "desactive@test.fr",
                                                             "username": "Eleve desactive", "role": "user"})
        self.assertEqual(self.q("SELECT is_active FROM users WHERE id = ?", uid)[0]["is_active"], 0)
        self.client.post("/deconnexion")

    def _messages_connexion(self, email, password):
        c = self.app.test_client()  # client neuf : aucun message d'une requête précédente
        r = c.post("/connexion", data={"email": email, "password": password}, follow_redirects=True)
        self.assertNotEqual(c.get("/tableau-de-bord").status_code, 200, f"{email} : connexion acceptée")
        return r.status_code, flashes(r.get_data(as_text=True))

    def test_inscription_ne_confirme_pas_qu_une_adresse_existe(self):
        motif = r"(?i)existe déjà|déjà utilisée|déjà pris|déjà inscrit|already exists"
        for email in ("existe@test.fr", "EXISTE@Test.fr", " existe@test.fr "):  # variantes de casse et d'espaces
            with self.subTest(email=email):
                r = self.inscrire(email, client=self.app.test_client(), follow_redirects=True)
                messages = " | ".join(flashes(r.get_data(as_text=True)))
                self.assertNotRegex(messages, motif, "l'inscription révèle qu'un compte existe avec cette adresse")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE lower(trim(email)) = 'existe@test.fr'")[0]["n"],
                         1, "pas de second compte pour la même adresse")

    def test_compte_desactive_meme_message_qu_un_mauvais_mot_de_passe(self):
        inconnu = self._messages_connexion("personne@test.fr", MOT_DE_PASSE)
        mauvais = self._messages_connexion("existe@test.fr", FAUX)
        desactive = self._messages_connexion("desactive@test.fr", MOT_DE_PASSE)  # bon mot de passe
        self.assertEqual(inconnu, mauvais)
        self.assertEqual(desactive, inconnu, "le message révèle que le compte existe et que le mot de passe est bon")

    def _mediane_ms(self, email, password, n=5):
        """Médiane de n essais, chacun depuis une IP différente et sous la limite de 5 échecs par compte :
        on mesure le traitement normal d'un échec, pas une réponse 429."""
        temps = []
        for i in range(n):
            ip = f"203.0.113.{len(self._ips) + 1}"
            self._ips.append(ip)
            debut = time.perf_counter()
            r = self.client.post("/connexion", data={"email": email, "password": password},
                                 environ_base={"REMOTE_ADDR": ip})
            temps.append((time.perf_counter() - debut) * 1000)
            self.assertNotEqual(r.status_code, 429, "mesure faussée par un blocage")
        return statistics.median(temps)

    def test_meme_temps_de_reponse_compte_connu_ou_inconnu(self):
        """Cahier : même temps de réponse à ± 50 ms (médiane de 5 essais par cas)."""
        self._ips = []
        self._mediane_ms("chauffe@test.fr", FAUX, n=2)
        inconnu = self._mediane_ms("personne@test.fr", FAUX)
        mauvais = self._mediane_ms("existe@test.fr", FAUX)
        self.assertLess(abs(mauvais - inconnu), 50,
                        f"e-mail inconnu {inconnu:.1f} ms contre mot de passe faux {mauvais:.1f} ms")


class PolitiqueDeMotDePasseTests(AppSecuTestCase):
    """SEC-03 : 10 caractères minimum, mots de passe courants refusés, ni l'e-mail ni le nom."""

    FAIBLES = [
        ("court@test.fr", "Eleve court", "Abc12345!"),          # 9 caractères
        ("azerty@test.fr", "Eleve azerty", "azertyuiop"),       # très courant
        ("chiffres@test.fr", "Eleve chiffres", "1234567890"),
        ("password@test.fr", "Eleve password", "password123"),
        ("mdp@test.fr", "Eleve mdp", "motdepasse"),
        ("jean.dupont@test.fr", "Eleve dupont", "jean.dupont@test.fr"),  # égal à l'e-mail
        ("nom@test.fr", "Jean-Baptiste Durand", "Jean-Baptiste Durand"),  # égal au nom
    ]

    def test_mots_de_passe_faibles_refuses_a_l_inscription(self):
        for email, nom, mdp in self.FAIBLES:
            with self.subTest(mot_de_passe=mdp):
                self.inscrire(email, client=self.app.test_client(), username=nom, password=mdp)
                self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = ?", email)[0]["n"], 0,
                                 f"compte créé avec le mot de passe faible {mdp!r}")

    def test_mot_de_passe_fort_accepte(self):
        self.inscrire("fort@test.fr", password="Tisane-Velo-Granit-42")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = 'fort@test.fr'")[0]["n"], 1)


class SessionsTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]

    def _cookie(self, client):
        c = client.get_cookie(self.app.config.get("SESSION_COOKIE_NAME", "session"))
        return c.value if c else None

    def _client_avec_cookie(self, valeur):
        c = self.app.test_client()
        c.set_cookie(self.app.config.get("SESSION_COOKIE_NAME", "session"), valeur)
        return c

    def test_session_regeneree_a_la_connexion(self):
        """Non-régression : rien de la session d'avant la connexion ne survit (fixation de session)."""
        self.client.post("/deconnexion")
        with self.client.session_transaction() as s:
            s["fixe"] = "valeur-imposee"
            s["_csrf"] = "jeton-impose"
        self.connecter("eleve@test.fr")
        with self.client.session_transaction() as s:
            self.assertNotIn("fixe", s)
            self.assertNotEqual(s.get("_csrf"), "jeton-impose")
            self.assertEqual(s.get("user_id"), self.uid)

    def test_desactivation_invalide_la_session_existante(self):
        """Non-régression : un compte désactivé perd immédiatement l'accès avec son ancien cookie."""
        vole = self._cookie(self.client)
        admin = self.app.test_client()
        self.connecter(ADMIN_EMAIL, ADMIN_MDP, client=admin)
        admin.post(f"/admin/utilisateurs/{self.uid}", data={"email": "eleve@test.fr", "username": "Eleve eleve",
                                                            "role": "user"})
        r = self._client_avec_cookie(vole).get("/tableau-de-bord")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/connexion", r.headers["Location"])

    def test_changement_de_mot_de_passe_invalide_les_sessions_existantes(self):
        vole = self._cookie(self.client)
        self.assertIsNotNone(vole)
        admin = self.app.test_client()
        self.connecter(ADMIN_EMAIL, ADMIN_MDP, client=admin)
        admin.post(f"/admin/utilisateurs/{self.uid}", data={"email": "eleve@test.fr", "username": "Eleve eleve",
                                                            "role": "user", "is_active": "1",
                                                            "password": "Nouveau-Secret-Fort-99"})
        r = self._client_avec_cookie(vole).get("/tableau-de-bord")
        self.assertNotEqual(r.status_code, 200, "l'ancien cookie reste valide après le changement de mot de passe")

    def test_session_expire_apres_7_jours(self):
        vole = self._cookie(self.client)
        dans_8_jours = time.time() + timedelta(days=8).total_seconds()
        with mock.patch("time.time", return_value=dans_8_jours):
            r = self._client_avec_cookie(vole).get("/tableau-de-bord")
        self.assertNotEqual(r.status_code, 200, "cookie de session encore accepté 8 jours plus tard")



# ---------------------------------------------------------------- vérification des corrections (manche 3)
class VariantesLimiteTests(AppSecuTestCase):
    """Variantes de F-03 : repartir à zéro par la casse, par X-Forwarded-For, par une connexion réussie, en parallèle."""

    def setUp(self):
        super().setUp()
        self.inscrire("cible@test.fr")
        self.inscrire("attaquant@test.fr", client=self.app.test_client())
        self.client.post("/deconnexion")

    def _essai(self, email, password=FAUX, ip="198.51.100.7", entetes=None, client=None):
        return (client or self.client).post("/connexion", data={"email": email, "password": password},
                                            environ_base={"REMOTE_ADDR": ip}, headers=entetes or {})

    def test_casse_et_espaces_de_l_email_ne_remettent_pas_a_zero(self):
        for i, email in enumerate(["cible@test.fr", "CIBLE@test.fr", " cible@test.fr", "Cible@Test.FR ",
                                   "cible@TEST.fr"]):
            self._essai(email, ip=f"198.51.100.{20 + i}")
        r = self._essai("\tCIBLE@TEST.FR\n", ip="198.51.100.99")
        self.assertEqual(r.status_code, 429)

    def test_x_forwarded_for_ne_change_pas_l_ip(self):
        for i in range(5):
            self._essai(f"v{i}@test.fr", entetes={"X-Forwarded-For": f"203.0.113.{i}", "X-Real-IP": f"203.0.113.{i}"})
        r = self._essai("v9@test.fr", entetes={"X-Forwarded-For": "203.0.113.99", "X-Real-IP": "203.0.113.99"})
        self.assertEqual(r.status_code, 429)
        statuts = []
        for i in range(6):
            r = self.app.test_client().post("/inscription", data={
                "email": f"bot{i}@test.fr", "username": f"Robot {i}", "password": MOT_DE_PASSE,
                "password2": MOT_DE_PASSE, "level": "licence", "school": "ENSAI"},
                environ_base={"REMOTE_ADDR": "198.51.100.200"}, headers={"X-Forwarded-For": f"203.0.113.{i}"})
            statuts.append(r.status_code)
        self.assertEqual(statuts[5], 429, f"inscriptions avec X-Forwarded-For changeant : {statuts}")

    def test_connexion_reussie_au_milieu_ne_remet_pas_a_zero(self):
        for _ in range(4):
            self._essai("cible@test.fr")
        self.assertEqual(self._essai("attaquant@test.fr", MOT_DE_PASSE).status_code, 302)  # son propre compte
        self.client.post("/deconnexion")
        self._essai("cible@test.fr")  # 5e échec depuis cette IP
        self.assertEqual(self._essai("cible@test.fr").status_code, 429)
        self.assertEqual(self._essai("cible@test.fr", MOT_DE_PASSE, ip="198.51.100.8").status_code, 429,
                         "compte bloqué : même le bon mot de passe depuis une autre IP est refusé")

    def test_rafale_parallele_limitee_a_5_essais(self):
        """F-030 : 20 tentatives simultanées sur un compte (20 fils, local) : chacune lit le compteur avant que les
        autres l'aient incrémenté ; le nombre de mots de passe réellement essayés ne doit pas dépasser 5."""
        import threading
        statuts, verrou = [], threading.Lock()
        depart = threading.Barrier(20)

        def essai(i):
            c = self.app.test_client()
            depart.wait()
            r = c.post("/connexion", data={"email": "cible@test.fr", "password": f"essai-{i:02d}-xxxxxx"},
                       environ_base={"REMOTE_ADDR": f"198.51.100.{100 + i}"})
            with verrou:
                statuts.append(r.status_code)

        fils = [threading.Thread(target=essai, args=(i,)) for i in range(20)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(60)
        self.assertNotIn(500, statuts, f"erreurs serveur pendant la rafale : {statuts}")
        essayes = sum(1 for s in statuts if s != 429)
        self.assertLessEqual(essayes, 5, f"{essayes} mots de passe essayés au lieu de 5 au plus ({sorted(statuts)})")


class VariantesEnumerationTests(EnumerationDeComptesTests):
    """Variantes de F-04."""

    test_inscription_ne_confirme_pas_qu_une_adresse_existe = None
    test_compte_desactive_meme_message_qu_un_mauvais_mot_de_passe = None
    test_meme_temps_de_reponse_compte_connu_ou_inconnu = None

    def test_meme_temps_pour_un_compte_desactive(self):
        self._ips = []
        self._mediane_ms("chauffe@test.fr", FAUX, n=2)
        inconnu = self._mediane_ms("personne@test.fr", MOT_DE_PASSE)
        desactive_bon = self._mediane_ms("desactive@test.fr", MOT_DE_PASSE)
        self.assertLess(abs(desactive_bon - inconnu), 50, f"inconnu {inconnu:.1f} ms, désactivé {desactive_bon:.1f} ms")

    def test_inscription_en_majuscules_d_une_adresse_existante(self):
        for email in ("EXISTE@TEST.FR", "Existe@Test.Fr"):
            with self.subTest(email=email):
                r = self.inscrire(email, client=self.app.test_client(), follow_redirects=True)
                messages = " | ".join(flashes(r.get_data(as_text=True)))
                self.assertNotRegex(messages, r"(?i)existe déjà|déjà utilisée|déjà pris|déjà inscrit")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE lower(email) = 'existe@test.fr'")[0]["n"], 1)

    def test_blocage_identique_compte_connu_ou_inconnu(self):
        """Le message de blocage (429) ne distingue pas non plus un compte existant."""
        resultats = []
        for k, email in enumerate(("existe@test.fr", "personne@test.fr")):
            for i in range(6):
                r = self.app.test_client().post("/connexion", data={"email": email, "password": FAUX},
                                                environ_base={"REMOTE_ADDR": f"198.51.{k}.{i}"})
            resultats.append((r.status_code, flashes(r.get_data(as_text=True))))
        self.assertEqual(resultats[0], resultats[1])


class VariantesSessionsTests(SessionsTests):
    """Variantes de F-016."""

    test_session_regeneree_a_la_connexion = None
    test_desactivation_invalide_la_session_existante = None
    test_changement_de_mot_de_passe_invalide_les_sessions_existantes = None
    test_session_expire_apres_7_jours = None

    def _cookie_forge(self, contenu: dict) -> str:
        """Cookie signé avec la clé de TEST (celle d'une fuite hypothétique) : mesure ce que le serveur exige."""
        return self.app.session_interface.get_signing_serializer(self.app).dumps(contenu)

    def _admin(self):
        admin = self.app.test_client()
        self.connecter(ADMIN_EMAIL, ADMIN_MDP, client=admin)
        return admin

    def test_desactivation_puis_reactivation_ne_ressuscite_pas_l_ancien_cookie(self):
        vole = self._cookie(self.client)
        admin = self._admin()
        base = {"email": "eleve@test.fr", "username": "Eleve eleve", "role": "user"}
        admin.post(f"/admin/utilisateurs/{self.uid}", data=base)
        admin.post(f"/admin/utilisateurs/{self.uid}", data={**base, "is_active": "1"})
        self.assertEqual(self.q("SELECT is_active FROM users WHERE id = ?", self.uid)[0]["is_active"], 1)
        self.assertNotEqual(self._client_avec_cookie(vole).get("/tableau-de-bord").status_code, 200)

    def test_cookie_sans_date_ou_avec_une_date_invalide(self):
        sv = self.q("SELECT session_version FROM users WHERE id = ?", self.uid)[0]["session_version"]
        import time as _t
        for nom, contenu in (("sans auth_at", {"user_id": self.uid, "sv": sv}),
                             ("auth_at texte", {"user_id": self.uid, "sv": sv, "auth_at": str(int(_t.time()))}),
                             ("auth_at il y a 8 jours", {"user_id": self.uid, "sv": sv,
                                                         "auth_at": int(_t.time()) - 8 * 86400}),
                             ("sans sv", {"user_id": self.uid, "auth_at": int(_t.time())}),
                             ("sv faux", {"user_id": self.uid, "sv": sv + 1, "auth_at": int(_t.time())})):
            with self.subTest(cookie=nom):
                r = self._client_avec_cookie(self._cookie_forge(contenu)).get("/tableau-de-bord")
                self.assertNotEqual(r.status_code, 200, f"cookie « {nom} » accepté")

    def test_admin_qui_change_son_mot_de_passe_coupe_ses_autres_sessions(self):
        admin = self._admin()
        vole = self._cookie(admin)
        admin.post(f"/admin/utilisateurs/{self.q('SELECT id FROM users WHERE email = ?', ADMIN_EMAIL)[0]['id']}",
                   data={"email": ADMIN_EMAIL, "username": "Administrateur", "role": "admin", "is_active": "1",
                         "password": "Nouveau-Secret-Admin-88"})
        self.assertEqual(admin.get("/admin/").status_code, 200, "la session courante de l'admin continue")
        self.assertNotEqual(self._client_avec_cookie(vole).get("/admin/").status_code, 200)


class VariantesMotsDePasseTests(PolitiqueDeMotDePasseTests):
    """Variantes de F-015 : mots de passe courants déguisés que la normalisation actuelle ne ramène pas à la liste."""

    test_mots_de_passe_faibles_refuses_a_l_inscription = None
    test_mot_de_passe_fort_accepte = None

    def test_variantes_deja_couvertes(self):
        """Non-régression : majuscules, chiffres et symboles en bordure, substitutions classiques."""
        for i, mdp in enumerate(("AZERTYUIOP", "Azertyuiop2024!", "M0tD3P@sse!!", "qwerty123456", "iloveyou2024",
                                 "football2024")):
            with self.subTest(mot_de_passe=mdp):
                email = f"c{i}@test.fr"
                self.inscrire(email, client=self.app.test_client(), username="Eleve", password=mdp,
                              environ_base={"REMOTE_ADDR": f"192.0.2.{i + 1}"})
                self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = ?", email)[0]["n"], 0)

    def test_separateurs_dans_un_mot_de_passe_courant(self):
        """F-029 : « motdepasse », « azertyuiop », « password123 » sont dans la liste ; avec des espaces, tirets,
        points ou soulignés au milieu, ils sont acceptés."""
        for i, mdp in enumerate(("mot de passe", "mot-de-passe", "Mot_De_Passe", "m.o.t.d.e.p.a.s.s.e",
                                 "azerty uiop", "pass word 123", "1q2w 3e4r5t")):
            with self.subTest(mot_de_passe=mdp):
                email = f"s{i}@test.fr"
                self.inscrire(email, client=self.app.test_client(), username="Eleve", password=mdp,
                              environ_base={"REMOTE_ADDR": f"192.0.2.{50 + i}"})  # une IP par essai : pas de 429
                self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = ?", email)[0]["n"], 0,
                                 f"compte créé avec {mdp!r}")



class VariantesCorrectionsManche2Tests(AppSecuTestCase):
    """Variantes de F-029 (séparateurs) et F-030 (rafales parallèles)."""

    def test_autres_separateurs_et_combinaisons(self):
        for i, mdp in enumerate(("mot*de*passe", "mot/de/passe", "mot+de+passe", "M0t De P@sse!", "Pass_Word-123",
                                 "a.z.e.r.t.y.u.i.o.p", "1q2w 3e4r5t", "mot\tde\tpasse")):
            with self.subTest(mot_de_passe=mdp):
                email = f"m{i}@test.fr"
                self.inscrire(email, client=self.app.test_client(), username="Eleve", password=mdp,
                              environ_base={"REMOTE_ADDR": f"192.0.2.{100 + i}"})
                self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = ?", email)[0]["n"], 0)

    def test_separateurs_autres_que_espace_point_tiret(self):
        """F-036 (contournement de F-029) : seuls espaces, points, tirets et soulignés sont retirés de la forme comparée
        telle quelle ; la forme « sans aucun symbole » n'est testée qu'après retrait des chiffres en bordure. Une
        entrée de la liste qui commence ou finit par des chiffres, coupée par *, /, + ou #, passe."""
        for i, mdp in enumerate(("1234*5678*90", "1234/5678/90", "1q2w*3e4r*5t", "123456789+a", "1qaz#2wsx#3edc")):
            with self.subTest(mot_de_passe=mdp):
                email = f"d{i}@test.fr"
                self.inscrire(email, client=self.app.test_client(), username="Eleve", password=mdp,
                              environ_base={"REMOTE_ADDR": f"192.0.2.{150 + i}"})
                self.assertEqual(self.q("SELECT COUNT(*) AS n FROM users WHERE email = ?", email)[0]["n"], 0,
                                 f"compte créé avec {mdp!r}")

    def _rafale(self, faire):
        import threading
        statuts, verrou, depart = [], threading.Lock(), threading.Barrier(20)

        def fil(i):
            c = self.app.test_client()
            depart.wait()
            r = faire(c, i)
            with verrou:
                statuts.append(r.status_code)

        fils = [threading.Thread(target=fil, args=(i,)) for i in range(20)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(60)
        return statuts

    def test_rafale_d_inscriptions_depuis_une_ip(self):
        statuts = self._rafale(lambda c, i: c.post("/inscription", data={
            "email": f"bot{i}@test.fr", "username": f"Robot {i}", "password": MOT_DE_PASSE, "password2": MOT_DE_PASSE,
            "level": "licence", "school": "ENSAI"}, environ_base={"REMOTE_ADDR": "198.51.100.30"}))
        self.assertNotIn(500, statuts)
        crees = self.q("SELECT COUNT(*) AS n FROM users WHERE email LIKE 'bot%'")[0]["n"]
        self.assertLessEqual(crees, 5, f"{crees} comptes créés en rafale depuis une IP")

    def test_rafale_sur_plusieurs_comptes_depuis_une_ip(self):
        statuts = self._rafale(lambda c, i: c.post("/connexion", data={"email": f"v{i}@test.fr", "password": FAUX},
                                                   environ_base={"REMOTE_ADDR": "198.51.100.31"}))
        self.assertNotIn(500, statuts)
        self.assertLessEqual(sum(1 for s in statuts if s != 429), 5, sorted(statuts))


if __name__ == "__main__":
    unittest.main()
