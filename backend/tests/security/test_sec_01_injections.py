"""SEC-01 — Injections SQL et fuite de données (constat F-13).

1. Charges SQL, jokers LIKE, octet nul et chaînes de 100 000 caractères envoyées dans chaque paramètre GET et
   chaque champ de formulaire connu, sur toutes les routes de app.url_map : jamais de 500, jamais de trace SQL,
   aucune ligne d'un autre utilisateur modifiée, aucune table supprimée.
2. Affectation de masse : /profil et /profil/infos ignorent role, is_active, password_hash, email.
3. Test statique : tout SQL construit dynamiquement (f-string, %, .format, concaténation) est dans la liste
   des emplacements relus, dont les noms viennent de listes blanches codées en dur.
"""
import ast
import io
import json
import os
import re
import unittest

from ._outils import ADMIN_EMAIL, ADMIN_MDP, AppSecuTestCase

CHARGES = ["' OR 1=1--", '" OR ""="', "'; DROP TABLE users;--", "1 UNION SELECT password_hash FROM users--",
           "%", "_", "a\x00b", "1; ATTACH DATABASE '/tmp/x.db' AS x;--", "x" * 100_000]
CHAMPS = ("back bedtime birth_date break_minutes buffer_minutes chapitre coefficient comfort company content course "
          "custom_label custom_mg daily_max_minutes date day day_end day_start description difficulte drink "
          "driving_license duration email end exam_date experiences field full_name goal home_city horizon_weeks kind "
          "label language languages level location method name next offer_id out_of password password2 phone place "
          "program projects q qualities repeat_weeks rest_days role school session_minutes skills start status "
          "study_city subject subject_id time title tone topics url username value ville wake_time wants_internship "
          "website weekly_hours wish_id chap_0 q0").split()
PARAMS_GET = ("q", "titre", "type", "niveau", "next", "nom", "semaine", "chapitre", "exos", "ville", "telecharger")
TRACES = re.compile(r"sqlite3|OperationalError|IntegrityError|ProgrammingError|syntax error|no such (column|table)"
                    r"|unrecognized token|Traceback|pattern too complex", re.I)


class InjectionsTests(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.inscrire("victime@test.fr")
        self.client.post("/deconnexion")
        self.inscrire("eleve@test.fr")
        c = self.client
        c.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2", "comfort": "3",
                                           "topics": "Suites\nLimites",
                                           "documents": (io.BytesIO(b"# Suites\ntexte\n# Limites\ntexte"), "c.md")},
               content_type="multipart/form-data")
        c.post("/agenda/ajouter", data={"title": "Cours", "kind": "cours", "subject": "1", "date": "2030-01-07",
                                        "start": "10:00", "end": "12:00", "repeat_weeks": "1"})
        c.post("/stages/offres", data={"title": "Offre", "description": "Texte"})
        self.eleve = self.q("SELECT id FROM users WHERE email = 'eleve@test.fr'")[0]["id"]

    def _autres_utilisateurs(self):
        rows = self.q("SELECT * FROM users WHERE id != ? ORDER BY id", self.eleve)
        return json.dumps(rows, sort_keys=True, default=str)

    def _tables(self):
        return sorted(r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type = 'table'"))

    def _routes(self, admin: bool):
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint == "static" or rule.rule == "/deconnexion" or rule.rule.startswith("/admin") != admin:
                continue
            if admin and rule.endpoint == "admin.user_delete":
                continue  # supprimerait des comptes : couvert par SEC-02
            url = rule.rule
            for a in rule.arguments:
                url = re.sub(rf"<(?:int:)?{a}>", "1", url)
            for m in sorted(rule.methods & {"GET", "POST"}):
                yield url, m

    def _attaquer(self, routes):
        tables, autres = self._tables(), self._autres_utilisateurs()
        for url, methode in routes:
            for charge in CHARGES:
                with self.subTest(route=url, methode=methode, charge=charge[:40]):
                    if methode == "GET":
                        r = self.client.get(url, query_string={p: charge for p in PARAMS_GET})
                    else:
                        r = self.client.post(url, data={c: charge for c in CHAMPS})
                    self.assertNotEqual(r.status_code, 500, f"{methode} {url} avec {charge[:40]!r}")
                    corps = r.get_data(as_text=True) if r.status_code != 302 else ""
                    m = TRACES.search(corps)
                    self.assertIsNone(m, f"{methode} {url} : trace « {m and m.group(0)} » dans la réponse")
        self.assertEqual(tables, self._tables(), "une table a disparu")
        self.assertEqual(autres, self._autres_utilisateurs(), "des comptes d'autres utilisateurs ont changé")

    def test_charges_dans_toutes_les_routes_utilisateur(self):
        self._attaquer(list(self._routes(admin=False)))

    def test_charges_dans_les_routes_admin(self):
        self.connecter(ADMIN_EMAIL, ADMIN_MDP)
        self.eleve = self.q("SELECT id FROM users WHERE email = ?", ADMIN_EMAIL)[0]["id"]
        self._attaquer(list(self._routes(admin=True)))

    def test_jokers_like_ne_revelent_pas_les_donnees_d_autrui(self):
        """`%` et `_` dans les API de suggestion ne renvoient que les données de l'utilisateur connecté."""
        autre = self.app.test_client()
        self.connecter("victime@test.fr", client=autre)
        autre.post("/agenda/ajouter", data={"title": "Cours SECRET VICTIME", "kind": "autre", "date": "2030-01-09",
                                            "start": "10:00", "end": "11:00", "repeat_weeks": "1"})
        autre.post("/matieres/nouvelle", data={"name": "Matiere SECRET VICTIME", "goal": "a_jour",
                                               "weekly_hours": "2", "comfort": "3", "topics": "A"})
        for url, param in (("/api/suggestions/matieres", "q"), ("/api/agenda/rattachement", "titre")):
            for joker in ("%", "_", "%SECRET%", "' OR 1=1--"):
                with self.subTest(url=url, valeur=joker):
                    r = self.client.get(url, query_string={param: joker})
                    self.assertNotEqual(r.status_code, 500)
                    self.assertNotIn("SECRET VICTIME", r.get_data(as_text=True))

    def test_charges_dans_le_nom_de_fichier_et_le_titre_ics(self):
        from datetime import date, timedelta
        jour = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
        for charge in CHARGES[:4]:
            with self.subTest(charge=charge):
                r = self.client.post("/matieres/1/documents", data={"documents": (io.BytesIO(b"# T\nx"), charge + ".txt")},
                                     content_type="multipart/form-data")
                self.assertNotEqual(r.status_code, 500)
                ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:" + charge + "\r\nSUMMARY:" + charge +
                       "\r\nDTSTART:" + jour + "T100000\r\nDTEND:" + jour + "T110000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
                r = self.client.post("/agenda/import", data={"ics": (io.BytesIO(ics.encode()), "e.ics")},
                                     content_type="multipart/form-data")
                self.assertNotEqual(r.status_code, 500)
        self.assertIn("users", self._tables())
        titres = [e["title"] for e in self.q("SELECT title FROM events")]
        self.assertIn("'; DROP TABLE users;--", titres, "la charge est stockée telle quelle (paramètre ?)")


class AffectationDeMasseTests(AppSecuTestCase):
    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.avant = self.q("SELECT id, email, role, is_active, password_hash FROM users WHERE email = 'eleve@test.fr'")[0]

    def test_profil_ignore_les_champs_sensibles(self):
        pirate = {"role": "admin", "is_active": "0", "password_hash": "pbkdf2:sha256:1$x$y", "email": "autre@test.fr",
                  "id": "1", "session_version": "0"}
        self.client.post("/profil", data={"username": "Eleve test", "level": "master", "school": "ENSAI", **pirate})
        self.client.post("/profil/infos", data={"full_name": "Eleve Test", **pirate})
        apres = self.q("SELECT id, email, role, is_active, password_hash FROM users WHERE id = ?", self.avant["id"])[0]
        self.assertEqual(self.avant, apres)
        self.assertEqual(self.q("SELECT level FROM users WHERE id = ?", self.avant["id"])[0]["level"], "master",
                         "témoin : les champs autorisés sont bien enregistrés")


# ---------------------------------------------------------------- analyse statique
RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/
FONCTIONS_SQL = {"execute", "executescript", "execute_many", "executemany", "query", "one"}
# Emplacements relus (manche 2) : seuls des noms de colonnes / tables issus de listes codées en dur ou des marques « ? »
# sont interpolés. Les SQL faits uniquement de littéraux et de constantes de chaîne (y compris un opérateur choisi
# entre deux littéraux) ne sont pas signalés. Toute nouvelle construction dynamique doit être relue puis ajoutée ici.
RELUS = {
    ("dao/db.py", "query"), ("dao/db.py", "one"), ("dao/db.py", "scalar"),  # enveloppes : transmettent `sql`
    ("dao/db.py", "execute"), ("dao/db.py", "execute_many"),                # (chaque appelant est analysé)
    ("dao/db.py", "install"),                # executescript(schema) : SCHEMA des modules (constantes)
    ("dao/db.py", "add_columns"),            # table / colonne / déclaration issues de MIGRATIONS (constante)
    ("dao/occupancy.py", "recent"),          # autant de « ? » que de clés
    ("dao/preferences.py", "save"),          # FIELDS (constante)
    ("dao/sessions.py", "stats"),            # MINUTES (constante)
    ("dao/sessions.py", "replannable"),      # placeholders(...) : des « ? »
    ("dao/sessions.py", "done_study_minutes"),
    ("dao/sessions.py", "delete_replannable"),
    ("dao/subjects.py", "create"),           # colonnes filtrées par COLUMNS (constante)
    ("dao/subjects.py", "update"),
    ("dao/users.py", "update"),              # colonnes filtrées par une liste blanche locale
    ("plugins/stages/store.py", "save_profile"),  # PROFILE_FIELDS (constante)
}


def _statique(n, noms: set[str]) -> bool:
    """Vrai si l'expression ne contient que du texte écrit dans le code (littéraux, constantes de chaîne,
    opérateur choisi entre deux littéraux) : rien qui puisse venir de l'utilisateur."""
    if isinstance(n, ast.Constant):
        return isinstance(n.value, str)
    if isinstance(n, ast.Name):
        return n.id in noms
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        return _statique(n.left, noms) and _statique(n.right, noms)
    if isinstance(n, ast.IfExp):
        return _statique(n.body, noms) and _statique(n.orelse, noms)
    if isinstance(n, ast.JoinedStr):
        return all(isinstance(v, ast.Constant) or (isinstance(v, ast.FormattedValue) and _statique(v.value, noms))
                   for v in n.values)
    return False


def _noms_statiques(corps: list, connus: set[str]) -> set[str]:
    """Noms affectés une seule fois, à une expression statique (point fixe)."""
    affectations: dict[str, list] = {}
    for n in corps:
        for a in ast.walk(n) if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else []:
            if isinstance(a, ast.Assign) and len(a.targets) == 1 and isinstance(a.targets[0], ast.Name):
                affectations.setdefault(a.targets[0].id, []).append(a.value)
    noms = set(connus)
    while True:
        ajout = {k for k, vs in affectations.items() if k not in noms and len(vs) == 1 and _statique(vs[0], noms)}
        if not ajout:
            return noms
        noms |= ajout


def _sql_dynamique(noeud, noms: set[str] = frozenset()) -> bool:
    return not _statique(noeud, set(noms))


def emplacements_sql_dynamiques() -> set[tuple[str, str]]:
    trouves = set()
    for dossier, _, fichiers in os.walk(RACINE):
        if os.sep + "tests" in dossier or "__pycache__" in dossier:
            continue
        for f in fichiers:
            if not f.endswith(".py"):
                continue
            chemin = os.path.join(dossier, f)
            rel = os.path.relpath(chemin, RACINE).replace(os.sep, "/")
            arbre = ast.parse(open(chemin, encoding="utf-8").read())
            du_module = _noms_statiques(arbre.body, set())
            for fonction in ast.walk(arbre):
                if not isinstance(fonction, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                noms = _noms_statiques(fonction.body, du_module)
                for n in ast.walk(fonction):
                    if isinstance(n, ast.Call) and n.args:
                        nom = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                        if nom in FONCTIONS_SQL and _sql_dynamique(n.args[0], noms):
                            trouves.add((rel, fonction.name))
    return trouves


class AnalyseStatiqueTests(unittest.TestCase):
    def test_sql_dynamique_limite_aux_emplacements_relus(self):
        nouveaux = emplacements_sql_dynamiques() - RELUS
        self.assertEqual(nouveaux, set(), "SQL construit dynamiquement à relire (nom issu d'une liste blanche ?) : "
                                          f"{sorted(nouveaux)}")

    def test_l_analyse_detecte_bien_une_f_string(self):
        """Témoin : l'analyse repère une f-string passée à execute()."""
        arbre = ast.parse('def f(x):\n    execute(f"SELECT * FROM t WHERE a = {x}")\n')
        appel = next(n for n in ast.walk(arbre) if isinstance(n, ast.Call))
        self.assertTrue(_sql_dynamique(appel.args[0]))


if __name__ == "__main__":
    unittest.main()
