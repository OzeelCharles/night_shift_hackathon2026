"""Communauté : messages privés, pièces jointes, forum, droits d'accès."""
import io
import os
import tempfile
import unittest

from backend import create_app
from backend.business import community
from backend.dao.db import get_db


class BusinessTests(unittest.TestCase):
    def test_attachment_formats(self):
        self.assertTrue(community.allowed_attachment("poly.PDF"))
        self.assertTrue(community.allowed_attachment("photo.jpg"))
        self.assertFalse(community.allowed_attachment("page.html"))
        self.assertFalse(community.allowed_attachment("virus.exe"))
        self.assertFalse(community.allowed_attachment("sans_extension"))

    def test_topic_validation(self):
        self.assertEqual(community.validate_topic("exercices", "Intégrale impropre", "Je bloque"), [])
        self.assertTrue(community.validate_topic("inconnue", "Intégrale impropre", "x"))
        self.assertTrue(community.validate_topic("libre", "ab", "x"))
        self.assertTrue(community.validate_topic("libre", "Un titre correct", "   "))
        self.assertTrue(community.validate_body("a" * 5001))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_EMAIL"] = "admin@test.fr"
        os.environ["ADMIN_PASSWORD"] = "Admin-Rivezli-2026!"
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.clients = {}
        for name in ("lea", "tom", "eve"):
            c = self.app.test_client()
            c.post("/inscription", data={"email": f"{name}@test.fr", "username": name.capitalize(),
                                         "password": "Carnet-Revision-2026", "password2": "Carnet-Revision-2026",
                                         "level": "prepa", "school": "Lycée Test"})
            self.clients[name] = c
        with self.app.app_context():
            self.ids = {n: get_db().execute("SELECT id FROM users WHERE email = ?", (f"{n}@test.fr",)).fetchone()[0]
                        for n in self.clients}

    def tearDown(self):
        self.tmp.cleanup()


class CommunityTests(_Base):
    def test_pages_require_login(self):
        anon = self.app.test_client()
        for url in ("/communaute/forum", "/communaute/messages", "/communaute/forum/nouveau"):
            self.assertEqual(anon.get(url).status_code, 302, url)

    def test_private_message_with_file_and_privacy(self):
        lea, tom, eve = (self.clients[n] for n in ("lea", "tom", "eve"))
        r = lea.post(f"/communaute/messages/{self.ids['tom']}", data={
            "body": "Voici le poly de proba", "files": (io.BytesIO(b"%PDF-1.4 test"), "poly.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        inbox = tom.get("/communaute/messages").get_data(as_text=True)
        self.assertIn("1 nouveau", inbox, "message non lu signalé")
        page = tom.get(f"/communaute/messages/{self.ids['lea']}").get_data(as_text=True)
        self.assertIn("Voici le poly de proba", page)
        self.assertIn("poly.pdf", page)
        self.assertNotIn("1 nouveau", tom.get("/communaute/messages").get_data(as_text=True), "lu après ouverture")
        with self.app.app_context():
            fid = get_db().execute("SELECT id FROM shared_files").fetchone()[0]
        self.assertEqual(tom.get(f"/communaute/fichiers/{fid}").data, b"%PDF-1.4 test")
        self.assertEqual(lea.get(f"/communaute/fichiers/{fid}").status_code, 200)
        self.assertEqual(eve.get(f"/communaute/fichiers/{fid}").status_code, 404, "un tiers ne voit ni le fichier…")
        self.assertNotIn("Voici le poly", eve.get(f"/communaute/messages/{self.ids['lea']}").get_data(as_text=True),
                         "… ni la conversation")

    def test_message_rules(self):
        lea = self.clients["lea"]
        url = f"/communaute/messages/{self.ids['tom']}"
        lea.post(url, data={"body": "   "})
        lea.post(url, data={"body": "x", "files": (io.BytesIO(b"<html>"), "page.html")}, content_type="multipart/form-data")
        with self.app.app_context():
            n = get_db().execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
            files = get_db().execute("SELECT COUNT(*) FROM shared_files").fetchone()[0]
        self.assertEqual((n, files), (1, 0), "message vide refusé, fichier HTML refusé")
        self.assertEqual(lea.get(f"/communaute/messages/{self.ids['lea']}").status_code, 404, "pas de conversation avec soi")
        self.assertEqual(lea.get("/communaute/messages/9999").status_code, 404)

    def test_user_search_never_leaks_email(self):
        html = self.clients["lea"].get("/communaute/messages?q=tom").get_data(as_text=True)
        self.assertIn("Tom", html)
        self.assertNotIn("tom@test.fr", html)

    def test_share_own_course_document_only(self):
        lea, tom = self.clients["lea"], self.clients["tom"]
        lea.post("/matieres/nouvelle", data={"name": "Proba", "goal": "a_jour", "weekly_hours": "2", "comfort": "3", "topics": "A"})
        lea.post("/matieres/1/documents", data={"documents": (io.BytesIO(b"cours de proba"), "cours.txt")},
                 content_type="multipart/form-data")
        with self.app.app_context():
            doc = get_db().execute("SELECT id FROM documents").fetchone()[0]
        self.assertIn("cours.txt", lea.get(f"/communaute/messages/{self.ids['tom']}").get_data(as_text=True))
        tom.post(f"/communaute/messages/{self.ids['eve']}", data={"body": "je triche", "documents": str(doc)})
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM shared_files").fetchone()[0], 0,
                             "on ne partage pas le support d'un autre")
        lea.post(f"/communaute/messages/{self.ids['tom']}", data={"body": "", "documents": str(doc)})
        with self.app.app_context():
            fid = get_db().execute("SELECT id FROM shared_files").fetchone()[0]
        self.assertEqual(tom.get(f"/communaute/fichiers/{fid}").data, b"cours de proba")

    def test_forum_flow(self):
        lea, tom = self.clients["lea"], self.clients["tom"]
        r = lea.post("/communaute/forum/nouveau", data={"category": "concours", "title": "Comment aborder les oraux ?",
                                                        "body": "Des conseils pour les oraux de concours ?"})
        topic = r.headers["Location"]
        self.assertIn("Comment aborder les oraux ?", tom.get("/communaute/forum").get_data(as_text=True))
        self.assertIn("Comment aborder", tom.get("/communaute/forum?categorie=concours").get_data(as_text=True))
        self.assertNotIn("Comment aborder", tom.get("/communaute/forum?categorie=astuces").get_data(as_text=True))
        self.assertIn("Comment aborder", tom.get("/communaute/forum?q=oraux").get_data(as_text=True))
        tom.post(topic + "/repondre", data={"body": "Entraîne-toi à voix haute."})
        page = lea.get(topic).get_data(as_text=True)
        self.assertIn("Entraîne-toi à voix haute.", page)
        self.assertIn("1 réponse", tom.get("/communaute/forum").get_data(as_text=True))
        self.assertEqual(lea.post("/communaute/forum/nouveau", data={"category": "x", "title": "a", "body": ""}).status_code, 200,
                         "formulaire invalide : on reste sur la page")

    def test_forum_deletion_rights(self):
        lea, tom, eve = (self.clients[n] for n in ("lea", "tom", "eve"))
        topic = lea.post("/communaute/forum/nouveau", data={"category": "libre", "title": "Un sujet de test",
                                                            "body": "Premier message"}).headers["Location"]
        tom.post(topic + "/repondre", data={"body": "Réponse de Tom"})
        with self.app.app_context():
            first, second = [r[0] for r in get_db().execute("SELECT id FROM forum_posts ORDER BY id")]
        self.assertEqual(eve.post(f"/communaute/forum/messages/{second}/supprimer").status_code, 403)
        self.assertEqual(tom.post(f"/communaute/forum/messages/{first}/supprimer").status_code, 403)
        tom.post(f"/communaute/forum/messages/{second}/supprimer")
        self.assertNotIn("Réponse de Tom", lea.get(topic).get_data(as_text=True))
        lea.post(f"/communaute/forum/messages/{first}/supprimer")
        self.assertEqual(lea.get(topic).status_code, 404, "supprimer le premier message supprime la discussion")

    def test_admin_can_moderate_and_user_deletion_cleans_files(self):
        lea = self.clients["lea"]
        topic = lea.post("/communaute/forum/nouveau", data={
            "category": "libre", "title": "Sujet à modérer", "body": "contenu",
            "files": (io.BytesIO(b"abc"), "note.txt")}, content_type="multipart/form-data").headers["Location"]
        with self.app.app_context():
            path = get_db().execute("SELECT stored_path FROM shared_files").fetchone()[0]
        self.assertTrue(os.path.exists(path))
        admin = self.app.test_client()
        admin.post("/connexion", data={"email": "admin@test.fr", "password": "Admin-Rivezli-2026!"})
        admin.post(f"/admin/utilisateurs/{self.ids['lea']}/supprimer")
        self.assertFalse(os.path.exists(path), "les fichiers de l'utilisateur supprimé sont effacés")
        self.assertEqual(admin.get(topic).status_code, 404)

    def test_pages_render(self):
        lea = self.clients["lea"]
        self.assertEqual(lea.get("/communaute/", follow_redirects=True).status_code, 200)
        for url in ("/communaute/forum", "/communaute/forum/nouveau", "/communaute/messages",
                    f"/communaute/messages/{self.ids['tom']}"):
            self.assertEqual(lea.get(url).status_code, 200, url)
        self.assertIn(">Communauté</a>", lea.get("/communaute/forum").get_data(as_text=True))


class ModerationTests(_Base):

    def setUp(self):
        super().setUp()
        self.admin = self.app.test_client()
        self.admin.post("/connexion", data={"email": "admin@test.fr", "password": "Admin-Rivezli-2026!"})
        self.topic = self.clients["lea"].post("/communaute/forum/nouveau", data={
            "category": "libre", "title": "Sujet pour la modération", "body": "Un message désagréable"}).headers["Location"]
        with self.app.app_context():
            self.post_id = get_db().execute("SELECT id FROM forum_posts").fetchone()[0]

    def appoint(self, name, on=True):
        self.admin.post(f"/admin/utilisateurs/{self.ids[name]}/moderateur", data={"on": "1" if on else "0"})

    def report(self, name, reason="haine", details=""):
        return self.clients[name].post(f"/communaute/forum/messages/{self.post_id}/signaler",
                                       data={"reason": reason, "details": details})

    def rows(self, sql):
        with self.app.app_context():
            return get_db().execute(sql).fetchall()

    def first_report(self):
        return self.rows("SELECT id FROM reports ORDER BY id")[0][0]

    def test_only_admin_appoints_moderators(self):
        self.clients["tom"].post(f"/admin/utilisateurs/{self.ids['tom']}/moderateur", data={"on": "1"})
        self.assertEqual(self.rows("SELECT is_moderator FROM users WHERE id = %d" % self.ids["tom"])[0][0], 0)
        self.appoint("tom")
        self.assertEqual(self.rows("SELECT is_moderator FROM users WHERE id = %d" % self.ids["tom"])[0][0], 1)
        self.assertIn("modérateur", self.admin.get("/admin/").get_data(as_text=True))
        self.appoint("tom", on=False)
        self.assertEqual(self.rows("SELECT is_moderator FROM users WHERE id = %d" % self.ids["tom"])[0][0], 0)

    def test_moderation_page_is_staff_only(self):
        self.assertEqual(self.clients["tom"].get("/communaute/moderation").status_code, 404)
        self.report("eve")
        self.assertEqual(self.clients["tom"].post(f"/communaute/moderation/{self.first_report()}",
                                                  data={"decision": "remove"}).status_code, 404)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM reports WHERE status = 'open'")[0][0], 1, "rien n'a été décidé")
        self.assertNotIn(">Modération</a>", self.clients["tom"].get("/communaute/forum").get_data(as_text=True))
        self.assertEqual(self.admin.get("/communaute/moderation").status_code, 200)
        self.appoint("tom")
        self.assertEqual(self.clients["tom"].get("/communaute/moderation").status_code, 200)
        self.assertIn(">Modération</a>", self.clients["tom"].get("/communaute/forum").get_data(as_text=True))

    def test_report_validation_and_duplicates(self):
        self.assertIn("Choisis la raison", self.clients["tom"].post(
            f"/communaute/forum/messages/{self.post_id}/signaler", data={"reason": ""}, follow_redirects=True).get_data(as_text=True))
        self.report("tom", reason="autre")  # « autre » exige une explication
        self.assertEqual(len(self.rows("SELECT id FROM reports")), 0)
        self.report("lea")  # son propre message
        self.assertEqual(len(self.rows("SELECT id FROM reports")), 0)
        self.report("tom")
        self.report("tom")
        self.assertEqual(len(self.rows("SELECT id FROM reports")), 1, "pas de doublon")
        self.assertIn("Signalé", self.clients["tom"].get(self.topic).get_data(as_text=True))

    def test_reports_are_grouped_and_dismissed_together(self):
        self.report("tom")
        self.report("eve", reason="spam")
        page = self.admin.get("/communaute/moderation").get_data(as_text=True)
        self.assertIn("2 signalements", page)
        self.assertIn("Un message désagréable", page)
        self.assertEqual(page.count("com-report-card"), 1, "un seul élément pour le même message")
        self.admin.post(f"/communaute/moderation/{self.first_report()}", data={"decision": "dismiss"})
        self.assertEqual(self.rows("SELECT COUNT(*) FROM reports WHERE status = 'open'")[0][0], 0)
        self.assertEqual(self.clients["tom"].get(self.topic).status_code, 200, "le message est conservé")
        self.assertEqual(self.rows("SELECT is_active FROM users WHERE id = %d" % self.ids["lea"])[0][0], 1)

    def test_remove_keeps_evidence_and_mute_blocks_writing(self):
        self.appoint("tom")
        self.report("eve", reason="harcelement", details="menaces")
        self.clients["tom"].post(f"/communaute/moderation/{self.first_report()}",
                                 data={"decision": "remove_mute", "note": "récidive"})
        self.assertEqual(self.clients["eve"].get(self.topic).status_code, 404, "le message (et sa discussion) a disparu")
        r = self.rows("SELECT status, decision, content, note FROM reports")[0]
        self.assertEqual((r["status"], r["decision"], r["note"]), ("closed", "remove_mute", "récidive"))
        self.assertIn("Un message désagréable", r["content"], "la copie du contenu est conservée")
        self.assertEqual(self.rows("SELECT community_muted FROM users WHERE id = %d" % self.ids["lea"])[0][0], 1)
        lea = self.clients["lea"]
        lea.post("/communaute/forum/nouveau", data={"category": "libre", "title": "Je reviens quand même", "body": "x"})
        lea.post(f"/communaute/messages/{self.ids['eve']}", data={"body": "salut"})
        self.assertEqual(len(self.rows("SELECT id FROM forum_topics")), 0)
        self.assertEqual(len(self.rows("SELECT id FROM dm_messages")), 0)
        self.assertEqual(lea.get("/communaute/forum").status_code, 200, "la lecture reste possible")
        self.assertIn("suspendu", lea.get(f"/communaute/messages/{self.ids['eve']}").get_data(as_text=True))
        self.assertIn("lea", [u["username"].lower() for u in self.rows("SELECT username FROM users WHERE community_muted = 1")])
        self.clients["tom"].post(f"/communaute/moderation/membres/{self.ids['lea']}/retablir")
        lea.post(f"/communaute/messages/{self.ids['eve']}", data={"body": "re-bonjour"})
        self.assertEqual(len(self.rows("SELECT id FROM dm_messages")), 1)

    def test_author_deleting_does_not_hide_the_report(self):
        self.report("tom")
        self.clients["lea"].post(f"/communaute/forum/messages/{self.post_id}/supprimer")
        self.assertIn("Un message désagréable", self.admin.get("/communaute/moderation").get_data(as_text=True))

    def test_moderators_cannot_mute_the_team(self):
        self.appoint("tom")
        self.appoint("eve")
        self.clients["eve"].post("/communaute/forum/nouveau", data={"category": "libre", "title": "Message d'un modérateur", "body": "ok"})
        with self.app.app_context():
            post = get_db().execute("SELECT id FROM forum_posts WHERE author_id = ?", (self.ids["eve"],)).fetchone()[0]
        self.clients["lea"].post(f"/communaute/forum/messages/{post}/signaler", data={"reason": "spam"})
        page = self.clients["tom"].get("/communaute/moderation").get_data(as_text=True)
        self.assertNotIn('value="remove_mute"', page, "un modérateur ne suspend pas un autre modérateur")
        self.assertIn('value="remove_mute"', self.admin.get("/communaute/moderation").get_data(as_text=True))
        self.admin.post(f"/communaute/moderation/{self.first_report()}", data={"decision": "remove_mute"})
        self.assertEqual(self.rows("SELECT community_muted FROM users WHERE id = %d" % self.ids["eve"])[0][0], 1)

    def test_private_message_report_is_recipient_only(self):
        self.clients["lea"].post(f"/communaute/messages/{self.ids['tom']}", data={"body": "Message privé insultant"})
        with self.app.app_context():
            mid = get_db().execute("SELECT id FROM dm_messages").fetchone()[0]
        url = f"/communaute/messages/signaler/{mid}"
        self.assertEqual(self.clients["lea"].post(url, data={"reason": "haine"}).status_code, 404, "l'expéditeur ne signale pas")
        self.assertEqual(self.clients["eve"].post(url, data={"reason": "haine"}).status_code, 404, "un tiers non plus")
        self.assertIn("Signaler", self.clients["tom"].get(f"/communaute/messages/{self.ids['lea']}").get_data(as_text=True))
        self.clients["tom"].post(url, data={"reason": "harcelement"})
        self.assertIn("Message privé insultant", self.admin.get("/communaute/moderation").get_data(as_text=True))
        self.admin.post(f"/communaute/moderation/{self.first_report()}", data={"decision": "remove"})
        self.assertEqual(len(self.rows("SELECT id FROM dm_messages")), 0)

    def test_moderator_can_delete_forum_posts_and_rules_page(self):
        self.assertEqual(self.clients["tom"].post(f"/communaute/forum/messages/{self.post_id}/supprimer").status_code, 403)
        self.appoint("tom")
        self.clients["tom"].post(f"/communaute/forum/messages/{self.post_id}/supprimer")
        self.assertEqual(self.clients["eve"].get(self.topic).status_code, 404)
        self.assertIn("Aucune haine", self.clients["eve"].get("/communaute/regles").get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
