"""SEC-02 — Contrôle d'accès et isolation entre utilisateurs (IDOR), constat F-11.

Deux comptes A et B. A possède une ligne de chaque type ; B rejoue toutes les routes à paramètre <int:...>
(générées depuis app.url_map) avec les identifiants de A, en GET et en POST. Attendu : 404 pour B et
aucune donnée de A lue ni modifiée. Les routes /admin/* répondent 403 à un utilisateur simple et
renvoient un visiteur vers la connexion.
"""
import io
import json
import re
import unittest
from datetime import date, timedelta

from ._outils import ADMIN_EMAIL, ADMIN_MDP, AppSecuTestCase

# Routes à <int:...> qui ne désignent pas une donnée d'utilisateur
HORS_PERIMETRE = {"campus.resto", "campus.resto_menu", "static",
                  # Forum de la Communauté : partagé entre tous les inscrits par conception. B lit le sujet de A,
                  # y répond et signale un message : c'est l'usage prévu, pas une fuite (le contenu est public).
                  "community.topic", "community.reply", "community.report_post",
                  # Suppression d'un message du forum par un autre que l'auteur : 403 et non 404, ce qui ne révèle
                  # rien (le message est public). Vérifié à part : le message de A existe toujours après l'essai de B.
                  "community.delete_post",
                  # /communaute/messages/<user_id> : <user_id> désigne l'interlocuteur, pas une donnée de A ; écrire à
                  # un autre membre est légitime. Vérifié à part : B ne lit ni ne modifie la conversation A <-> C.
                  "community.conversation"}
# Champs envoyés avec chaque POST de B : plausibles, pour ne pas être arrêté par une simple validation
FORMULAIRE = {"name": "PIRATE", "title": "PIRATE", "content": "PIRATE", "status": "refuse", "subject": "auto",
              "url": "https://exemple.fr/pirate", "difficulte": "moyen", "chapitre": "", "goal": "a_jour",
              "weekly_hours": "1", "comfort": "3", "topics": "PIRATE", "q0": "0", "date": "2030-01-01",
              "kind": "examen", "label": "PIRATE"}
MARQUEUR_A = "SECRET-DE-A"


class IsolationEntreUtilisateursTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.a = self._creer_donnees_de_a()
        self.client_b = self.app.test_client()
        self.inscrire("b@test.fr", client=self.client_b)
        self.b_id = self.q("SELECT id FROM users WHERE email = 'b@test.fr'")[0]["id"]

    # ------------------------------------------------------------ données de A
    def _creer_donnees_de_a(self) -> dict:
        c = self.client
        self.inscrire("a@test.fr")
        uid = self.q("SELECT id FROM users WHERE email = 'a@test.fr'")[0]["id"]
        cours = f"# Chapitre {MARQUEUR_A}\nLa loi normale est une loi continue.\n# Variance\nKönig-Huygens.\n"
        c.post("/matieres/nouvelle", data={"name": f"Matiere {MARQUEUR_A}", "goal": "a_jour", "weekly_hours": "3",
                                           "comfort": "3", "topics": "Chapitre\nVariance",
                                           "documents": (io.BytesIO(cours.encode()), "cours-a.md")},
               content_type="multipart/form-data")
        sid = self.q("SELECT id FROM subjects WHERE user_id = ?", uid)[0]["id"]
        jour = (date.today() + timedelta(days=3)).isoformat()
        c.post("/agenda/ajouter", data={"title": f"Cours {MARQUEUR_A}", "kind": "cours", "subject": str(sid),
                                        "date": jour, "start": "10:00", "end": "12:00", "repeat_weeks": "1"})
        c.post("/agenda/ajouter", data={"title": "Révision", "kind": "revision", "subject": str(sid),
                                        "date": jour, "start": "14:00", "end": "15:00", "repeat_weeks": "1"})
        c.post(f"/matieres/{sid}/ressources/ajouter", data={"title": f"Lien {MARQUEUR_A}",
                                                            "url": "https://exemple.fr/a"})
        c.post("/dossier/notes", data={"course": f"Matiere {MARQUEUR_A}", "subject_id": str(sid), "value": "12",
                                       "out_of": "20", "coefficient": "1", "label": "Partiel"})
        c.post("/cafeine/ajouter", data={"drink": "the"})
        c.post("/stages/postes", data={"title": f"Juriste {MARQUEUR_A}", "location": "Rennes"})
        c.post("/stages/offres", data={"title": f"Offre {MARQUEUR_A}", "description": "Texte de l'offre."})
        offer_id = self.q("SELECT id FROM stg_offers WHERE user_id = ?", uid)[0]["id"]
        c.post("/stages/lettres", data={"offer_id": str(offer_id)})
        with self.app.app_context():
            from backend.plugins.revision import store
            store.create_quiz(uid, sid, "Chapitre", "moyen", [
                {"type": "def", "chapter": "Chapitre", "prompt": f"Question {MARQUEUR_A}", "quote": "",
                 "options": ["a", "b"], "answer": 0, "explanation": ""}])
            store.add_exercise(sid, "Chapitre", f"Exo {MARQUEUR_A}", "énoncé", "solution", "moyen", "test", "",
                               "empreinte-a")
            store.add_notebook(sid, "Chapitre", "moyen", f"NB {MARQUEUR_A}", content=json.dumps({"cells": []}))
            from backend.dao import actions as actions_dao  # proposition de l'assistant, en attente
            from backend.services import reschedule_service
            actions_dao.create(uid, reschedule_service.KIND, {"note": MARQUEUR_A})
            from backend.dao.db import get_db
            get_db().commit()
        # Communauté : un tiers C ; sujet de forum de A (public), réponse de C signalée par A,
        # message privé de A à C avec une pièce jointe PDF (privé : B ne doit rien en voir)
        self.client_c = self.app.test_client()
        self.inscrire("c@test.fr", client=self.client_c)
        self.c_id = self.q("SELECT id FROM users WHERE email = 'c@test.fr'")[0]["id"]
        sujet = c.post("/communaute/forum/nouveau", data={"category": "libre", "title": "Sujet public de A",
                                                         "body": "Premier message de A"}).headers["Location"]
        self.client_c.post(sujet + "/repondre", data={"body": "Réponse de C"})
        self.c_post_id = self.q("SELECT id FROM forum_posts WHERE author_id = ?", self.c_id)[0]["id"]
        c.post(f"/communaute/forum/messages/{self.c_post_id}/signaler", data={"reason": "spam"})
        c.post(f"/communaute/messages/{self.c_id}", data={
            "body": f"Message privé {MARQUEUR_A}",
            "files": (io.BytesIO(f"%PDF-1.4 {MARQUEUR_A}".encode()), "prive-a.pdf")},
            content_type="multipart/form-data")

        def un(sql):
            rows = self.q(sql, uid)
            self.assertTrue(rows, f"donnée de A non créée : {sql}")
            return rows[0]["id"]

        ids = {
            "subject_id": sid,
            "doc_id": un("SELECT d.id FROM documents d JOIN subjects s ON s.id = d.subject_id WHERE s.user_id = ?"),
            "event_id": un("SELECT id FROM events WHERE user_id = ?"),
            "session_id": un("SELECT id FROM sessions WHERE user_id = ? ORDER BY manual DESC"),
            "resource_id": un("SELECT r.id FROM resources r JOIN subjects s ON s.id = r.subject_id WHERE s.user_id = ?"),
            "grade_id": un("SELECT id FROM grades WHERE user_id = ?"),
            "intake_id": un("SELECT id FROM caffeine_intakes WHERE user_id = ?"),
            "quiz_id": un("SELECT id FROM rev_quizzes WHERE user_id = ?"),
            "exercise_id": un("SELECT e.id FROM rev_exercises e JOIN subjects s ON s.id = e.subject_id "
                              "WHERE s.user_id = ?"),
            "notebook_id": un("SELECT n.id FROM rev_notebooks n JOIN subjects s ON s.id = n.subject_id "
                              "WHERE s.user_id = ?"),
            "wish_id": un("SELECT id FROM stg_wishes WHERE user_id = ?"),
            "offer_id": offer_id,
            "letter_id": un("SELECT id FROM stg_letters WHERE user_id = ?"),
            "action_id": un("SELECT id FROM chat_actions WHERE user_id = ?"),
            "topic_id": un("SELECT id FROM forum_topics WHERE author_id = ?"),
            "post_id": un("SELECT id FROM forum_posts WHERE author_id = ?"),
            "message_id": un("SELECT id FROM dm_messages WHERE sender_id = ?"),
            "file_id": un("SELECT id FROM shared_files WHERE owner_id = ? AND message_id IS NOT NULL"),
            "report_id": un("SELECT id FROM reports WHERE reporter_id = ?"),
            "user_id": uid,
        }
        self.client.post("/deconnexion")
        return ids

    def _instantane(self) -> dict:
        """Toutes les lignes de toutes les tables, sauf celles qui appartiennent à B."""
        tables = [r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type = 'table' "
                                            "AND name NOT LIKE 'sqlite_%'")]
        out = {}
        for t in tables:
            rows = self.q(f"SELECT * FROM {t}")  # noms issus de sqlite_master (test uniquement)
            out[t] = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows if r.get("user_id") != self.b_id)
        return out

    def _routes_a_identifiant(self):
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint in HORS_PERIMETRE or rule.rule.startswith("/admin"):
                continue
            args = [a for a in rule.arguments if rule._converters[a].__class__.__name__ == "IntegerConverter"]
            if args:
                yield rule, args

    # ------------------------------------------------------------ tests
    def test_couverture_de_toutes_les_routes_a_identifiant(self):
        """Chaque paramètre <int:...> du site a une donnée de A pour l'attaque (sinon la liste est à compléter)."""
        for rule, args in self._routes_a_identifiant():
            for a in args:
                self.assertIn(a, self.a, f"{rule.rule} : paramètre {a} non couvert par le test")

    def test_b_ne_lit_ni_ne_modifie_les_donnees_de_a(self):
        """Propriété de sécurité : aucune donnée de A ne fuit vers B, aucune n'est modifiée."""
        avant = self._instantane()
        for rule, args in self._routes_a_identifiant():
            url = rule.rule
            for a in args:
                url = re.sub(rf"<int:{a}>", str(self.a[a]), url)
            for methode in sorted(rule.methods & {"GET", "POST"}):
                with self.subTest(route=url, methode=methode):
                    if methode == "GET":
                        r = self.client_b.get(url, follow_redirects=True)
                    else:
                        r = self.client_b.post(url, data=dict(FORMULAIRE), follow_redirects=True)
                    self.assertNotEqual(r.status_code, 500, f"{methode} {url} : erreur serveur")
                    self.assertNotIn(MARQUEUR_A, r.get_data(as_text=True), f"{methode} {url} : donnée de A visible")
        apres = self._instantane()
        for table in avant:
            self.assertEqual(avant[table], apres.get(table), f"table {table} modifiée par B")

    def test_b_recoit_404_sur_chaque_route_a_identifiant(self):
        """Critère du cahier : 404 (et non une redirection « succès ») sur toute route visant une donnée d'autrui."""
        for rule, args in self._routes_a_identifiant():
            url = rule.rule
            for a in args:
                url = re.sub(rf"<int:{a}>", str(self.a[a]), url)
            for methode in sorted(rule.methods & {"GET", "POST"}):
                with self.subTest(route=url, methode=methode):
                    r = self.client_b.open(url, method=methode, data=dict(FORMULAIRE) if methode == "POST" else None)
                    self.assertEqual(r.status_code, 404, f"{methode} {url} : {r.status_code} au lieu de 404")

    def test_identifiants_de_a_dans_les_formulaires_de_b(self):
        """Identifiants de A glissés dans les champs de formulaires (pas dans l'URL)."""
        avant = self._instantane()
        jour = (date.today() + timedelta(days=4)).isoformat()
        essais = [
            ("/stages/lettres", {"offer_id": str(self.a["offer_id"])}),
            ("/stages/lettres", {"wish_id": str(self.a["wish_id"])}),
            ("/agenda/ajouter", {"title": "X", "kind": "cours", "subject": str(self.a["subject_id"]),
                                 "date": jour, "start": "08:00", "end": "09:00", "repeat_weeks": "1"}),
            ("/agenda/ajouter", {"title": "X", "kind": "revision", "subject": str(self.a["subject_id"]),
                                 "date": jour, "start": "08:00", "end": "09:00", "repeat_weeks": "1"}),
            ("/dossier/notes", {"course": "X", "subject_id": str(self.a["subject_id"]), "value": "10",
                                "out_of": "20", "coefficient": "1", "label": "X"}),
        ]
        for url, data in essais:
            with self.subTest(route=url, data=data):
                r = self.client_b.post(url, data=data, follow_redirects=True)
                self.assertNotEqual(r.status_code, 500)
                self.assertNotIn(MARQUEUR_A, r.get_data(as_text=True))
        for table in ("stg_letters", "events", "sessions", "grades"):
            rows = self.q(f"SELECT * FROM {table} WHERE user_id = ?", self.b_id)
            self.assertFalse(any(MARQUEUR_A in json.dumps(r, default=str) for r in rows),
                             f"{table} : B a obtenu une ligne qui reprend les données de A")
        # B ne peut pas non plus rattacher ses lignes à la matière de A
        for table in ("events", "sessions", "grades"):
            rows = self.q(f"SELECT subject_id FROM {table} WHERE user_id = ?", self.b_id)
            self.assertNotIn(self.a["subject_id"], [r["subject_id"] for r in rows], f"{table} rattachée à A")
        self.assertEqual(avant, self._instantane())

    def test_b_ne_supprime_pas_les_messages_du_forum_d_autrui(self):
        """Forum public (hors périmètre du 404) : B le lit, mais ne supprime ni le message de A ni celui de C."""
        self.assertEqual(self.client_b.get(f"/communaute/forum/{self.a['topic_id']}").status_code, 200,
                         "témoin : le forum est partagé entre inscrits")
        avant = self.q("SELECT * FROM forum_posts ORDER BY id"), self.q("SELECT * FROM forum_topics ORDER BY id")
        for post_id in (self.a["post_id"], self.c_post_id):
            with self.subTest(post_id=post_id):
                r = self.client_b.post(f"/communaute/forum/messages/{post_id}/supprimer", data=dict(FORMULAIRE))
                self.assertIn(r.status_code, (403, 404), f"suppression du message {post_id} : {r.status_code}")
        self.assertEqual(avant, (self.q("SELECT * FROM forum_posts ORDER BY id"),
                                 self.q("SELECT * FROM forum_topics ORDER BY id")), "B a supprimé un message d'autrui")

    def test_b_ne_lit_ni_ne_modifie_la_conversation_a_c(self):
        """/communaute/messages/<user_id> désigne l'interlocuteur : B peut écrire à A ou à C, jamais lire A <-> C."""
        prives = ("SELECT * FROM dm_messages WHERE id = ?", self.a["message_id"])
        avant = self.q(*prives), self.q("SELECT * FROM shared_files WHERE message_id IS NOT NULL ORDER BY id")
        for autre in (self.a["user_id"], self.c_id):
            with self.subTest(interlocuteur=autre):
                for r in (self.client_b.get(f"/communaute/messages/{autre}"),
                          self.client_b.post(f"/communaute/messages/{autre}", data={"body": "Bonjour"},
                                             follow_redirects=True),
                          self.client_b.get("/communaute/messages")):
                    self.assertEqual(r.status_code, 200)
                    page = r.get_data(as_text=True)
                    self.assertNotIn(MARQUEUR_A, page, "message privé de A à C visible par B")
                    self.assertNotIn("prive-a.pdf", page, "pièce jointe de A à C visible par B")
        self.assertEqual(avant[0], self.q(*prives), "message de A à C modifié (lu ?) par B")
        fichiers = self.q("SELECT * FROM shared_files WHERE message_id IS NOT NULL ORDER BY id")
        self.assertEqual(avant[1], [f for f in fichiers if f["owner_id"] != self.b_id])
        # les messages de B partent bien dans ses propres conversations, pas dans celle de A et C
        for m in self.q("SELECT sender_id, recipient_id FROM dm_messages WHERE id != ?", self.a["message_id"]):
            self.assertIn(self.b_id, (m["sender_id"], m["recipient_id"]))


class AccesAdministrationTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.inscrire("simple@test.fr")
        self.uid = self.q("SELECT id FROM users WHERE email = 'simple@test.fr'")[0]["id"]
        self.admin_id = self.q("SELECT id FROM users WHERE email = ?", ADMIN_EMAIL)[0]["id"]

    def _routes_admin(self):
        for rule in self.app.url_map.iter_rules():
            if rule.rule.startswith("/admin"):
                url = rule.rule
                for a in rule.arguments:
                    url = url.replace(f"<int:{a}>", str(self.admin_id if a == "user_id" else 1))
                for m in sorted(rule.methods & {"GET", "POST"}):
                    yield url, m

    def test_utilisateur_simple_recoit_403_sur_tout_admin(self):
        routes = list(self._routes_admin())
        self.assertGreaterEqual(len(routes), 7)
        avant = self.q("SELECT id, email, role, is_active FROM users ORDER BY id")
        for url, m in routes:
            with self.subTest(route=url, methode=m):
                data = {"email": "pirate@test.fr", "username": "pirate", "password": "Galet-Mouette-Corde-31",
                        "role": "admin", "is_active": "1"} if m == "POST" else None
                r = self.client.open(url, method=m, data=data)
                self.assertEqual(r.status_code, 403, f"{m} {url}")
        self.assertEqual(avant, self.q("SELECT id, email, role, is_active FROM users ORDER BY id"))

    def test_visiteur_renvoye_vers_la_connexion(self):
        self.client.post("/deconnexion")
        for url, m in self._routes_admin():
            with self.subTest(route=url, methode=m):
                r = self.client.open(url, method=m)
                self.assertEqual(r.status_code, 302, f"{m} {url}")
                self.assertIn("/connexion", r.headers["Location"])

    def test_admin_ne_peut_pas_se_supprimer_ni_retirer_le_dernier_admin(self):
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        self.client.post(f"/admin/utilisateurs/{self.admin_id}/supprimer")
        self.client.post(f"/admin/utilisateurs/{self.admin_id}",
                         data={"email": ADMIN_EMAIL, "username": "Administrateur", "role": "user", "is_active": "1"})
        self.client.post(f"/admin/utilisateurs/{self.admin_id}",
                         data={"email": ADMIN_EMAIL, "username": "Administrateur", "role": "admin"})
        u = self.q("SELECT role, is_active FROM users WHERE id = ?", self.admin_id)[0]
        self.assertEqual((u["role"], u["is_active"]), ("admin", 1))

    def test_admin_ne_peut_pas_se_retrograder_meme_avec_un_autre_admin(self):
        """Cahier SEC-02 : « Un admin ne peut ni se supprimer, ni se rétrograder » (pas seulement le dernier)."""
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        self.client.post("/admin/utilisateurs/nouveau", data={"email": "admin2@test.fr", "username": "Admin2",
                                                              "password": "Galet-Mouette-Corde-31", "role": "admin"})
        self.assertEqual(self.q("SELECT role FROM users WHERE email = 'admin2@test.fr'")[0]["role"], "admin")
        self.client.post(f"/admin/utilisateurs/{self.admin_id}",
                         data={"email": ADMIN_EMAIL, "username": "Administrateur", "role": "user", "is_active": "1"})
        self.client.post(f"/admin/utilisateurs/{self.admin_id}",
                         data={"email": ADMIN_EMAIL, "username": "Administrateur", "role": "admin"})
        u = self.q("SELECT role, is_active FROM users WHERE id = ?", self.admin_id)[0]
        self.assertEqual((u["role"], u["is_active"]), ("admin", 1), "l'admin a pu se rétrograder ou se désactiver")



# ---------------------------------------------------------------- vérification des corrections (manche 3)
class VariantesIsolationTests(IsolationEntreUtilisateursTests):
    """Variantes de F-11 : identifiants inexistants, donnée de A supprimée puis rejouée, téléchargements."""

    test_couverture_de_toutes_les_routes_a_identifiant = None
    test_b_ne_lit_ni_ne_modifie_les_donnees_de_a = None
    test_b_recoit_404_sur_chaque_route_a_identifiant = None
    test_identifiants_de_a_dans_les_formulaires_de_b = None
    test_b_ne_supprime_pas_les_messages_du_forum_d_autrui = None
    test_b_ne_lit_ni_ne_modifie_la_conversation_a_c = None

    def test_identifiant_inexistant_renvoie_404(self):
        for rule, args in self._routes_a_identifiant():
            url = rule.rule
            for a in args:
                url = re.sub(rf"<int:{a}>", "987654", url)
            for methode in sorted(rule.methods & {"GET", "POST"}):
                with self.subTest(route=url, methode=methode):
                    r = self.client_b.open(url, method=methode, data=dict(FORMULAIRE) if methode == "POST" else None)
                    self.assertEqual(r.status_code, 404, f"{methode} {url} : {r.status_code}")

    def test_telechargements_de_a_par_b(self):
        for url in (f"/documents/{self.a['doc_id']}?telecharger=1", f"/stages/lettres/{self.a['letter_id']}/telecharger",
                    f"/reviser/notebooks/{self.a['notebook_id']}.ipynb", f"/documents/{self.a['doc_id']}",
                    f"/communaute/fichiers/{self.a['file_id']}"):
            with self.subTest(url=url):
                r = self.client_b.get(url)
                self.assertEqual(r.status_code, 404)
                self.assertNotIn(MARQUEUR_A, r.get_data(as_text=True))

    def test_suppression_par_a_puis_rejeu_par_b(self):
        """B rejoue une suppression sur une ligne de A déjà supprimée par A : 404, pas de « succès »."""
        connecte = self.app.test_client()
        self.connecter("a@test.fr", client=connecte)
        connecte.post(f"/stages/postes/{self.a['wish_id']}/supprimer")
        r = self.client_b.post(f"/stages/postes/{self.a['wish_id']}/supprimer")
        self.assertEqual(r.status_code, 404)


class VariantesAdministrationTests(AccesAdministrationTests):
    """Variantes de F-014 : autres formulaires et valeurs pour se retirer ses propres droits."""

    test_utilisateur_simple_recoit_403_sur_tout_admin = None
    test_visiteur_renvoye_vers_la_connexion = None
    test_admin_ne_peut_pas_se_supprimer_ni_retirer_le_dernier_admin = None
    test_admin_ne_peut_pas_se_retrograder_meme_avec_un_autre_admin = None

    def test_auto_retrait_par_toutes_les_variantes(self):
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        self.client.post("/admin/utilisateurs/nouveau", data={"email": "admin2@test.fr", "username": "Admin2",
                                                              "password": "Galet-Mouette-Corde-31", "role": "admin"})
        base = {"email": ADMIN_EMAIL, "username": "Administrateur"}
        essais = [{"role": "user", "is_active": "1"}, {"role": "admin"}, {"role": "Admin", "is_active": "1"},
                  {"role": "ADMIN"}, {"is_active": "1"}, {"role": "admin", "is_active": ""},
                  {"role": ["admin", "user"], "is_active": "1"}]
        for extra in essais:
            with self.subTest(formulaire=str(extra)):
                self.client.post(f"/admin/utilisateurs/{self.admin_id}", data={**base, **extra})
                u = self.q("SELECT role, is_active FROM users WHERE id = ?", self.admin_id)[0]
                self.assertEqual((u["role"], u["is_active"]), ("admin", 1), f"auto-retrait accepté avec {extra}")
        self.client.post(f"/admin/utilisateurs/{self.admin_id}/supprimer")
        self.assertEqual(len(self.q("SELECT id FROM users WHERE id = ?", self.admin_id)), 1)

    def test_un_admin_peut_toujours_retrograder_un_autre_admin(self):
        """Témoin : la règle vise l'auto-retrait, pas la gestion des autres admins."""
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        self.client.post("/admin/utilisateurs/nouveau", data={"email": "admin2@test.fr", "username": "Admin2",
                                                              "password": "Galet-Mouette-Corde-31", "role": "admin"})
        a2 = self.q("SELECT id FROM users WHERE email = 'admin2@test.fr'")[0]["id"]
        self.client.post(f"/admin/utilisateurs/{a2}", data={"email": "admin2@test.fr", "username": "Admin2",
                                                            "role": "user", "is_active": "1"})
        self.assertEqual(self.q("SELECT role FROM users WHERE id = ?", a2)[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
