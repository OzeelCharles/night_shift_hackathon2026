"""Recherche de stage : postes, liens de recherche, offres suivies, lettres de motivation (Groq simulé)."""
import os
import tempfile
import unittest
from unittest import mock

from backend import create_app
from backend.dao.db import get_db
from backend.plugins.stages import letters, service, sources

JOB_PAGE = """<html><head><title>Offre</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "JobPosting",
 "title": "Stage - Assistant juridique (F/H)", "description": "<p>Rédaction d'actes.</p><ul><li>Veille</li></ul>",
 "hiringOrganization": {"@type": "Organization", "name": "Cabinet Martin"},
 "jobLocation": {"@type": "Place", "address": {"addressLocality": "Lyon"}}}</script></head></html>"""


class SourcesTests(unittest.TestCase):
    def test_links_for_every_site(self):
        links = sources.search_links("Infirmier en pédiatrie", "Lille")
        sites = [l["site"] for l in links]
        for site in ("Indeed", "LinkedIn", "Glassdoor"):
            self.assertIn(site, sites)
        indeed = links[0]["url"]
        self.assertIn("q=stage+Infirmier+en+p%C3%A9diatrie", indeed)
        self.assertIn("l=Lille", indeed)
        self.assertEqual(sources.query_for("Alternance développeur web"), "Alternance développeur web")

    def test_parse_job_posting(self):
        offer = sources.parse_offer(JOB_PAGE)
        self.assertEqual(offer["title"], "Stage - Assistant juridique (F/H)")
        self.assertEqual((offer["company"], offer["location"]), ("Cabinet Martin", "Lyon"))
        self.assertIn("Rédaction d'actes.\nVeille", offer["description"])
        self.assertEqual(sources.site_name("https://fr.linkedin.com/jobs/view/1"), "LinkedIn")

    def test_fix_mojibake(self):
        good = "Stage d’été – Professionnel de recherche, Économie de la santé · « Montréal » 5 € ™"
        once = good.encode("utf-8").decode("latin-1")                          # Latin-1 (requests sans charset)
        cp = good.encode("utf-8").decode("cp1252", errors="replace")           # Windows-1252
        twice = once.encode("utf-8").decode("latin-1")                         # encodé deux fois
        self.assertEqual(sources.fix_mojibake(once), good)
        self.assertEqual(sources.fix_mojibake(twice), good)
        self.assertIn("Économie de la santé", sources.fix_mojibake(cp))
        for ok in (good, "Ça coûte 5 € à Montréal", "Señor Müller, naïve, œuvre", "", "ASCII only"):
            self.assertEqual(sources.fix_mojibake(ok), ok, "un texte correct n'est jamais modifié")

    def test_decode_page(self):
        page = "<html><head><title>Économie</title></head></html>"
        self.assertEqual(sources.decode_page(page.encode("utf-8"), "text/html"), page, "sans charset : UTF-8")
        self.assertEqual(sources.decode_page(page.encode("cp1252"), "text/html; charset=windows-1252"), page)
        meta = '<meta charset="iso-8859-1"><p>Économie</p>'
        self.assertEqual(sources.decode_page(meta.encode("latin-1"), "text/html"), meta, "charset de la balise meta")


class StagesRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")})
        self.client = self.app.test_client()
        self.client.post("/inscription", data={"email": "e@test.fr", "username": "Léa", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "licence", "school": "Université Lyon 3"})

    def tearDown(self):
        self.tmp.cleanup()

    def account(self, **details):
        data = {"username": "Léa", "level": "licence", "school": "Université Lyon 3", "full_name": "Léa Martin",
                "phone": "06 12 34 56 78", **details}
        self.client.post("/profil", data=data)

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def test_space_wishes_and_offers(self):
        page = self.client.get("/stages").get_data(as_text=True)
        self.assertIn('aria-current="page">Stages</a>', page, "espace dans la barre du haut")
        self.client.post("/stages/postes", data={"title": "Assistant juridique", "location": "Lyon"})
        self.client.post("/stages/postes", data={"title": "assistant juridique", "location": "lyon"})
        self.assertEqual(len(self.q("SELECT id FROM stg_wishes")), 1, "pas de doublon")
        page = self.client.get("/stages").get_data(as_text=True)
        self.assertIn("https://fr.indeed.com/jobs?q=stage+Assistant+juridique&amp;l=Lyon", page)
        self.assertIn("linkedin.com/jobs/search", page)

        r = self.client.post("/stages/offres", data={"url": "https://www.linkedin.com/jobs/view/42"},
                             follow_redirects=True)
        self.assertIn("recopie au moins", r.get_data(as_text=True), "sans lecture possible, l'intitulé est demandé")
        self.client.post("/stages/offres", data={"url": "https://www.linkedin.com/jobs/view/42", "title": "Juriste",
                                                 "company": "Cabinet Martin"})
        offer = self.q("SELECT * FROM stg_offers")[0]
        self.assertEqual(offer["source"], "LinkedIn")
        self.client.post(f"/stages/offres/{offer['id']}/statut", data={"status": "postule"})
        self.assertEqual(self.q("SELECT status FROM stg_offers")[0]["status"], "postule")

    def test_offer_read_from_link(self):
        self.app.config["ENABLE_WEB"] = True
        with mock.patch.object(service, "_fetch", wraps=service._fetch) as fetch, \
                mock.patch("backend.dao.web.secure_session") as session:
            session.return_value.get.return_value = mock.Mock(
                status_code=200, content=JOB_PAGE.encode("utf-8"),
                headers={"Content-Type": "text/html; charset=utf-8"}, apparent_encoding="utf-8")
            self.client.post("/stages/offres", data={"url": "https://www.welcometothejungle.com/fr/jobs/1"})
            fetch.assert_called_once()
        offer = self.q("SELECT * FROM stg_offers")[0]
        self.assertEqual((offer["title"], offer["company"], offer["location"]),
                         ("Stage - Assistant juridique (F/H)", "Cabinet Martin", "Lyon"))

    def test_offer_page_without_charset_is_readable(self):
        """Page UTF-8 servie sans charset (cas d'iCIMS) : avant, l'intitulé devenait « Stage dâ€™Ã©tÃ©… »."""
        self.app.config["ENABLE_WEB"] = True
        page = JOB_PAGE.replace("Stage - Assistant juridique (F/H)",
                                "Stage d’été – Professionnel de recherche, Économie de la santé")
        page = page.replace("Lyon", "Montréal")
        with mock.patch("backend.dao.web.secure_session") as session:
            session.return_value.get.return_value = mock.Mock(
                status_code=200, content=page.encode("utf-8"), headers={"Content-Type": "text/html"},
                apparent_encoding="ascii")
            self.client.post("/stages/offres", data={"url": "https://careers-analysisgroup.icims.com/jobs/1/job"})
        offer = self.q("SELECT title, location FROM stg_offers")[0]
        self.assertEqual(offer["title"], "Stage d’été – Professionnel de recherche, Économie de la santé")
        self.assertEqual(offer["location"], "Montréal")

    def test_saved_garbled_offers_are_repaired_at_startup(self):
        good = "Stage d’été – Économie de la santé à Montréal"
        garbled = good.encode("utf-8").decode("latin-1")      # ce qu'enregistrait l'ancienne version
        uid = self.q("SELECT id FROM users WHERE email = 'e@test.fr'")[0]["id"]
        with self.app.app_context():
            get_db().execute("INSERT INTO stg_offers (user_id, title, company) VALUES (?, ?, ?)",
                             (uid, garbled, "Café Crème"))
            get_db().execute("INSERT INTO stg_letters (user_id, title, content) VALUES (?, ?, ?)",
                             (uid, garbled, "Objet : " + garbled))
            get_db().commit()
        again = create_app({"TESTING": True, "ENABLE_WEB": False, "DATABASE": self.app.config["DATABASE"],
                            "UPLOAD_FOLDER": self.app.config["UPLOAD_FOLDER"]})
        with again.app_context():
            offer = dict(get_db().execute("SELECT title, company FROM stg_offers").fetchone())
            letter = dict(get_db().execute("SELECT title, content FROM stg_letters").fetchone())
        self.assertEqual(offer, {"title": good, "company": "Café Crème"}, "le texte correct reste identique")
        self.assertEqual(letter, {"title": good, "content": "Objet : " + good})

    def test_template_letter_without_ai(self):
        self.account(field="Droit", program="Licence 3 de droit privé", home_city="Lyon", driving_license="oui",
                     languages="français, anglais (B2)")
        self.client.post("/stages/profil", data={"skills": "rédaction juridique, recherche documentaire",
                                                 "duration": "2 mois", "start": "juin 2027"})
        self.client.post("/stages/offres", data={"title": "Assistant juridique", "company": "Cabinet Martin"})
        oid = self.q("SELECT id FROM stg_offers")[0]["id"]
        r = self.client.post("/stages/lettres", data={"offer_id": oid})
        letter = self.q("SELECT * FROM stg_letters")[0]
        self.assertTrue(r.headers["Location"].endswith(f"/stages/lettres/{letter['id']}"))
        self.assertEqual(letter["author"], "modèle")
        for part in ("Licence 3 de droit privé", "Université Lyon 3", "Cabinet Martin", "rédaction juridique",
                     "à partir de juin 2027, pour une durée de 2 mois", "un stage en droit", "[métier visé]",
                     "Je parle français, anglais (B2).", "permis B", "À Lyon, le ", "Objet : Stage - Assistant juridique",
                     "Léa Martin\nLyon\n06 12 34 56 78, e@test.fr", "salutations distinguées.\n\nLéa Martin"):
            self.assertIn(part, letter["content"])
        self.client.post(f"/stages/lettres/{letter['id']}", data={"content": "Ma lettre retouchée."})
        dl = self.client.get(f"/stages/lettres/{letter['id']}/telecharger")
        self.assertEqual(dl.get_data(as_text=True), "Ma lettre retouchée.")
        self.assertIn("lettre-motivation-cabinet-martin.txt", dl.headers["Content-Disposition"])

    def test_groq_letter_any_field(self):
        self.app.config.update(STAGES_AI=True, GROQ_API_KEY="gsk_test")
        self.account(field="Soins infirmiers", program="IFSI 2e année", birth_date="2004-03-01")
        self.client.post("/stages/postes", data={"title": "Infirmier en pédiatrie"})
        wid = self.q("SELECT id FROM stg_wishes")[0]["id"]
        with mock.patch.object(letters, "write", return_value="Madame, Monsieur, " + "x" * 300) as write:
            self.client.post("/stages/lettres", data={"wish_id": wid, "tone": "dynamique", "language": "en"})
        profile, user, target, tone, language = write.call_args.args
        self.assertEqual((user["field"], target["title"], tone, language),
                         ("Soins infirmiers", "Infirmier en pédiatrie", "dynamique", "en"))
        self.assertEqual(self.q("SELECT author FROM stg_letters")[0]["author"], "Groq")
        with self.app.test_request_context():
            text = letters.prompt(profile, user, target, "sobre", "fr")
        self.assertIn("Domaine d'études : Soins infirmiers", text)
        self.assertIn("Niveau d'études : Licence", text)
        self.assertIn("Formation : IFSI 2e année", text)
        self.assertRegex(text, r"Âge : \d\d ans")

    def test_groq_failure_falls_back_to_template(self):
        self.app.config.update(STAGES_AI=True, GROQ_API_KEY="gsk_test")
        with mock.patch.object(letters, "write", side_effect=letters.LetterError("quota de Groq atteint")):
            page = self.client.post("/stages/lettres", data={"title": "Graphiste"}, follow_redirects=True)
        self.assertIn("quota de Groq atteint", page.get_data(as_text=True))
        self.assertEqual(self.q("SELECT author FROM stg_letters")[0]["author"], "modèle")

    def test_account_prefills_stage_page(self):
        self.account(home_city="Villeurbanne", field="Informatique")
        page = self.client.get("/stages").get_data(as_text=True)
        self.assertIn('value="Villeurbanne"', page, "le lieu recherché est prérempli avec la ville du compte")
        self.assertIn("Léa Martin", page)
        self.assertIn(">Python</button>", page, "compétences suggérées d'après le domaine")
        self.assertIn("ta formation", page, "ce qui manque au compte est signalé")

    def test_stages_hidden_when_not_looking(self):
        self.assertIn(">Stages</a>", self.client.get("/tableau-de-bord").get_data(as_text=True))
        self.account(wants_internship="non")
        self.assertNotIn(">Stages</a>", self.client.get("/tableau-de-bord").get_data(as_text=True))
        self.assertEqual(self.client.get("/stages").status_code, 200, "la page reste accessible")

    def test_other_users_letters_are_private(self):
        self.client.post("/stages/lettres", data={"title": "Graphiste"})
        lid = self.q("SELECT id FROM stg_letters")[0]["id"]
        self.client.post("/deconnexion")
        self.client.post("/inscription", data={"email": "b@test.fr", "username": "Bobby", "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})
        self.assertEqual(self.client.get(f"/stages/lettres/{lid}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
