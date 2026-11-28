"""BDD-01 — Contraintes d'intégrité au niveau SQL.

La base est créée par l'application (schéma et migrations), puis des lignes incohérentes sont insérées
directement par sqlite3, sans passer par la validation Python : la base doit les refuser d'elle-même.
"""
import sqlite3
import unittest

from ._outils import AppSecuTestCase


class ContraintesTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A\nB"})
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]
        self.sid = self.q("SELECT id FROM subjects")[0]["id"]

    def _base(self):
        db = sqlite3.connect(self.app.config["DATABASE"])
        db.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(db.close)
        return db

    def _refuse(self, sql, *args):
        db = self._base()
        with self.assertRaises(sqlite3.IntegrityError, msg=f"accepté par la base : {sql} {args}"):
            db.execute(sql, args)
            db.commit()
        db.rollback()

    def _accepte(self, sql, *args):
        db = self._base()
        db.execute(sql, args)
        db.rollback()

    def test_notes(self):
        sql = ("INSERT INTO grades (user_id, subject_id, course, value, out_of, coefficient, label, day) "
               "VALUES (?, ?, 'Analyse', ?, ?, ?, '', '2026-01-10')")
        for valeur, bareme, coef in ((-1, 20, 1), (25, 20, 1), (10, 0, 1), (10, -20, 1), (10, 20, 0), (10, 20, -2)):
            with self.subTest(value=valeur, out_of=bareme, coefficient=coef):
                self._refuse(sql, self.uid, self.sid, valeur, bareme, coef)
        self._accepte(sql, self.uid, self.sid, 14.5, 20, 2)  # témoin

    def test_evenements_et_seances(self):
        ev = "INSERT INTO events (user_id, title, start, end, source) VALUES (?, 'Cours', ?, ?, 'manuel')"
        se = ("INSERT INTO sessions (user_id, subject_id, start, end, topic, kind, activity) "
              "VALUES (?, ?, ?, ?, 'A', 'study', 'lire')")
        mauvais = [("2030-01-07T10:00", "2030-01-07T10:00"), ("2030-01-07T10:00", "2030-01-07T09:00"),
                   ("demain", "après-demain"), ("", "2030-01-07T10:00")]
        for debut, fin in mauvais:
            with self.subTest(table="events", start=debut, end=fin):
                self._refuse(ev, self.uid, debut, fin)
            with self.subTest(table="sessions", start=debut, end=fin):
                self._refuse(se, self.uid, self.sid, debut, fin)
        self._accepte(ev, self.uid, "2030-01-07T10:00", "2030-01-07T12:00")
        self._accepte(se, self.uid, self.sid, "2030-01-07T10:00", "2030-01-07T11:00")

    def test_preferences_hors_bornes(self):
        cas = {"session_minutes": (9, 241), "break_minutes": (-1, 61), "daily_max_minutes": (29, 961),
               "horizon_weeks": (0, 27)}
        for colonne, valeurs in cas.items():
            for v in valeurs:
                with self.subTest(colonne=colonne, valeur=v):
                    self._refuse(f"UPDATE preferences SET {colonne} = ? WHERE user_id = ?", v, self.uid)

    def test_utilisateurs(self):
        sql = "INSERT INTO users (email, username, password_hash, role) VALUES (?, 'Doublon', 'x', 'user')"
        for email in ("ELEVE@TEST.FR", "Eleve@Test.fr"):
            with self.subTest(email=email):
                self._refuse(sql, email)
        with self.subTest(is_active=2):
            self._refuse("UPDATE users SET is_active = 2 WHERE id = ?", self.uid)

    def test_cles_etrangeres_actives_sur_les_connexions_de_l_application(self):
        from backend.dao.db import get_db
        with self.app.app_context():
            self.assertEqual(get_db().execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.app.test_request_context("/"):
            self.assertEqual(get_db().execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_cle_etrangere_en_arriere_plan(self):
        """Le traitement en arrière-plan (recherche de ressources) ouvre sa connexion dans un contexte
        d'application : on rejoue ce chemin dans un fil séparé."""
        import threading
        from backend.dao.db import get_db
        resultat = {}

        def fil():
            with self.app.app_context():
                resultat["fk"] = get_db().execute("PRAGMA foreign_keys").fetchone()[0]

        t = threading.Thread(target=fil)
        t.start()
        t.join(10)
        self.assertEqual(resultat.get("fk"), 1)


if __name__ == "__main__":
    unittest.main()
