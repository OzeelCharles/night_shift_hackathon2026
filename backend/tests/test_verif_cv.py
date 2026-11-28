"""Vérificateur ATS de CV (plugin verif_cv) : règles pures, dépôt et vie privée, intégration à « Stages »,
conseils de Groq simulés et consentis, sécurité."""
import ast
import io
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from backend import config, create_app
from backend.dao import groq
from backend.dao.db import get_db
from backend.plugins.verif_cv import verif_cv_rules as rules

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "verif_cv"

OFFER_TITLE = "Stage Data Analyst"
OFFER = """Nous recherchons un stagiaire data analyst pour rejoindre notre équipe.
Missions : analyse de données clients, création de tableaux de bord sous Power BI, requêtes SQL,
automatisation de rapports en Python. Gestion de projet avec les équipes marketing.
Profil : maîtrise de SQL et Python, Excel avancé, anglais courant. Analyse de données et gestion de projet appréciées."""

CLEAN_CV = """Léa Martin
lea.martin@example.fr — 06 12 34 56 78 — Lyon

FORMATION
2024 - 2026 : Master statistique et science des données, Université Lyon 2. Cours de statistiques, d'économétrie, d'apprentissage automatique et de visualisation de données, avec plusieurs projets en équipe.
2021 - 2024 : Licence de mathématiques appliquées, Université Lyon 1, mention bien.

EXPÉRIENCES PROFESSIONNELLES
Stage d'analyste de données, Banque Rhône, été 2025 : préparation de tableaux de bord Power BI pour le service marketing, écriture de requêtes SQL sur la base clients, automatisation d'un rapport mensuel en Python qui faisait gagner une demi-journée par mois à l'équipe.
Job étudiant, médiathèque municipale, 2022 - 2024 : accueil du public, classement, animation d'ateliers numériques pour les seniors le samedi matin.

COMPÉTENCES
Python (pandas, scikit-learn), SQL, Excel avancé, Power BI, Git, gestion de projet en méthode agile, rédaction de rapports et présentation de résultats à des équipes non techniques.

LANGUES
Anglais courant (C1, TOEIC 945), espagnol intermédiaire (B1).

CENTRES D'INTÉRÊT
Course à pied, bénévolat dans une association d'aide aux devoirs, photographie argentique."""


def without_line(text, start):
    """Le CV propre, sans la rubrique qui commence par `start` (titre et ligne suivante)."""
    lines = text.splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith(start))
    return "\n".join(lines[:i] + lines[i + 2:])


def make_docx(text: str, table: list[list[str]] | None = None) -> bytes:
    import docx
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def make_blank_pdf() -> bytes:
    """Un PDF sans texte, comme un CV scanné."""
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


# ================================================================ règles pures
class RulesTests(unittest.TestCase):
    def test_rules_are_pure(self):
        forbidden = ("flask", "sqlite3", "requests", "backend.dao", "backend.core", "backend.services",
                     "backend.controllers")
        tree = ast.parse((PLUGIN_DIR / "verif_cv_rules.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                self.assertFalse(name.startswith(forbidden), f"verif_cv_rules importe {name}")

    def test_match_is_deterministic_and_bounded(self):
        a = rules.match(CLEAN_CV, OFFER, OFFER_TITLE)
        b = rules.match(CLEAN_CV, OFFER, OFFER_TITLE)
        self.assertEqual(a, b)
        self.assertIsInstance(a.score, int)
        self.assertTrue(0 <= a.score <= 100)
        self.assertEqual(sorted(a.found + a.missing), sorted(a.keywords), "chaque mot-clé est trouvé ou manquant")
        for k in ("Power BI", "SQL", "Python", "gestion de projet"):
            self.assertIn(k, a.keywords)
        self.assertIn("SQL", a.found)
        self.assertIn("data analyst", a.missing)
        self.assertTrue(a.reliable)

    def test_accents_and_case_are_ignored(self):
        offer = "Gestion de projet, gestion de projet et analyse de données. Excel et SQL indispensables. Anglais."
        found = rules.match("GESTION DE PROJET ; Analyse des donnees ; excel ; sql ; ANGLAIS", offer)
        self.assertIn("gestion de projet", found.found)
        self.assertIn("Excel", found.found)
        self.assertEqual(rules.normalize("Expériences Professionnelles"), "experiences professionnelles")

    def test_cv_with_every_keyword_scores_100(self):
        keywords = rules.extract_keywords(OFFER, OFFER_TITLE)
        self.assertGreaterEqual(len(keywords), 5)
        self.assertEqual(rules.match(" ; ".join(keywords).upper(), OFFER, OFFER_TITLE).score, 100)
        self.assertEqual(rules.match("", OFFER, OFFER_TITLE).score, 0)

    def test_title_only_offer_is_not_reliable(self):
        m = rules.match(CLEAN_CV, "", "Assistant juridique")
        self.assertFalse(m.reliable)
        self.assertIn("Ajoute le texte de l'offre pour un score fiable", " ".join(rules.match_advice(m)))
        m = rules.match("Python", OFFER, OFFER_TITLE)
        self.assertIn("Ajoute-les seulement si tu as vraiment cette compétence", " ".join(rules.match_advice(m)))

    def codes(self, *args, **kw):
        return [a["code"] for a in rules.ats_alerts(*args, **kw)]

    def test_clean_cv_has_no_alert(self):
        self.assertEqual(rules.ats_alerts(CLEAN_CV, 1, 0), [])

    def test_scanned_or_empty_text(self):
        self.assertEqual(self.codes("", 1), ["extraction"])
        self.assertIn("extraction", self.codes("x " * 150, 2), "moins de 200 caractères par page")
        self.assertNotIn("extraction", self.codes(CLEAN_CV, 1))

    def test_each_missing_section(self):
        for start, code in (("FORMATION", "rubrique_formation"), ("EXPÉRIENCES", "rubrique_experiences"),
                            ("COMPÉTENCES", "rubrique_competences"), ("LANGUES", "rubrique_langues")):
            codes = self.codes(without_line(CLEAN_CV, start), 1)
            self.assertIn(code, codes, start)
            self.assertEqual([c for c in codes if c.startswith("rubrique_")], [code], "les autres restent trouvées")

    def test_section_synonyms(self):
        english = "EDUCATION\nx\nWork experience\ny\nSkills\nz\nLanguages: English, French"
        self.assertEqual(rules.detect_sections(english), {"formation", "experiences", "competences", "langues"})
        self.assertEqual(rules.detect_sections("Parcours\nSavoir-faire\nLangues : anglais (B2), espagnol (A2)"),
                         {"formation", "competences", "langues"})
        self.assertEqual(rules.detect_sections("Formation de 20 personnes à l'outil interne de la société"), set(),
                         "une phrase qui commence par « Formation » n'est pas un titre")

    def test_languages_as_a_sub_section(self):
        """« Langues » rangée sous « Compétences », ou écrite autrement, est bien trouvée."""
        base = without_line(CLEAN_CV, "LANGUES")
        self.assertIn("rubrique_langues", self.codes(base, 1))
        for variant in ("Langues – Anglais courant, espagnol intermédiaire, allemand scolaire et italien débutant",
                        "• Langues : anglais, espagnol, allemand, italien et portugais pour les voyages",
                        "Langues étrangères : anglais courant, espagnol intermédiaire, allemand et italien",
                        "Langues parlées | Anglais, Espagnol",
                        "Langues\tAnglais courant, espagnol intermédiaire, allemand scolaire, italien",
                        "COMPÉTENCES LINGUISTIQUES",
                        "Compétences et langues",
                        "Anglais : C1 (TOEIC 945)"):
            cv = base.replace("COMPÉTENCES\n", f"COMPÉTENCES\n{variant}\n")
            self.assertNotIn("rubrique_langues", self.codes(cv, 1), variant)
            self.assertNotIn("mise_en_page", self.codes(cv, 1), f"{variant} : pas de faux signal de colonnes")
        self.assertIn("competences", rules.detect_sections("Compétences et langues"))
        self.assertIn("langues", rules.detect_sections("Informatique : Python, SQL        Langues : anglais, espagnol"),
                      "sous-rubrique placée dans une seconde colonne")

    def test_contacts(self):
        self.assertIn("email", self.codes(CLEAN_CV.replace("lea.martin@example.fr", "LinkedIn"), 1))
        self.assertIn("telephone", self.codes(CLEAN_CV.replace("06 12 34 56 78", ""), 1))
        for phone in ("06.12.34.56.78", "+33 6 12 34 56 78", "0612345678", "+44 20 7946 0958", "+1 415 555 2671"):
            self.assertTrue(rules.has_phone(f"Tél. {phone}"), phone)
        self.assertFalse(rules.has_phone("2021 - 2024"))
        self.assertTrue(rules.has_email("contact : lea_m+cv@univ-lyon2.fr"))

    def test_tables_and_columns(self):
        self.assertIn("mise_en_page", self.codes(CLEAN_CV, 1, tables=1))
        columns = CLEAN_CV.replace("FORMATION", "FORMATION          COMPÉTENCES")
        self.assertIn("mise_en_page", self.codes(columns, 1))
        side_by_side = CLEAN_CV + "\n" + "\n".join(
            f"Python avancé        Stage {y} chez Banque Rhône" for y in range(2019, 2023))
        alert = next(a for a in rules.ats_alerts(side_by_side, 1) if a["code"] == "mise_en_page")
        self.assertIn("semble en colonnes", alert["title"])

    def test_length(self):
        self.assertIn("long", self.codes(CLEAN_CV, 3))
        short = "\n".join(CLEAN_CV.splitlines()[:9]) + "\nCOMPÉTENCES\nPython\nLANGUES\nAnglais"
        self.assertIn("court", self.codes(short, 1))

    def test_every_alert_has_advice_and_gravity_order(self):
        alerts = rules.ats_alerts("Bonjour " * 40, 3, tables=2)
        self.assertGreaterEqual(len(alerts), 7)
        for a in alerts:
            self.assertTrue(a["advice"].strip(), a["code"])
            self.assertTrue(a["title"].strip(), a["code"])
        order = [rules.GRAVITY[a["gravity"]] for a in alerts]
        self.assertEqual(order, sorted(order), "classées par gravité")


# ================================================================ pages
class _AppCase(unittest.TestCase):
    extra = ["backend.plugins.verif_cv"]
    features = None   # None : FEATURES de config tel quel

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.upload_dir = os.path.join(self.tmp.name, "up")
        self.vcv_tmp = os.path.join(self.tmp.name, "vcv_tmp")
        overrides = {"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                     "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                     "UPLOAD_FOLDER": self.upload_dir, "VERIF_CV_TMP": self.vcv_tmp,
                     "EXTRA_FEATURES": self.extra}
        if self.features is not None:
            overrides["FEATURES"] = self.features
        self.app = create_app(overrides)
        self.client = self.app.test_client()
        self.register("e@test.fr", "Léa")

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, email, name):
        self.client.post("/inscription", data={"email": email, "username": name, "password": "motdepasse1",
                                               "password2": "motdepasse1", "level": "master", "school": "ENSAI"})

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]

    def add_offer(self, title=OFFER_TITLE, description=OFFER):
        self.client.post("/stages/offres", data={"title": title, "company": "Banque Rhône", "description": description})
        return self.q("SELECT id FROM stg_offers ORDER BY id DESC")[0]["id"]

    def upload(self, content: bytes, name="cv.docx", follow=False):
        return self.client.post("/stages/cv", data={"cv": (io.BytesIO(content), name)},
                                content_type="multipart/form-data", follow_redirects=follow)

    def page(self, url="/stages/cv"):
        r = self.client.get(url)
        return r.status_code, r.get_data(as_text=True).replace("&#39;", "'")


class UploadAndPrivacyTests(_AppCase):
    def test_docx_upload_keeps_only_the_text(self):
        r = self.upload(make_docx(CLEAN_CV, table=[["Outils", "Tableau Power Query"]]), follow=True)
        html = r.get_data(as_text=True).replace("&#39;", "'")
        self.assertIn("Seul son texte est gardé", html)
        rows = self.q("SELECT * FROM vcv_resumes")
        self.assertEqual(len(rows), 1)
        self.assertIn("Master statistique et science des données", rows[0]["text"])
        self.assertIn("Tableau Power Query", rows[0]["text"], "le texte des tableaux du DOCX est lu aussi")
        self.assertIn('"tables": 1', rows[0]["signals"])
        self.assertEqual(rows[0]["filename"], "cv.docx")
        leftovers = [p for p in pathlib.Path(self.upload_dir).rglob("*") if p.is_file()]
        self.assertEqual(leftovers, [], "aucun fichier dans UPLOAD_FOLDER")
        self.assertEqual(os.listdir(self.vcv_tmp), [], "le fichier temporaire est supprimé")
        self.assertIn("Lisibilité par un logiciel de recrutement", html)
        self.assertIn("1 tableau", html)

        # « Supprimer mon CV » efface la ligne
        r = self.client.post("/stages/cv/supprimer", follow_redirects=True)
        self.assertIn("Ton CV a été supprimé", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT * FROM vcv_resumes"), [])

    def test_new_upload_replaces_the_old_one(self):
        self.upload(make_docx("Ancien CV\nancien@example.fr"))
        self.upload(make_docx(CLEAN_CV))
        rows = self.q("SELECT text FROM vcv_resumes")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("Ancien CV", rows[0]["text"])

    def test_account_deletion_cascades(self):
        self.upload(make_docx(CLEAN_CV))
        uid = self.q("SELECT user_id FROM vcv_resumes")[0]["user_id"]
        with self.app.app_context():
            from backend.dao import users
            users.delete(uid)
            get_db().commit()
        self.assertEqual(self.q("SELECT * FROM vcv_resumes"), [])

    def test_refused_extension_and_size(self):
        r = self.upload(b"MZ\x90\x00", name="virus.exe", follow=True)
        self.assertIn("Format refusé : dépose ton CV en PDF, DOCX ou ODT.", r.get_data(as_text=True))
        r = self.upload(b"0" * (5 * 1024 * 1024 + 1), name="gros.pdf", follow=True)
        self.assertIn("Fichier trop lourd : ton CV doit faire 5 Mo au plus.", r.get_data(as_text=True))
        r = self.client.post("/stages/cv", data={}, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("Choisis le fichier de ton CV", r.get_data(as_text=True))
        self.assertEqual(self.q("SELECT * FROM vcv_resumes"), [])
        self.assertEqual(os.listdir(self.vcv_tmp) if os.path.isdir(self.vcv_tmp) else [], [])

    def test_scanned_pdf_is_flagged(self):
        r = self.upload(make_blank_pdf(), name="scan.pdf", follow=True)
        html = r.get_data(as_text=True).replace("&#39;", "'")
        self.assertIn("Presque aucun texte n'a pu être lu", html)
        self.assertIn("Refais un PDF depuis Word ou LibreOffice", html)
        self.assertEqual(os.listdir(self.vcv_tmp), [])

    def test_users_never_see_each_other(self):
        self.upload(make_docx(CLEAN_CV))
        oid = self.add_offer()
        self.assertIn("Banque Rhône", self.page()[1])
        self.client.post("/deconnexion")
        self.register("b@test.fr", "Bobby")
        status, html = self.page()
        self.assertEqual(status, 200)
        self.assertNotIn("Master statistique", html)
        self.assertNotIn("cv.docx", html)
        self.assertEqual(self.client.get(f"/stages/cv?offre={oid}").status_code, 404)
        self.assertEqual(self.client.post("/stages/cv/conseils-ia", data={"offer_id": oid}).status_code, 404)
        self.client.post("/stages/cv/supprimer")
        self.assertEqual(len(self.q("SELECT * FROM vcv_resumes")), 1, "Bobby ne supprime pas le CV de Léa")


class StagesIntegrationTests(_AppCase):
    def test_tabs_card_badge_and_read_only_offers(self):
        oid = self.add_offer()
        self.add_offer(title="Juriste", description="")   # intitulé seul : pas de pastille
        status, html = self.page("/stages")
        self.assertEqual(status, 200)
        sub = html[html.index('<nav class="subbar"'):]
        sub = sub[:sub.index("</nav>")]
        self.assertIn('aria-current="page">Mes offres</a>', sub)
        self.assertIn(">Vérifier mon CV</a>", sub)
        self.assertNotIn("CV : ", html, "rien sur les cartes sans CV déposé")

        before = self.q("SELECT * FROM stg_offers ORDER BY id")
        self.upload(make_docx(CLEAN_CV))
        status, html = self.page("/stages")
        self.assertEqual(status, 200)
        self.assertEqual(html.count("CV : "), 1, "une pastille, pour l'offre avec un texte")
        self.assertRegex(html, r"CV : \d{1,3} / 100")
        self.assertIn(f"/stages/cv?offre={oid}", html)

        status, html = self.page(f"/stages/cv?offre={oid}")
        self.assertEqual(status, 200)
        sub = html[html.index('<nav class="subbar"'):]
        self.assertIn('aria-current="page">Vérifier mon CV</a>', sub[:sub.index("</nav>")])
        self.assertRegex(html, r'class="vcv-score">\d{1,3} / 100')
        self.assertIn("Copier les mots-clés manquants", html)
        self.assertIn("Ajoute-les seulement si tu as vraiment cette compétence", html)
        self.assertIn("pas le score d'un vrai logiciel de recrutement", html)
        self.assertIn('class="tag tag-school">SQL</li>', html)
        self.assertIn('class="tag tag-pdf">data analyst</li>', html)
        self.assertEqual(self.q("SELECT * FROM stg_offers ORDER BY id"), before, "stg_offers en lecture seule")

    def test_page_without_cv_or_offer(self):
        status, html = self.page()
        self.assertEqual(status, 200)
        self.assertIn("seul le texte est gardé".lower(), html.lower())
        self.assertIn("Garde d'abord une offre", html)
        self.assertNotIn("Supprimer mon CV", html)
        self.assertEqual(self.client.get("/stages/cv?offre=abc").status_code, 200)
        self.assertEqual(self.client.get("/stages/cv?offre=999").status_code, 404)


class WithoutPluginTests(_AppCase):
    # Retire explicitement le plugin, qu'il soit dans FEATURES (après fusion) ou non.
    features = [f for f in config.FEATURES if f != "backend.plugins.verif_cv"]
    extra = []

    def test_stages_unchanged_without_plugin(self):
        self.add_offer()
        status, html = self.page("/stages")
        self.assertEqual(status, 200)
        self.assertNotIn("Vérifier mon CV", html)
        self.assertNotIn("CV : ", html)
        self.assertEqual(self.client.get("/stages/cv").status_code, 404)


class DuplicateFeatureTests(_AppCase):
    # Cas d'après fusion : le plugin est à la fois dans FEATURES et dans EXTRA_FEATURES.
    features = [f for f in config.FEATURES if f != "backend.plugins.verif_cv"] + ["backend.plugins.verif_cv"]

    def test_plugin_listed_twice_is_loaded_once(self):
        self.assertIn("verif_cv", self.app.blueprints)
        self.assertEqual(self.client.get("/stages/cv").status_code, 200)


class GroqTests(_AppCase):
    def test_without_key_everything_else_works(self):
        self.upload(make_docx(CLEAN_CV))
        oid = self.add_offer()
        status, html = self.page(f"/stages/cv?offre={oid}")
        self.assertEqual(status, 200)
        self.assertIn("Conseils de l'IA indisponibles sur ce serveur", html)
        self.assertNotIn("Demander des conseils de rédaction à l'IA", html)
        self.assertNotIn("seront envoyés à Groq", html)
        self.assertRegex(html, r'class="vcv-score">\d{1,3} / 100')
        self.assertIn("Ajoute-les seulement si tu as vraiment cette compétence", html)

    def test_cv_is_sent_only_after_the_click(self):
        self.app.config.update(VERIF_CV_AI=True, GROQ_API_KEY="gsk_test")
        self.upload(make_docx(CLEAN_CV))
        oid = self.add_offer()
        with mock.patch.object(groq, "chat", return_value="Formation : garde ce titre. [Si tu as utilisé Power BI : "
                                                          "« Tableaux de bord Power BI pour le marketing ».]") as chat:
            status, html = self.page(f"/stages/cv?offre={oid}")
            self.assertEqual(status, 200)
            chat.assert_not_called()
            mention = html.index("Le texte de ton CV et celui de l'offre seront envoyés à Groq")
            self.assertLess(mention, html.index("Demander des conseils de rédaction à l'IA"))
            r = self.client.post("/stages/cv/conseils-ia", data={"offer_id": oid})
            self.assertEqual(r.status_code, 200)
            chat.assert_called_once()
        sent = " ".join(m["content"] for m in chat.call_args.args[0])
        self.assertIn("Master statistique et science des données", sent)
        self.assertIn("Power BI", sent)
        self.assertIn("Tableaux de bord Power BI pour le marketing", r.get_data(as_text=True))

    def test_groq_error_gives_a_french_message(self):
        self.app.config.update(VERIF_CV_AI=True, GROQ_API_KEY="gsk_test")
        self.upload(make_docx(CLEAN_CV))
        oid = self.add_offer()
        with mock.patch.object(groq, "chat", side_effect=groq.GroqError("Quota de Groq atteint : réessaie dans une minute.")):
            r = self.client.post("/stages/cv/conseils-ia", data={"offer_id": oid}, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True).replace("&#39;", "'")
        self.assertIn("Groq n'a pas pu rédiger de conseils", html)
        self.assertIn("Quota de Groq atteint", html)


class SecurityTests(_AppCase):
    def test_routes_need_login(self):
        self.client.post("/deconnexion")
        for method, url in (("get", "/stages/cv"), ("get", "/stages/cv?offre=1"), ("post", "/stages/cv"),
                            ("post", "/stages/cv/supprimer"), ("post", "/stages/cv/conseils-ia")):
            r = getattr(self.client, method)(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/connexion", r.headers["Location"], url)

    def test_posts_without_csrf_token_are_refused(self):
        self.upload(make_docx(CLEAN_CV))
        self.app.config["WTF_CSRF_DISABLED"] = False
        for url in ("/stages/cv", "/stages/cv/supprimer", "/stages/cv/conseils-ia"):
            self.assertEqual(self.client.post(url, data={}).status_code, 400, url)
        self.assertEqual(len(self.q("SELECT * FROM vcv_resumes")), 1)
        with self.client.session_transaction() as s:
            s["_csrf"] = "jeton"
        self.client.post("/stages/cv/supprimer", data={"_csrf": "jeton"})
        self.assertEqual(self.q("SELECT * FROM vcv_resumes"), [], "avec le jeton, la suppression passe")

    def test_no_secret_in_code(self):
        for path in PLUGIN_DIR.rglob("*.*"):
            if path.suffix in (".py", ".html"):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"gsk_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}", text), path.name)

    def test_refuses_to_start_without_stages(self):
        from backend import config
        tmp = self.tmp.name
        with self.assertRaisesRegex(RuntimeError, "verif_cv a besoin du plugin stages"):
            create_app({"TESTING": True, "ENABLE_WEB": False, "DATABASE": os.path.join(tmp, "o.sqlite"),
                        "UPLOAD_FOLDER": os.path.join(tmp, "o"),
                        "FEATURES": [f for f in config.FEATURES if f != "backend.plugins.stages"],
                        "EXTRA_FEATURES": ["backend.plugins.verif_cv"]})


if __name__ == "__main__":
    unittest.main()
