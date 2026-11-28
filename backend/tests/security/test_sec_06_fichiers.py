"""SEC-06 — Fichiers déposés (supports de cours, .ics, photos). Constat F-08 et suivants.

Tous les fichiers piégés sont fabriqués en mémoire (une bombe zip de 110 Mo décompressés pèse ~110 Ko).
Aucun appel réseau : la lecture de photos (Groq) est remplacée par un faux (unittest.mock).
PROPAGATE_EXCEPTIONS=False : une exception non gérée donne une vraie réponse 500, comme en production.
"""
import io
import os
import time
import unittest
import zipfile
from datetime import date, datetime, timedelta
from unittest import mock

from ._outils import AppSecuTestCase

MO = 1024 * 1024
CONFIG = {"PROPAGATE_EXCEPTIONS": False}

CT_DOCX = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
           '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
           'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
RELS_DOCX = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
             'officeDocument" Target="word/document.xml"/></Relationships>')
W = b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(corps_xml: bytes = b"", remplissage_mo: int = 0, paragraphes_mo: int = 0) -> bytes:
    """.docx valide. remplissage_mo : Mo d'espaces dans document.xml (bombe zip) ;
    paragraphes_mo : Mo de paragraphes minuscules (coût de traitement par python-docx)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT_DOCX)
        z.writestr("_rels/.rels", RELS_DOCX)
        with z.open("word/document.xml", "w") as f:
            f.write(b'<?xml version="1.0"?><w:document ' + W + b'><w:body>'
                    b'<w:p><w:r><w:t>Chapitre 1 Les suites</w:t></w:r></w:p>' + corps_xml)
            for _ in range(remplissage_mo):
                f.write(b" " * MO)
            bloc = b"<w:p><w:r><w:t>a</w:t></w:r></w:p>" * 30000
            for _ in range(paragraphes_mo * MO // len(bloc)):
                f.write(bloc)
            f.write(b"</w:body></w:document>")
    return buf.getvalue()


def odt(image_mo: int = 0, entrees: int = 0) -> bytes:
    """.odt valide ; image_mo : une « image » de zéros déclarée dans le manifeste (odfpy la charge en mémoire) ;
    entrees : nombre d'entrées supplémentaires dans l'archive."""
    buf = io.BytesIO()
    manifeste = ['<manifest:file-entry manifest:full-path="/" '
                 'manifest:media-type="application/vnd.oasis.opendocument.text"/>',
                 '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>']
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", '<?xml version="1.0"?><office:document-content '
                   'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                   'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
                   '<office:body><office:text><text:h text:outline-level="1">Chapitre 1</text:h>'
                   '<text:p>Texte du cours.</text:p></office:text></office:body></office:document-content>')
        if image_mo:
            manifeste.append('<manifest:file-entry manifest:full-path="Pictures/bombe.png" '
                             'manifest:media-type="image/png"/>')
            with z.open("Pictures/bombe.png", "w") as f:
                for _ in range(image_mo):
                    f.write(b"\0" * MO)
        for i in range(entrees):
            z.writestr(f"Pictures/p{i}.png", b"x")
        z.writestr("META-INF/manifest.xml", '<?xml version="1.0"?><manifest:manifest '
                   'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                   + "".join(manifeste) + "</manifest:manifest>")
    return buf.getvalue()


def pdf(pages: int = 1, mot_de_passe: str | None = None) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(612, 792)
    if mot_de_passe:
        w.encrypt(mot_de_passe)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


HTML = b"<!doctype html><html><body><script>alert(document.cookie)</script>Chapitre 1</body></html>"


class _AvecMatiere(AppSecuTestCase):
    config = CONFIG

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A\nB"})
        self.sid = self.q("SELECT id FROM subjects WHERE user_id = ?", self.uid)[0]["id"]

    def deposer(self, contenu: bytes, nom: str):
        debut = time.perf_counter()
        r = self.client.post(f"/matieres/{self.sid}/documents", data={"documents": (io.BytesIO(contenu), nom)},
                             content_type="multipart/form-data")
        return r, time.perf_counter() - debut

    def documents(self):
        return self.q("SELECT d.* FROM documents d JOIN subjects s ON s.id = d.subject_id WHERE s.user_id = ?",
                      self.uid)

    def fichiers_deposes(self):
        racine = self.app.config["UPLOAD_FOLDER"]
        return [os.path.join(d, f) for d, _, fs in os.walk(racine) for f in fs]


class ArchivesPiegeesTests(_AvecMatiere):
    """F-08 : archives Office/ODF analysées sans limite de taille décompressée ni de nombre d'entrées."""

    def _verifier_refus(self, contenu, nom):
        r, duree = self.deposer(contenu, nom)
        self.assertNotEqual(r.status_code, 500, f"{nom} : erreur serveur")
        self.assertLess(duree, 30, f"{nom} : {duree:.1f} s")
        self.assertEqual(self.documents(), [], f"{nom} ({len(contenu)} octets) : archive piégée acceptée")
        self.assertEqual(self.fichiers_deposes(), [], f"{nom} : le fichier refusé reste sur le disque")

    def test_bombe_zip_docx_plus_de_100_mo_decompresses(self):
        self._verifier_refus(docx(remplissage_mo=110), "cours.docx")

    def test_bombe_zip_odt_plus_de_100_mo_decompresses(self):
        self._verifier_refus(odt(image_mo=110), "cours.odt")

    def test_archive_de_plus_de_2000_entrees(self):
        self._verifier_refus(odt(entrees=2500), "cours.odt")

    def test_archive_normale_acceptee(self):
        """Non-régression : un vrai .docx et un vrai .odt passent toujours."""
        for contenu, nom in ((docx(), "cours.docx"), (odt(), "cours.odt")):
            with self.subTest(nom=nom):
                r, _ = self.deposer(contenu, nom)
                self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.documents()), 2)


class CoutDeTraitementTests(_AvecMatiere):
    """F-017 : un .docx de ~45 Ko (15 Mo de XML, sous la limite de 100 Mo) occupe le serveur bien plus de 30 s."""

    def test_docx_a_paragraphes_minuscules_traite_en_moins_de_30_s(self):
        contenu = docx(paragraphes_mo=15)
        self.assertLess(len(contenu), 100 * 1024)
        r, duree = self.deposer(contenu, "cours.docx")
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30, f"un .docx de {len(contenu)} octets bloque la requête {duree:.1f} s")


class PdfTests(_AvecMatiere):
    def test_pdf_de_10000_pages(self):
        """Non-régression : extraction bornée, réponse rapide, pas d'erreur."""
        r, duree = self.deposer(pdf(10000), "gros.pdf")
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 20)

    def test_pdf_chiffre_ou_corrompu_donne_un_message(self):
        """Non-régression : ni 500, ni exception ; un message est affiché."""
        cas = [(pdf(1, "secret"), "chiffre.pdf"), (b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 9 0 R >>\n%%EOF", "casse.pdf"),
               (pdf(3)[: 400], "tronque.pdf")]
        for contenu, nom in cas:
            with self.subTest(nom=nom):
                r = self.client.post(f"/matieres/{self.sid}/documents",
                                     data={"documents": (io.BytesIO(contenu), nom)},
                                     content_type="multipart/form-data", follow_redirects=True)
                self.assertEqual(r.status_code, 200)
                self.assertIn(nom, r.get_data(as_text=True), "aucun message sur le fichier")


class TypeParLeContenuTests(_AvecMatiere):
    """F-018 : le type n'est vérifié que par l'extension ; aucun en-tête nosniff sur /documents/<id>."""

    def test_html_renomme_en_pdf_ou_docx_refuse(self):
        for nom in ("cours.pdf", "cours.docx", "cours.odt", "cours.pptx"):
            with self.subTest(nom=nom):
                r, _ = self.deposer(HTML, nom)
                self.assertNotEqual(r.status_code, 500)
        noms = [d["filename"] for d in self.documents()]
        self.assertEqual(noms, [], f"fichiers HTML acceptés sous une extension de document : {noms}")

    def test_documents_servis_avec_nosniff(self):
        self.deposer(HTML, "notes.txt")
        self.deposer(pdf(1), "vrai.pdf")
        for d in self.documents():
            with self.subTest(fichier=d["filename"]):
                r = self.client.get(f"/documents/{d['id']}")
                r.close()
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
                self.assertNotIn("html", r.headers.get("Content-Type", "").lower())

    def test_seuls_pdf_et_texte_sont_affiches_le_reste_telecharge(self):
        """Non-régression : un .docx ou un .odt est servi en pièce jointe, jamais affiché dans le navigateur."""
        self.deposer(docx(), "cours.docx")
        self.deposer(odt(), "cours.odt")
        self.assertEqual(len(self.documents()), 2)
        for d in self.documents():
            with self.subTest(fichier=d["filename"]):
                r = self.client.get(f"/documents/{d['id']}")
                ctype = r.headers.get("Content-Type", "")
                disposition = r.headers.get("Content-Disposition", "")
                r.close()
                self.assertIn("attachment", disposition, ctype)
                self.assertNotIn("text/html", ctype)


class NomsDeFichiersTests(_AvecMatiere):
    def test_traversee_de_chemin_dans_le_nom(self):
        """Non-régression : aucun fichier écrit hors de UPLOAD_FOLDER/<user_id>/."""
        noms = ["../../etc/passwd.txt", "..\\..\\evil.txt", "/etc/passwd.md", "C:\\Windows\\evil.txt",
                "....//....//evil.txt", "%2e%2e%2fevil.txt", ".txt", "a\x00b.txt"]
        for nom in noms:
            with self.subTest(nom=nom):
                r, _ = self.deposer(b"# Chapitre\ntexte", nom)
                self.assertNotEqual(r.status_code, 500)
        dossier = os.path.realpath(os.path.join(self.app.config["UPLOAD_FOLDER"], str(self.uid)))
        for chemin in self.fichiers_deposes():
            self.assertEqual(os.path.dirname(os.path.realpath(chemin)), dossier, chemin)
        for d in self.documents():
            self.assertTrue(os.path.realpath(d["stored_path"]).startswith(dossier + os.sep), d["stored_path"])
            r = self.client.get(f"/documents/{d['id']}?telecharger=1")
            r.close()
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("\n", r.headers.get("Content-Disposition", ""))

    def test_nom_de_fichier_tres_long_sans_erreur_500(self):
        """F-023 : le nom d'origine est collé à un UUID sans être raccourci : au-delà de la limite du système
        de fichiers (255 octets par nom sous Linux, 260 caractères de chemin sous Windows), l'envoi plante (500)."""
        for nom in ("a" * 250 + ".txt", "e" * 240 + ".md", "x" * 195 + ".html.txt"):
            with self.subTest(longueur=len(nom)):
                r, _ = self.deposer(b"# Chapitre\ntexte", nom)
                self.assertNotEqual(r.status_code, 500, f"nom de {len(nom)} caractères : erreur serveur")


class QuotaTests(_AvecMatiere):
    """F-019 : pas de quota de 200 Mo par utilisateur (9 envois de 24 Mo = 216 Mo acceptés)."""

    def test_quota_de_200_mo_par_utilisateur(self):
        bloc = b" " * (24 * MO)
        for i in range(9):
            self.deposer(bloc, f"gros{i}.txt")
        total = sum(os.path.getsize(p) for p in self.fichiers_deposes())
        self.assertLessEqual(total, 200 * MO, f"{total / MO:.0f} Mo stockés pour un seul utilisateur")


# ---------------------------------------------------------------- agenda (.ics)
def calendrier(*evenements: str, remplissage: int = 0) -> bytes:
    pad = "".join(f"X-PAD-{i}:{'x' * 60}\r\n" for i in range(remplissage))
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + pad + "".join(evenements) + "END:VCALENDAR\r\n").encode()


def evenement(uid, titre, debut: str, fin: str | None = None, rrule: str | None = None) -> str:
    lignes = ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{titre}", f"DTSTART:{debut}"]
    if fin:
        lignes.append(f"DTEND:{fin}")
    if rrule:
        lignes.append(f"RRULE:{rrule}")
    return "\r\n".join(lignes + ["END:VEVENT"]) + "\r\n"


class ImportIcsTests(AppSecuTestCase):
    config = CONFIG

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]
        self.demain = datetime.combine(date.today() + timedelta(days=1), datetime.min.time()).replace(hour=10)

    def importer(self, contenu: bytes):
        debut = time.perf_counter()
        r = self.client.post("/agenda/import", data={"ics": (io.BytesIO(contenu), "edt.ics")},
                             content_type="multipart/form-data")
        return r, time.perf_counter() - debut

    def evenements(self):
        return self.q("SELECT * FROM events WHERE user_id = ?", self.uid)

    def test_fichier_de_plus_de_2_mo_refuse(self):
        d = self.demain.strftime("%Y%m%dT%H%M%S")
        contenu = calendrier(evenement("a", "Cours", d), remplissage=40000)
        self.assertGreater(len(contenu), 2 * MO)
        r, _ = self.importer(contenu)
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(self.evenements(), [], f".ics de {len(contenu) / MO:.1f} Mo importé")

    def test_plus_de_5000_evenements_refuses_ou_tronques(self):
        evs = [evenement(f"e{i}", f"Cours {i % 50}", (self.demain + timedelta(minutes=37 * i)).strftime("%Y%m%dT%H%M%S"),
                         (self.demain + timedelta(minutes=37 * i + 30)).strftime("%Y%m%dT%H%M%S"))
               for i in range(6000)]
        r, _ = self.importer(calendrier(*evs))
        self.assertNotEqual(r.status_code, 500)
        self.assertLessEqual(len(self.evenements()), 5000)

    def test_recurrence_sans_fin_depliee_sur_un_an_au_plus(self):
        """Non-régression : MINUTELY, DAILY, WEEKLY sans fin ne dépassent pas un an d'occurrences."""
        d = self.demain.strftime("%Y%m%dT%H%M%S")
        f = (self.demain + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
        r, duree = self.importer(calendrier(evenement("m", "Minute", d, f, "FREQ=MINUTELY"),
                                            evenement("d", "Jour", d, f, "FREQ=DAILY"),
                                            evenement("w", "Semaine", d, f, "FREQ=WEEKLY;COUNT=100000")))
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30)
        limite = (self.demain + timedelta(days=366)).isoformat()
        evs = self.evenements()
        self.assertLessEqual(len(evs), 366 + 53 + 1)
        self.assertTrue(all(e["start"] <= limite for e in evs))

    def test_recurrence_partant_de_l_an_1_ne_bloque_pas_le_serveur(self):
        """F-020 : DTSTART en l'an 1 + RRULE sans fin : la boucle part de l'an 1, jour par jour (~1 s par événement)."""
        evs = [evenement(f"v{i}", f"Vieux cours {i}", "00010101T100000", "00010101T110000", "FREQ=WEEKLY")
               for i in range(40)]
        contenu = calendrier(*evs)
        r, duree = self.importer(contenu)
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30, f"{len(contenu)} octets d'.ics bloquent la requête {duree:.1f} s")

    def test_regles_et_dates_invalides_sans_erreur_500(self):
        """F-021 : INTERVAL=0, INTERVAL ou COUNT non numériques, UNTIL mal formé font planter l'import (500)."""
        d = self.demain.strftime("%Y%m%dT%H%M%S")
        f = (self.demain + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")
        regles = ["FREQ=DAILY;INTERVAL=0", "FREQ=WEEKLY;INTERVAL=0", "FREQ=DAILY;INTERVAL=abc",
                  "FREQ=DAILY;COUNT=x", "FREQ=WEEKLY;UNTIL=2030", "FREQ=WEEKLY;INTERVAL=-1", "FREQ=DAILY;BYDAY=ZZ",
                  "FREQ", "=;=;="]
        for regle in regles:
            with self.subTest(rrule=regle):
                r, _ = self.importer(calendrier(evenement("r", "Cours", d, f, regle)))
                self.assertNotEqual(r.status_code, 500, f"RRULE:{regle} : erreur serveur")
        for debut in ("00000101T100000", "20260230T100000", "99991231T235959", "2026", "abc", "-1"):
            with self.subTest(dtstart=debut):
                r, _ = self.importer(calendrier(evenement("x", "Cours", debut)))
                self.assertNotEqual(r.status_code, 500, f"DTSTART:{debut} : erreur serveur")


# ---------------------------------------------------------------- photos (lecture par Groq simulée)
class PhotosTests(AppSecuTestCase):
    """F-022 : ni taille maximale par photo ni nombre maximal de photos par envoi avant l'appel au LLM."""

    config = CONFIG

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A\nB"})
        self.sid = self.q("SELECT id FROM subjects")[0]["id"]
        from backend.dao import vision
        self.ask = mock.Mock(return_value="# Chapitre lu\n" + "texte de la photo " * 5)
        for p in (mock.patch.object(vision, "available", return_value=True), mock.patch.object(vision, "ask", self.ask)):
            p.start()
            self.addCleanup(p.stop)

    def _tailles_envoyees(self):
        return [len(c.args[0]) for c in self.ask.call_args_list]

    def test_photo_de_plus_de_10_mo_refusee_avant_le_llm(self):
        grosse = b"\x89PNG\r\n\x1a\n" + b"\0" * (11 * MO)
        self.client.post("/agenda/photo", data={"photo": (io.BytesIO(grosse), "edt.png", "image/png")},
                         content_type="multipart/form-data")
        self.client.post(f"/matieres/{self.sid}/documents/photo",
                         data={"photos": [(io.BytesIO(grosse), "notes.png", "image/png")]},
                         content_type="multipart/form-data")
        self.assertTrue(all(t <= 10 * MO for t in self._tailles_envoyees()),
                        f"photo de plus de 10 Mo envoyée au LLM : {self._tailles_envoyees()}")

    def test_plus_de_10_photos_par_envoi(self):
        petites = [(io.BytesIO(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 100), f"p{i}.png", "image/png") for i in range(15)]
        r = self.client.post(f"/matieres/{self.sid}/documents/photo", data={"photos": petites},
                             content_type="multipart/form-data")
        self.assertNotEqual(r.status_code, 500)
        self.assertLessEqual(self.ask.call_count, 10, f"{self.ask.call_count} photos envoyées au LLM en un envoi")



# ---------------------------------------------------------------- vérification des corrections (manche 2)
def pptx(remplissage_mo: int = 0, entrees: int = 0) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT_DOCX)
        z.writestr("ppt/slides/slide1.xml", '<?xml version="1.0"?><p:sld '
                   'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                   'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp>'
                   '<p:txBody><a:p><a:r><a:t>Chapitre 1</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        if remplissage_mo:
            with z.open("ppt/media/image1.bin", "w") as f:
                for _ in range(remplissage_mo):
                    f.write(b"\0" * MO)
        for i in range(entrees):
            z.writestr(f"ppt/media/p{i}.bin", b"x")
    return buf.getvalue()


class VariantesArchivesTests(_AvecMatiere):
    """Variantes de F-08, F-017, F-018 : autres types d'archives, archives sans partie principale, PDF déguisés."""

    def _refus(self, contenu, nom):
        r, duree = self.deposer(contenu, nom)
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30)
        self.assertEqual(self.documents(), [], f"{nom} accepté")
        self.assertEqual(self.fichiers_deposes(), [], f"{nom} : fichier refusé resté sur le disque")

    def test_bombe_et_entrees_en_pptx_et_odp(self):
        for contenu, nom in ((pptx(remplissage_mo=110), "cours.pptx"), (pptx(entrees=2500), "cours.pptx"),
                             (odt(image_mo=110), "cours.odp"), (odt(entrees=2500), "cours.odp")):
            with self.subTest(nom=nom, taille=len(contenu)):
                self._refus(contenu, nom)

    def test_archive_sans_partie_principale_ou_html_deguise(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("index.html", HTML)
        for contenu, nom in ((buf.getvalue(), "cours.docx"), (buf.getvalue(), "cours.odt"),
                             (b"   \n" + HTML, "cours.pdf"), (b"%PD" + HTML, "cours.pdf"), (HTML, "cours.odp")):
            with self.subTest(nom=nom, debut=contenu[:8]):
                self._refus(contenu, nom)

    def test_pptx_et_odt_a_paragraphes_minuscules(self):
        """Variante de F-017 sur les autres lecteurs : réponse en moins de 30 s."""
        bloc = b"<text:p>a</text:p>" * 30000
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("content.xml", b'<?xml version="1.0"?><office:document-content '
                       b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                       b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
                       + bloc * (15 * MO // len(bloc)) + b"</office:text></office:body></office:document-content>")
        r, duree = self.deposer(buf.getvalue(), "cours.odt")
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30)

    def test_nosniff_sur_les_autres_telechargements(self):
        self.client.post("/stages/offres", data={"title": "Offre", "description": "x"})
        self.client.post("/stages/lettres", data={"offer_id": "1"})
        for url in ("/stages/lettres/1/telecharger", "/planning/export.ics", "/"):
            with self.subTest(url=url):
                r = self.client.get(url)
                r.close()
                self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")


class VariantesQuotaTests(_AvecMatiere):
    """Variantes de F-019, avec un quota réduit à 1 Mo (USER_QUOTA_BYTES) pour ne pas écrire 200 Mo."""

    config = {**CONFIG, "USER_QUOTA_BYTES": MO}

    def _total(self):
        return sum(os.path.getsize(p) for p in self.fichiers_deposes())

    def test_plusieurs_fichiers_dans_un_seul_envoi(self):
        fichiers = [(io.BytesIO(b" " * (400 * 1024)), f"f{i}.txt") for i in range(4)]
        self.client.post(f"/matieres/{self.sid}/documents", data={"documents": fichiers},
                         content_type="multipart/form-data")
        self.assertLessEqual(self._total(), MO)

    def test_plusieurs_matieres(self):
        self.client.post("/matieres/nouvelle", data={"name": "Physique", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})
        sid2 = self.q("SELECT id FROM subjects WHERE user_id = ? AND id != ?", self.uid, self.sid)[0]["id"]
        for i in range(3):
            for s in (self.sid, sid2):
                self.client.post(f"/matieres/{s}/documents",
                                 data={"documents": (io.BytesIO(b" " * (300 * 1024)), f"f{s}{i}.txt")},
                                 content_type="multipart/form-data")
        self.assertLessEqual(self._total(), MO)

    def test_envois_simultanes(self):
        """F-034 (contournement de F-019) : le quota est lu avant l'écriture ; 10 envois simultanés le lisent tous avant qu'aucun n'écrive."""
        import threading
        cookie = self.client.get_cookie(self.app.config.get("SESSION_COOKIE_NAME", "session")).value
        depart = threading.Barrier(10)

        def envoi(i):
            c = self.app.test_client()
            c.set_cookie(self.app.config.get("SESSION_COOKIE_NAME", "session"), cookie)
            depart.wait()
            c.post(f"/matieres/{self.sid}/documents", data={"documents": (io.BytesIO(b" " * (300 * 1024)), f"p{i}.txt")},
                   content_type="multipart/form-data")

        fils = [threading.Thread(target=envoi, args=(i,)) for i in range(10)]
        for f in fils:
            f.start()
        for f in fils:
            f.join(60)
        self.assertLessEqual(self._total(), MO, f"{self._total() / MO:.1f} Mo stockés pour un quota de 1 Mo")


class VariantesIcsTests(ImportIcsTests):
    """Variantes de F-020, F-021, F-024."""

    test_fichier_de_plus_de_2_mo_refuse = None
    test_plus_de_5000_evenements_refuses_ou_tronques = None
    test_recurrence_sans_fin_depliee_sur_un_an_au_plus = None
    test_recurrence_partant_de_l_an_1_ne_bloque_pas_le_serveur = None
    test_regles_et_dates_invalides_sans_erreur_500 = None

    def test_autres_regles_depuis_l_an_1(self):
        evs = []
        for i, regle in enumerate(("FREQ=DAILY", "FREQ=DAILY;INTERVAL=3", "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR",
                                   "FREQ=WEEKLY;COUNT=100000000", "FREQ=DAILY;UNTIL=20991231T000000")):
            evs += [evenement(f"v{i}-{k}", f"Vieux {i}", "00010101T100000", "00010101T110000", regle) for k in range(8)]
        r, duree = self.importer(calendrier(*evs))
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30)

    def test_dates_utc_aux_limites(self):
        """F-035 (contournement de F-021) : une date UTC (suffixe Z) en l'an 1 ou en 9999 est convertie en heure
        locale avant le contrôle des limites ; sous Windows, la conversion lève OSError, qui n'est pas attrapée (500)."""
        d = self.demain.strftime("%Y%m%dT%H%M%S")
        cas = [evenement("u1", "Cours", "00010101T100000Z"), evenement("u2", "Cours", "99991231T235959Z"),
               evenement("u3", "Cours", d, None, "FREQ=DAILY;UNTIL=99991231T000000Z"),
               evenement("u4", "Cours", d, "99991231T235959Z")]
        for ev in cas:
            with self.subTest(ev=ev.split("\r\n")[3:5]):
                r, _ = self.importer(calendrier(ev))
                self.assertNotEqual(r.status_code, 500)

    def test_autres_champs_invalides(self):
        d = self.demain.strftime("%Y%m%dT%H%M%S")
        cas = ["BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:X\r\nDTSTART:" + d + "\r\nDURATION:P99999999W\r\nEND:VEVENT\r\n",
               "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:X\r\nDTSTART:" + d + "\r\nEXDATE:abc,99999999T999999\r\n"
               "RRULE:FREQ=DAILY;COUNT=3\r\nEND:VEVENT\r\n",
               "BEGIN:VEVENT\r\nUID:c\r\nSUMMARY:X\r\nDTSTART:" + d + "\r\nRECURRENCE-ID:zz\r\nEND:VEVENT\r\n",
               "BEGIN:VEVENT\r\nUID:d\r\nSUMMARY:X\r\nDTSTART:" + d + "\r\nDTEND:00010101T000000\r\nEND:VEVENT\r\n",
               "BEGIN:VEVENT\r\nUID:e\r\nSUMMARY:X\r\nDTSTART;TZID=Europe/Paris:" + d + "\r\nRRULE:FREQ=WEEKLY;"
               "BYDAY=1MO,-1FR,ZZ;INTERVAL=1\r\nEND:VEVENT\r\n"]
        for ev in cas:
            with self.subTest(ev=ev.split("\r\n")[3][:40]):
                r, _ = self.importer(calendrier(ev))
                self.assertNotEqual(r.status_code, 500)

    def test_limites_taille_et_nombre(self):
        d = self.demain.strftime("%Y%m%dT%H%M%S")
        juste_trop = calendrier(evenement("a", "Cours", d), remplissage=0)
        juste_trop += b"X" * (2 * MO - len(juste_trop) + 1)
        r, _ = self.importer(juste_trop)
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(self.evenements(), [], "fichier de 2 Mo + 1 octet importé")
        evs = [evenement(f"r{i}", f"Cours {i}", (self.demain + timedelta(minutes=37 * i)).strftime("%Y%m%dT%H%M%S"),
                         (self.demain + timedelta(minutes=37 * i + 30)).strftime("%Y%m%dT%H%M%S"), "FREQ=DAILY;COUNT=5")
               for i in range(2000)]
        r, duree = self.importer(calendrier(*evs))
        self.assertNotEqual(r.status_code, 500)
        self.assertLess(duree, 30)
        self.assertLessEqual(len(self.evenements()), 5000, "plus de 5 000 créneaux après dépliage des récurrences")


class VariantesPhotosTests(PhotosTests):
    test_photo_de_plus_de_10_mo_refusee_avant_le_llm = None
    test_plus_de_10_photos_par_envoi = None

    def test_exactement_10_photos_passent_et_11_sont_refusees(self):
        def photos(n):
            return [(io.BytesIO(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 50), f"p{i}.png", "image/png") for i in range(n)]
        self.client.post(f"/matieres/{self.sid}/documents/photo", data={"photos": photos(10)},
                         content_type="multipart/form-data")
        self.assertEqual(self.ask.call_count, 10, "témoin : 10 photos acceptées")
        self.client.post(f"/matieres/{self.sid}/documents/photo", data={"photos": photos(11)},
                         content_type="multipart/form-data")
        self.assertEqual(self.ask.call_count, 10)

    def test_photo_de_10_mo_plus_un_octet(self):
        grosse = b"\x89PNG\r\n\x1a\n" + b"\0" * (10 * MO - 7)
        for url, champ in (("/agenda/photo", "photo"), (f"/matieres/{self.sid}/documents/photo", "photos")):
            with self.subTest(url=url):
                self.client.post(url, data={champ: (io.BytesIO(grosse), "x.png", "image/png")},
                                 content_type="multipart/form-data")
        self.assertTrue(all(len(c.args[0]) <= 10 * MO for c in self.ask.call_args_list))


class VariantesNomsTests(_AvecMatiere):
    def test_noms_longs_et_unicode(self):
        for nom, contenu in (("é" * 300 + ".txt", b"# T\nx"), ("a" * 3000 + ".md", b"# T\nx"),
                             ("x" * 500 + ".pdf", pdf(1)), ("中文" * 100 + ".txt", b"# T\nx"), (".." * 200 + ".txt", b"x")):
            with self.subTest(longueur=len(nom)):
                r, _ = self.deposer(contenu, nom)
                self.assertNotEqual(r.status_code, 500)
        dossier = os.path.realpath(os.path.join(self.app.config["UPLOAD_FOLDER"], str(self.uid)))
        for chemin in self.fichiers_deposes():
            self.assertEqual(os.path.dirname(os.path.realpath(chemin)), dossier)


if __name__ == "__main__":
    unittest.main()
