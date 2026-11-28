"""Migration 2 (SEC-03) : additive, sur une base existante avec des données, et réversible."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from backend import create_app


class MigrationSessionVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"SECRET_KEY": "cle-de-test-" + "x" * 20, "ADMIN_EMAIL": "admin@test.fr",
                                           "ADMIN_PASSWORD": "Phare-Ecluse-Bruine-77"})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = {"TESTING": True, "ENABLE_WEB": False, "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
                    "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")}

    def _db(self):
        db = sqlite3.connect(self.cfg["DATABASE"])
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)  # fermée même si une assertion échoue (sinon Windows garde le fichier verrouillé)
        return db

    def test_existing_v1_database_is_migrated_without_data_loss_and_can_be_reverted(self):
        create_app(self.cfg)
        db = self._db()
        # Ramène la base à l'état de la version 1 (ce qui teste aussi l'annulation de la migration)
        db.execute("ALTER TABLE users DROP COLUMN session_version")
        db.execute("DROP TABLE auth_attempts")
        db.execute("DROP TABLE llm_usage")  # migration 3 annulée aussi
        db.execute("PRAGMA user_version = 1")
        db.execute("INSERT INTO users (email, username, password_hash) VALUES ('ancien@test.fr', 'Ancien', 'h')")
        db.commit()
        db.close()

        create_app(self.cfg)  # relance : migration 2
        db = self._db()
        self.assertGreaterEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)  # migrations 2 et 3 rejouées
        row = db.execute("SELECT username, password_hash, session_version FROM users WHERE email = 'ancien@test.fr'")
        self.assertEqual(tuple(row.fetchone()), ("Ancien", "h", 0))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_attempts").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0], 0)
        db.close()

        create_app(self.cfg)  # idempotente
        db = self._db()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE email = 'ancien@test.fr'").fetchone()[0], 1)
        db.close()


class LimiterStatePerAppTests(unittest.TestCase):
    """L'état de la limitation est dans la base de chaque application : deux applications ne se gênent pas."""

    def test_two_apps_do_not_share_attempts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"SECRET_KEY": "k" * 40, "ADMIN_EMAIL": "admin@test.fr",
                                          "ADMIN_PASSWORD": "Phare-Ecluse-Bruine-77"}):
            apps = [create_app({"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
                                "DATABASE": os.path.join(tmp.name, f"{i}.sqlite"),
                                "UPLOAD_FOLDER": os.path.join(tmp.name, f"up{i}")}) for i in (1, 2)]
            first = apps[0].test_client()
            for _ in range(6):
                r = first.post("/connexion", data={"email": "x@test.fr", "password": "Mauvais-Mot-De-Passe"})
            self.assertEqual(r.status_code, 429)
            r = apps[1].test_client().post("/connexion", data={"email": "x@test.fr", "password": "Mauvais-Mot-De-Passe"})
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
