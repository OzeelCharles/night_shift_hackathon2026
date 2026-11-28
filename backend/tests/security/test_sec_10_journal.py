"""SEC-10 — Journalisation et audit.

Attendu (cahier) : un journal structuré (JSON, une ligne par événement) des connexions réussies et échouées, des
blocages et des actions admin ; jamais de mot de passe dans le journal ; une table `audit_log` des actions admin,
consultable dans /admin.

Les journaux sont captés sur tous les loggers Python existants après la création de l'application (aucun nom de
logger n'est imposé). Un événement « structuré » est un message qui est un objet JSON.
"""
import json
import logging
import re
import unittest

from ._outils import ADMIN_EMAIL, ADMIN_MDP, MOT_DE_PASSE, AppSecuTestCase

SECRET_ESSAYE = "Mot-De-Passe-Tres-Secret-4821"
NOUVEAU_MDP = "Nouveau-Secret-Journal-7310"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lignes: list[str] = []

    def emit(self, record):
        try:
            texte = record.getMessage()
        except Exception:
            texte = str(record.msg)
        self.lignes.append(texte)
        try:
            self.lignes.append(self.format(record))
        except Exception:
            pass


def objets_json(lignes):
    out = []
    for ligne in lignes:
        for morceau in re.findall(r"\{.*\}", ligne):
            try:
                v = json.loads(morceau)
            except ValueError:
                continue
            if isinstance(v, dict):
                out.append(v)
    return out


class JournalTests(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.capture = _Capture()
        loggers = [logging.getLogger()] + [l for l in logging.Logger.manager.loggerDict.values()
                                          if isinstance(l, logging.Logger)] + [self.app.logger]
        anciens = [(l, l.level) for l in loggers]
        for l in loggers:
            l.addHandler(self.capture)
            if l.level == logging.NOTSET or l.level > logging.INFO:
                l.setLevel(logging.INFO)

        def nettoyer():
            for l, niveau in anciens:
                l.removeHandler(self.capture)
                l.setLevel(niveau)
        self.addCleanup(nettoyer)
        self.inscrire("eleve@test.fr")
        self.client.post("/deconnexion")
        self.uid = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]

    def _pendant(self, action):
        avant = len(self.capture.lignes)
        action()
        return self.capture.lignes[avant:]

    def _contient(self, objets, *mots):
        texte = [json.dumps(o, ensure_ascii=False).lower() for o in objets]
        return any(all(m.lower() in t for m in mots) for t in texte)

    def test_connexion_echouee_journalisee_sans_le_mot_de_passe(self):
        lignes = self._pendant(lambda: self.client.post("/connexion", data={"email": "eleve@test.fr",
                                                                            "password": SECRET_ESSAYE}))
        self.assertTrue(objets_json(lignes), "aucune ligne JSON pour une connexion échouée")
        self.assertFalse(any(SECRET_ESSAYE in l for l in self.capture.lignes), "mot de passe dans le journal")

    def test_connexion_reussie_journalisee(self):
        lignes = self._pendant(lambda: self.connecter("eleve@test.fr"))
        self.assertTrue(objets_json(lignes), "aucune ligne JSON pour une connexion réussie")
        self.assertFalse(any(MOT_DE_PASSE in l for l in self.capture.lignes), "mot de passe dans le journal")

    def test_blocage_journalise(self):
        def attaque():
            for _ in range(6):
                self.client.post("/connexion", data={"email": "eleve@test.fr", "password": SECRET_ESSAYE},
                                 environ_base={"REMOTE_ADDR": "198.51.100.9"})
        lignes = self._pendant(attaque)
        self.assertGreaterEqual(len(objets_json(lignes)), 6, "chaque échec et le blocage doivent être journalisés")

    def test_actions_admin_journalisees_et_dans_audit_log(self):
        admin = self.app.test_client()
        self.connecter(ADMIN_EMAIL, ADMIN_MDP, client=admin)
        actions = [
            ("création", lambda: admin.post("/admin/utilisateurs/nouveau", data={
                "email": "cree@test.fr", "username": "Cree", "password": NOUVEAU_MDP, "role": "user"})),
            ("réinitialisation du mot de passe", lambda: admin.post(f"/admin/utilisateurs/{self.uid}", data={
                "email": "eleve@test.fr", "username": "Eleve eleve", "role": "user", "is_active": "1",
                "password": NOUVEAU_MDP})),
            ("désactivation", lambda: admin.post(f"/admin/utilisateurs/{self.uid}", data={
                "email": "eleve@test.fr", "username": "Eleve eleve", "role": "user"})),
            ("suppression", lambda: admin.post(
                f"/admin/utilisateurs/{self.q('SELECT id FROM users WHERE email = ?', 'cree@test.fr')[0]['id']}/supprimer")),
        ]
        for nom, action in actions:
            with self.subTest(action=nom):
                lignes = self._pendant(action)
                self.assertTrue(objets_json(lignes), f"{nom} : aucune ligne JSON")
        self.assertFalse(any(NOUVEAU_MDP in l for l in self.capture.lignes), "mot de passe dans le journal")
        tables = {r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn("audit_log", tables, "table audit_log absente")
        lignes_audit = self.q("SELECT * FROM audit_log")
        self.assertGreaterEqual(len(lignes_audit), 4, "les 4 actions admin doivent être dans audit_log")
        self.assertFalse(any(NOUVEAU_MDP in json.dumps(r, default=str) for r in lignes_audit))
        pages = " ".join(admin.get(rule.rule).get_data(as_text=True) for rule in self.app.url_map.iter_rules()
                         if rule.rule.startswith("/admin") and "GET" in rule.methods and not rule.arguments)
        self.assertIn("cree@test.fr", pages, "le journal d'audit n'est pas consultable dans /admin")

    def test_journal_inaccessible_a_un_utilisateur_simple(self):
        """Non-régression : /admin reste réservé (le journal d'audit y sera)."""
        self.connecter("eleve@test.fr")
        for rule in self.app.url_map.iter_rules():
            if rule.rule.startswith("/admin") and "GET" in rule.methods and not rule.arguments:
                with self.subTest(route=rule.rule):
                    self.assertEqual(self.client.get(rule.rule).status_code, 403)


if __name__ == "__main__":
    unittest.main()
