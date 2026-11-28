"""BDD-04 / BDD-08 — Transactions, cascades et suppression de compte.

1. Une exception au milieu d'une requête, après une première écriture, annule tout (base et fichiers déposés).
2. Suppression d'un utilisateur par l'admin : plus aucune ligne à lui (y compris rev_*, stg_*, occupancy_reports,
   auth_attempts) ni aucun fichier dans uploads.
3. BDD-08 : l'utilisateur peut supprimer son compte lui-même, avec confirmation par mot de passe.
"""
import hashlib
import io
import json
import os
import re
import unittest
from datetime import date, timedelta
from unittest import mock

from ._outils import ADMIN_EMAIL, ADMIN_MDP, MOT_DE_PASSE, AppSecuTestCase

COURS = ("# Variance\nLa variance mesure la dispersion des valeurs autour de l'espérance.\n"
         "# Lois\nLa loi normale est une loi continue dont la densité a une forme de cloche.\n").encode()


class _AvecDonnees(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def remplir(self, email, client):
        """Une ligne dans chaque table qui dépend de l'utilisateur, et des fichiers déposés."""
        self.inscrire(email, client=client)
        uid = self.q("SELECT id FROM users WHERE email = ?", email)[0]["id"]
        jour = (date.today() + timedelta(days=3)).isoformat()
        client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                "comfort": "3", "topics": "Variance\nLois",
                                                "documents": (io.BytesIO(COURS), "cours.md")},
                    content_type="multipart/form-data")
        sid = self.q("SELECT id FROM subjects WHERE user_id = ?", uid)[0]["id"]
        client.post(f"/matieres/{sid}/documents", data={"documents": (io.BytesIO(COURS), "suite.txt")},
                    content_type="multipart/form-data")
        client.post("/agenda/ajouter", data={"title": "Cours", "kind": "cours", "subject": str(sid), "date": jour,
                                             "start": "10:00", "end": "12:00", "repeat_weeks": "1"})
        client.post(f"/matieres/{sid}/ressources/ajouter", data={"title": "Lien", "url": "https://exemple.fr/a"})
        client.post("/dossier/notes", data={"course": "Analyse", "subject_id": str(sid), "value": "12",
                                            "out_of": "20", "coefficient": "1", "label": "DS"})
        client.post("/cafeine/ajouter", data={"drink": "the"})
        client.post("/stages/profil", data={"skills": "Python", "goal": "Data"})
        client.post("/stages/postes", data={"title": "Juriste", "location": "Rennes"})
        client.post("/stages/offres", data={"title": "Offre", "description": "Texte"})
        client.post("/stages/lettres", data={"offer_id": str(self.q("SELECT id FROM stg_offers WHERE user_id = ?",
                                                                    uid)[0]["id"])})
        client.post(f"/reviser/{sid}/niveau", data={"difficulte": "moyen"})
        client.post(f"/reviser/{sid}/qcm", data={"chapitre": "", "difficulte": "moyen"})
        client.get(f"/reviser/{sid}/fiches")
        with self.app.app_context():
            from backend.dao.db import get_db
            from backend.plugins.revision import store
            store.add_exercise(sid, "Variance", "Exo", "énoncé", "corrigé", "moyen", "test", "", f"e-{uid}")
            store.add_notebook(sid, "Variance", "moyen", "NB", content=json.dumps({"cells": []}))
            store.create_quiz(uid, sid, "Variance", "moyen", [{"type": "def", "chapter": "Variance", "prompt": "Q",
                                                                "quote": "", "options": ["a", "b"], "answer": 0,
                                                                "explanation": ""}])
            get_db().execute("INSERT INTO occupancy_reports (user_id, place_key, level) VALUES (?, 'bu:test', 2)", (uid,))
            get_db().commit()
        client.post("/deconnexion")
        client.post("/connexion", data={"email": email, "password": "Mauvais-Mot-De-Passe-0"},
                    environ_base={"REMOTE_ADDR": "198.51.100.77"})  # une tentative échouée (auth_attempts)
        return uid, sid

    def traces(self, uid, sid, email):
        """Lignes qui appartiennent encore à l'utilisateur, table par table."""
        restes = {}
        tables = [r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type = 'table' "
                                            "AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            cols = [c["name"] for c in self.q(f"PRAGMA table_info({t})")]
            conditions, args = [], []
            if "user_id" in cols:
                conditions.append("user_id = ?")
                args.append(uid)
            if "subject_id" in cols:
                conditions.append("subject_id = ?")
                args.append(sid)
            if t == "users":
                conditions, args = ["id = ? OR lower(email) = ?"], [uid, email]
            if conditions:
                n = self.q(f"SELECT COUNT(*) AS n FROM {t} WHERE " + " OR ".join(conditions), *args)[0]["n"]
                if n:
                    restes[t] = n
        if "auth_attempts" in tables:
            cles = {email, hashlib.sha256(f"login_account\0{email}".encode()).hexdigest()}
            n = sum(self.q("SELECT COUNT(*) AS n FROM auth_attempts WHERE key = ?", k)[0]["n"] for k in cles)
            if n:
                restes["auth_attempts"] = n
        return restes

    def fichiers(self, uid):
        dossier = os.path.join(self.app.config["UPLOAD_FOLDER"], str(uid))
        return [f for _, _, fs in os.walk(dossier) for f in fs] if os.path.isdir(dossier) else []


class TransactionTests(_AvecDonnees):
    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]

    def test_exception_apres_une_ecriture_annule_la_matiere(self):
        from backend.services import planning_service
        with mock.patch.object(planning_service, "update", side_effect=RuntimeError("panne simulée")):
            r = self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour",
                                                             "weekly_hours": "2", "comfort": "3", "topics": "A"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM subjects")[0]["n"], 0, "matière écrite malgré l'erreur")

    def test_exception_apres_import_annule_les_evenements(self):
        from backend.services import planning_service
        d = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
        ics = (f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Cours\r\nDTSTART:{d}T100000\r\n"
               f"DTEND:{d}T110000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
        with mock.patch.object(planning_service, "update", side_effect=RuntimeError("panne simulée")):
            r = self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics), "e.ics")},
                                 content_type="multipart/form-data")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM events")[0]["n"], 0)

    def test_exception_apres_depot_ne_laisse_pas_de_fichier(self):
        """F-037 : la ligne `documents` est annulée, mais le fichier déjà écrit sur le disque reste (orphelin)."""
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A"})
        from backend.services import planning_service
        with mock.patch.object(planning_service, "update", side_effect=RuntimeError("panne simulée")):
            r = self.client.post("/matieres/1/documents", data={"documents": (io.BytesIO(COURS), "cours.md")},
                                 content_type="multipart/form-data")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM documents")[0]["n"], 0)
        self.assertEqual(self.fichiers(self.uid), [], "fichier orphelin laissé dans uploads après l'annulation")


class SuppressionParAdminTests(_AvecDonnees):
    def test_suppression_efface_toutes_les_donnees_et_fichiers(self):
        c = self.app.test_client()
        uid, sid = self.remplir("victime@test.fr", c)
        avant = self.traces(uid, sid, "victime@test.fr")
        for t in ("users", "subjects", "documents", "events", "sessions", "resources", "grades", "caffeine_intakes",
                  "stg_profile", "stg_wishes", "stg_offers", "stg_letters", "rev_quizzes", "rev_exercises",
                  "rev_notebooks", "occupancy_reports"):
            self.assertIn(t, avant, f"préparation : aucune ligne dans {t} (le test ne prouverait rien)")
        self.assertTrue(self.fichiers(uid), "préparation : aucun fichier déposé")
        admin = self.app.test_client()
        self.connecter(ADMIN_EMAIL, ADMIN_MDP, client=admin)
        admin.post(f"/admin/utilisateurs/{uid}/supprimer")
        self.assertEqual(self.traces(uid, sid, "victime@test.fr"), {}, "données restantes après suppression")
        self.assertEqual(self.fichiers(uid), [], "fichiers restants dans uploads")

    def test_suppression_d_une_matiere_garde_les_notes(self):
        """Non-régression : notes conservées avec subject_id = NULL."""
        c = self.app.test_client()
        uid, sid = self.remplir("eleve@test.fr", c)
        self.connecter("eleve@test.fr", client=c, password=MOT_DE_PASSE)
        c.post(f"/matieres/{sid}/supprimer")
        notes = self.q("SELECT subject_id, course FROM grades WHERE user_id = ?", uid)
        self.assertEqual([(n["subject_id"], n["course"]) for n in notes], [(None, "Analyse")])
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM documents")[0]["n"], 0)


class SuppressionParLUtilisateurTests(_AvecDonnees):
    """F-038 (BDD-08) : aucune route ne permet à l'utilisateur de supprimer son compte."""

    def _route(self):
        for rule in self.app.url_map.iter_rules():
            if ("POST" in rule.methods and not rule.rule.startswith("/admin") and not rule.arguments
                    and re.search(r"(compte|profil|account).*(suppr|delete)|(suppr|delete).*(compte|account)",
                                  rule.rule)):
                return rule.rule
        return None

    def test_route_de_suppression_de_compte(self):
        self.assertIsNotNone(self._route(), "aucune route POST de suppression de compte (/profil/supprimer…)")

    def test_suppression_avec_confirmation_par_mot_de_passe(self):
        url = self._route()
        if url is None:
            self.skipTest("route absente (voir test_route_de_suppression_de_compte)")
        c = self.app.test_client()
        uid, sid = self.remplir("moi@test.fr", c)
        self.connecter("moi@test.fr", client=c)
        r = c.post(url, data={"password": "Mauvais-Mot-De-Passe-0"})
        self.assertNotEqual(r.status_code, 500)
        self.assertTrue(self.q("SELECT 1 FROM users WHERE id = ?", uid), "compte supprimé sans le bon mot de passe")
        c.post(url, data={"password": MOT_DE_PASSE, "confirm": "1", "confirmation": "1"})
        self.assertEqual(self.traces(uid, sid, "moi@test.fr"), {})
        self.assertEqual(self.fichiers(uid), [])
        self.assertNotEqual(c.get("/tableau-de-bord").status_code, 200, "session encore ouverte")


if __name__ == "__main__":
    unittest.main()
