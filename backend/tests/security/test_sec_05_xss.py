"""SEC-05 — XSS : chaque champ affiché reçoit les charges du cahier ; chaque page qui l'affiche doit l'échapper.

Contenu généré par un LLM : faux Groq (FauxGroq, aucun appel réseau) qui renvoie du HTML et du LaTeX piégé.
Tests statiques : pas de |safe ni de Markup() hors liste blanche, pas d'innerHTML qui reçoit des données,
KaTeX jamais appelé avec trust: true.
"""
import io
import json
import os
import re
import unittest
from datetime import date, timedelta

from ._outils import ADMIN_EMAIL, ADMIN_MDP, CLE_FACTICE, AppSecuTestCase, FauxGroq

SCRIPT = "<script>alert(1)</script>"
IMG = "<img src=x onerror=alert(1)>"
SVG = '"><svg onload=alert(1)>'
LONG = SCRIPT + IMG + SVG
BRUTS = ("<script>alert(1)", "<img src=x onerror", "<svg onload")  # jamais tels quels dans le HTML
ECHAPPE = "&lt;svg onload=alert(1)&gt;"

COURS = (f"# Chapitre {SCRIPT}\nLa variance {IMG} mesure la dispersion des valeurs autour de l'espérance.\n"
         "La variance est égale à l'espérance du carré moins le carré de l'espérance.\n"
         f"# Lois usuelles {SVG}\nLa loi binomiale est la loi du nombre de succès dans des épreuves indépendantes.\n"
         "La loi normale est une loi continue dont la densité a une forme de cloche.\n"
         "La loi de Poisson est une loi discrète qui modélise le nombre d'événements rares.\n"
         "# Espérance\nL'espérance est la moyenne des valeurs pondérée par leurs probabilités.\n"
         "L'écart-type est la racine carrée de la variance.\n")


def bruts(page: str) -> list[str]:
    return [b for b in BRUTS if b in page]


class XssStockeeTests(AppSecuTestCase):
    """Charges stockées dans tous les champs, puis toutes les pages qui les affichent sont relues."""

    def setUp(self):
        super().setUp()
        c = self.client
        self.inscrire("eleve@test.fr", username=SVG)
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]
        jour = (date.today() + timedelta(days=3)).isoformat()
        self.envois = [
            ("/profil", {"username": SVG, "level": "licence", "school": SVG, "full_name": SVG, "home_city": SVG,
                         "study_city": SVG, "field": SVG, "program": LONG, "languages": LONG,
                         "website": "javascript:alert(1)"}),
            ("/matieres/nouvelle", {"name": LONG, "goal": "a_jour", "weekly_hours": "3", "comfort": "3",
                                    "topics": f"{LONG}\nVariance\nLois usuelles"}),
            ("/agenda/ajouter", {"title": LONG, "kind": "cours", "subject": "1", "date": jour, "start": "10:00",
                                 "end": "12:00", "repeat_weeks": "1"}),
            ("/agenda/ajouter", {"title": LONG, "kind": "autre", "date": jour, "start": "14:00", "end": "15:00",
                                 "repeat_weeks": "1"}),
            ("/matieres/1/ressources/ajouter", {"title": LONG, "url": f"https://exemple.fr/{SVG}"}),
            ("/matieres/1/examens", {"date": (date.today() + timedelta(days=20)).isoformat(), "title": LONG}),
            ("/dossier/notes", {"course": SVG, "subject_id": "1", "value": "12", "out_of": "20", "coefficient": "1",
                                "label": SVG}),
            ("/cafeine/ajouter", {"custom_label": SVG[:40], "custom_mg": "80"}),
            ("/stages/profil", {"goal": SVG, "skills": LONG, "experiences": LONG, "projects": LONG,
                                "qualities": LONG, "duration": SVG, "start": SVG}),
            ("/stages/postes", {"title": LONG, "location": SVG}),
            ("/stages/offres", {"title": LONG, "company": SVG, "location": SVG, "description": LONG,
                                "url": f"https://exemple.fr/{SVG}"}),
            ("/stages/lettres", {"offer_id": "1"}),
            ("/stages/lettres", {"title": LONG, "company": SVG, "location": SVG, "description": LONG}),
            ("/reviser/1/niveau", {"difficulte": "moyen"}),
            ("/reviser/1/qcm", {"chapitre": "", "difficulte": "moyen"}),
            ("/campus/signaler", {"place": "bu", "level": "3", "back": "/campus/"}),
        ]
        self.pages_flash = []
        for url, data in self.envois[:4]:
            self.pages_flash.append((url, c.post(url, data=data, follow_redirects=True).get_data(as_text=True)))
        c.post("/matieres/1/documents", data={"documents": (io.BytesIO(COURS.encode()), f"{IMG}.md")},
               content_type="multipart/form-data")
        ics = (f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:{LONG}\r\n"
               f"DTSTART:{jour.replace('-', '')}T080000\r\nDTEND:{jour.replace('-', '')}T090000\r\n"
               "END:VEVENT\r\nEND:VCALENDAR\r\n")
        self.pages_flash.append(("/agenda/import", c.post("/agenda/import", data={"ics": (io.BytesIO(ics.encode()), "e.ics")},
                                                          content_type="multipart/form-data",
                                                          follow_redirects=True).get_data(as_text=True)))
        for url, data in self.envois[4:]:
            self.pages_flash.append((url, c.post(url, data=data, follow_redirects=True).get_data(as_text=True)))

    PAGES = ["/", "/tableau-de-bord", "/profil", "/matieres/1", "/matieres/1/modifier", "/planning", "/agenda",
             "/dossier/", "/cafeine", "/stages", "/stages/lettres/1", "/stages/lettres/2", "/reviser", "/reviser/1",
             "/reviser/1/fiches", "/reviser/qcm/1", "/campus/", "/campus/bibliotheque", "/preferences",
             "/matieres/nouvelle?nom=" + SVG, "/encadrement"]

    def test_les_charges_sont_bien_stockees(self):
        """Témoin : sans charge en base, les tests d'affichage ne prouveraient rien."""
        self.assertIn(SVG, self.q("SELECT username FROM users WHERE id = ?", self.uid)[0]["username"])
        self.assertTrue(self.q("SELECT 1 FROM subjects WHERE name LIKE '%<script>%'"))
        self.assertTrue(self.q("SELECT 1 FROM events WHERE title LIKE '%<img%'"))
        self.assertTrue(self.q("SELECT 1 FROM stg_offers WHERE title LIKE '%<script>%'"))
        self.assertTrue(self.q("SELECT 1 FROM stg_letters"))

    def test_pages_de_l_utilisateur_echappent_tout(self):
        vues = 0
        for url in self.PAGES:
            with self.subTest(page=url):
                r = self.client.get(url)
                self.assertNotEqual(r.status_code, 500)
                page = r.get_data(as_text=True)
                self.assertEqual(bruts(page), [], f"{url} : charge non échappée")
                vues += ECHAPPE in page or "&lt;script&gt;" in page
        self.assertGreater(vues, 8, "les charges devraient apparaître (échappées) sur la plupart des pages")

    def test_messages_flash_echappes(self):
        for url, page in self.pages_flash:
            with self.subTest(envoi=url):
                self.assertEqual(bruts(page), [], f"réponse à {url} : charge non échappée")

    def test_pages_admin_echappent_tout(self):
        admin = self.app.test_client()
        self.connecter(ADMIN_EMAIL, ADMIN_MDP, client=admin)
        for url in ("/admin/", f"/admin/?q={SVG}", "/admin/matieres", f"/admin/utilisateurs/{self.uid}"):
            with self.subTest(page=url):
                page = admin.get(url).get_data(as_text=True)
                self.assertEqual(bruts(page), [], f"{url} : charge non échappée")

    def test_reponses_json_et_fichiers_ne_sont_pas_du_html(self):
        for url in ("/api/suggestions/matieres?q=" + SVG, "/api/agenda/rattachement?titre=" + SVG,
                    "/stages/lettres/1/telecharger", "/planning/export.ics", "/documents/1"):
            with self.subTest(url=url):
                r = self.client.get(url)
                r.close()
                self.assertNotIn("text/html", r.headers.get("Content-Type", ""), url)


class LiensTests(AppSecuTestCase):
    """Champs qui deviennent des liens : seuls http:// et https:// passent."""

    CHARGES = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "\tjavascript:alert(1)",
               "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "jav&#x09;ascript:alert(1)",
               "//evil.com", "javascript://%0aalert(1)"]

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})

    def _hrefs(self, page):
        return re.findall(r'href="([^"]*)"', page)

    def test_site_web_du_profil(self):
        for charge in self.CHARGES:
            with self.subTest(website=charge):
                self.client.post("/profil/infos", data={"website": charge})
                v = self.q("SELECT website FROM users WHERE email = 'eleve@test.fr'")[0]["website"] or ""
                self.assertTrue(v == "" or re.match(r"^https?://", v, re.I), f"site web enregistré : {v!r}")
                self.assertFalse(re.match(r"^\s*(javascript|data|vbscript):", v, re.I))

    def test_liens_des_ressources_et_des_offres(self):
        for charge in self.CHARGES:
            with self.subTest(url=charge):
                self.client.post("/matieres/1/ressources/ajouter", data={"title": "lien", "url": charge})
                self.client.post("/stages/offres", data={"title": "Offre", "url": charge, "description": "x"})
        for page in (self.client.get("/matieres/1").get_data(as_text=True),
                     self.client.get("/stages").get_data(as_text=True)):
            for h in self._hrefs(page):
                self.assertFalse(re.match(r"^\s*(javascript|data|vbscript):", h, re.I), f"lien dangereux : {h!r}")
                self.assertNotIn("evil.com", h)


class ContenuLlmTests(AppSecuTestCase):
    """Contenu rédigé par le LLM (faux Groq) : HTML et LaTeX piégés affichés échappés."""

    config = {"REVISION_AI": True, "STAGES_AI": True, "GROQ_API_KEY": CLE_FACTICE, "PROPAGATE_EXCEPTIONS": False}
    HREF = r"$\href{javascript:alert(1)}{clique}$"

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Probabilités", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "Variance\nLois usuelles\nEspérance"})
        self.client.post("/matieres/1/documents", data={"documents": (io.BytesIO(COURS.encode()), "c.md")},
                         content_type="multipart/form-data")
        self.client.post("/reviser/1/niveau", data={"difficulte": "moyen"})

    def _reponse(self, body):
        consigne = json.dumps(body.get("messages", []), ensure_ascii=False)
        if "QCM" in consigne:
            q = {"chapter": "Variance", "prompt": f"Question {SCRIPT} {self.HREF}", "quote": IMG,
                 "options": [f"Bonne {SVG}", "Faux 1", "Faux 2", "Faux 3"], "explanation": f"{SCRIPT} {self.HREF}"}
            return json.dumps({"questions": [dict(q, prompt=f"{q['prompt']} n°{i}") for i in range(10)]})
        if "exercices" in consigne:
            return json.dumps({"exercises": [{"title": f"Exo {SCRIPT}", "chapter": "Variance",
                                              "statement": f"Énoncé {IMG} {self.HREF}", "solution": SVG}]})
        return f"Madame, Monsieur,\n{SCRIPT}\n{IMG}\n" + "Je suis motivé. " * 40

    def test_qcm_exercices_et_lettre_generes_echappes(self):
        with FauxGroq(self._reponse) as groq:
            r1 = self.client.post("/reviser/1/qcm", data={"chapitre": "", "difficulte": "moyen"})
            self.client.post("/reviser/1/exercices", data={"chapitre": "", "difficulte": "moyen"})
            self.client.post("/stages/lettres", data={"title": "Assistant", "company": "Cabinet"})
        self.assertTrue(groq.appels, "le faux LLM n'a pas été appelé : le test ne prouve rien")
        pages = [self.client.get(r1.headers.get("Location", "/reviser/qcm/1")).get_data(as_text=True)]
        for url in ("/reviser/1", "/reviser/1?exos=toutes", "/stages/lettres/1", "/stages"):
            pages.append(self.client.get(url).get_data(as_text=True))
        for page in pages:
            self.assertEqual(bruts(page), [])
            self.assertNotRegex(page, r'href="\s*javascript:', "lien javascript: dans la page")
        self.assertTrue(any("&lt;script&gt;alert(1)&lt;/script&gt;" in p for p in pages),
                        "le contenu du LLM devrait apparaître, échappé")


# ---------------------------------------------------------------- analyse statique
RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
GABARITS = [os.path.join(RACINE, "frontend", "templates"), os.path.join(RACINE, "backend", "plugins")]
# Liste blanche documentée (manche 3) : (fichier relatif à la racine, motif)
MARKUP_AUTORISES = {("backend/core/security.py", "csrf_field")}  # jeton CSRF : aucune donnée utilisateur
INNERHTML_AUTORISES = {
    ("frontend/templates/base.html", "box.innerHTML = await r.text()"),  # fragment HTML rendu (et échappé) par Jinja
    ("frontend/templates/base.html", "box.innerHTML = '<p class=\"muted small\">Indisponible pour le moment.</p>'"),
    ("frontend/templates/calendar.html", "const initial = box.innerHTML"),  # lecture seule
    ("frontend/templates/calendar.html", "box.innerHTML = initial"),  # remet le HTML d'origine de la page
    # assistant : Markdown passé par DOMPurify, formules par KaTeX (sans trust), texte de l'étudiant échappé,
    # liens de source construits par url_for et échappés (relu à la fusion de main)
    ("frontend/templates/assistant/bubble.html",
     "node.innerHTML = markdown ? markdownHtml(text, sources) : plainHtml(text)"),
}


def _fichiers(extensions):
    for base in [os.path.join(RACINE, "frontend"), os.path.join(RACINE, "backend")]:
        for d, _, fs in os.walk(base):
            if "__pycache__" in d or os.sep + "tests" in d:
                continue
            for f in fs:
                if f.endswith(extensions):
                    chemin = os.path.join(d, f)
                    yield os.path.relpath(chemin, RACINE).replace(os.sep, "/"), open(chemin, encoding="utf-8").read()


class AnalyseStatiqueTests(unittest.TestCase):
    def test_aucun_filtre_safe_dans_les_gabarits(self):
        trouves = [(rel, m.group(0)) for rel, txt in _fichiers((".html", ".j2", ".jinja"))
                   for m in re.finditer(r"\|\s*safe\b|\{%-?\s*autoescape\s+false", txt)]
        self.assertEqual(trouves, [])

    def test_markup_seulement_dans_la_liste_blanche(self):
        trouves = set()
        for rel, txt in _fichiers((".py",)):
            for m in re.finditer(r"\bMarkup\(", txt):
                fonction = re.findall(r"^def (\w+)", txt[:m.start()], re.M)
                trouves.add((rel, fonction[-1] if fonction else "<module>"))
        self.assertEqual(trouves - MARKUP_AUTORISES, set(), "Markup() hors liste blanche")

    def test_innerhtml_seulement_dans_la_liste_blanche(self):
        trouves = set()
        for rel, txt in _fichiers((".html", ".js")):
            for ligne in txt.splitlines():
                if re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", ligne) and not any(
                        f == rel and motif in ligne for f, motif in INNERHTML_AUTORISES):
                    trouves.add((rel, ligne.strip()))
        self.assertEqual(trouves, set(), "insertion HTML hors liste blanche")

    def test_katex_jamais_en_mode_confiance(self):
        for rel, txt in _fichiers((".html", ".js")):
            if "renderMathInElement" in txt or "katex.render" in txt:
                self.assertNotRegex(txt, r"trust\s*:\s*(true|\(|function|[a-zA-Z_]+\s*=>)", rel)


if __name__ == "__main__":
    unittest.main()
