"""BDD-06 — Valeurs extrêmes dans chaque formulaire.

Chaque champ de chaque formulaire reçoit tour à tour : 1 Mo de texte, des caractères de contrôle, des emoji,
0000-01-01, 9999-12-31, NaN, inf, -inf, 1e309, 2^63, -5, une chaîne vide. Les autres champs gardent une valeur
valide. Attendu : jamais de 500 ; et aucune valeur aberrante écrite en base (nombre non fini ou hors bornes,
date hors de 1900-2200, fin avant début, texte de 1 Mo).
"""
import math
import unittest
from datetime import date, timedelta

from ._outils import AppSecuTestCase

MO = 1024 * 1024
EXTREMES = {"1 Mo": "a" * MO, "contrôle": "\x00\x01\x1b[31m\x7f‮", "emoji": "📚🔥" * 10,
            "0000-01-01": "0000-01-01", "9999-12-31": "9999-12-31", "NaN": "NaN", "inf": "inf", "-inf": "-inf",
            "1e309": "1e309", "2^63": str(2 ** 63), "-5": "-5", "vide": ""}
JOUR = (date.today() + timedelta(days=5)).isoformat()
HIER = (date.today() - timedelta(days=1)).isoformat()
FORMULAIRES = [
    ("/preferences", {"method": "active_recall", "session_minutes": "40", "break_minutes": "5",
                      "daily_max_minutes": "120", "day_start": "08:00", "day_end": "20:00", "rest_days": "6",
                      "buffer_minutes": "10", "horizon_weeks": "4"}),
    ("/dossier/notes", {"course": "Analyse", "subject_id": "1", "value": "12", "out_of": "20", "coefficient": "1",
                        "label": "DS", "day": HIER}),
    ("/cafeine/ajouter", {"custom_label": "Maté", "custom_mg": "80", "time": "10:00"}),
    ("/cafeine/sommeil", {"bedtime": "23:00", "wake_time": "07:00"}),
    ("/agenda/ajouter", {"title": "Cours", "kind": "cours", "subject": "1", "date": JOUR, "start": "10:00",
                         "end": "12:00", "repeat_weeks": "2"}),
    ("/matieres/nouvelle", {"name": "Physique", "goal": "exam", "exam_date": JOUR, "comfort": "3",
                            "weekly_hours": "3", "topics": "A\nB"}),
    ("/matieres/1/modifier", {"name": "Analyse", "goal": "exam", "exam_date": JOUR, "comfort": "3",
                              "weekly_hours": "3", "topics": "A\nB"}),
    ("/matieres/1/examens", {"date": JOUR, "start": "08:00", "end": "10:00", "title": "Partiel"}),
    ("/profil/infos", {"full_name": "Léa", "birth_date": "2004-05-06", "phone": "0600000000", "home_city": "Rennes",
                       "website": "https://exemple.fr"}),
    ("/stages/profil", {"goal": "Data", "skills": "Python", "duration": "2 mois", "start": "juin"}),
    ("/stages/postes", {"title": "Juriste", "location": "Rennes"}),
    ("/stages/offres", {"title": "Offre", "company": "Cabinet", "description": "Texte"}),
    ("/reviser/1/niveau", {"difficulte": "moyen"}),
    ("/campus/signaler", {"place": "bu", "level": "2"}),
]
TEXTE_MAX = 100_000


def _nombre(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class ValeursExtremesTests(AppSecuTestCase):
    config = {"PROPAGATE_EXCEPTIONS": False}

    def setUp(self):
        super().setUp()
        self.inscrire("eleve@test.fr")
        self.client.post("/matieres/nouvelle", data={"name": "Analyse", "goal": "a_jour", "weekly_hours": "2",
                                                     "comfort": "3", "topics": "A\nB"})

    def test_ni_500_ni_valeur_aberrante(self):
        """Un seul passage sur tous les champs : d'abord les 500 (sous-tests), puis l'état de la base."""
        for url, base in FORMULAIRES:
            for champ in base:
                for nom, valeur in EXTREMES.items():
                    with self.subTest(route=url, champ=champ, valeur=nom):
                        r = self.client.post(url, data={**base, champ: valeur})
                        self.assertNotEqual(r.status_code, 500, f"{url} {champ}={nom}")
        problemes = []

        def annee_ok(v):
            return v is None or v == "" or (str(v)[:4].isdigit() and 1900 <= int(str(v)[:4]) <= 2200)

        for t in [r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type='table' "
                                            "AND name NOT LIKE 'sqlite_%' AND name != 'auth_attempts'")]:
            for row in self.q(f"SELECT * FROM {t}"):
                for k, v in row.items():
                    if isinstance(v, float) and not math.isfinite(v):
                        problemes.append(f"{t}.{k} = {v}")
                    elif isinstance(v, (int, float)) and abs(v) > 10 ** 12 and k not in ("at",):
                        problemes.append(f"{t}.{k} = {v}")
                    elif isinstance(v, str) and len(v) > TEXTE_MAX:
                        problemes.append(f"{t}.{k} : texte de {len(v)} caractères")
                    elif k in ("start", "end", "day", "exam_date", "exam_manual", "birth_date", "taken_at") \
                            and not annee_ok(v):
                        problemes.append(f"{t}.{k} = {str(v)[:30]}")
                if t in ("events", "sessions") and row["end"] <= row["start"]:
                    problemes.append(f"{t} : fin {row['end']} avant début {row['start']}")
                if t == "grades" and not (0 <= row["value"] <= row["out_of"] and row["coefficient"] > 0):
                    problemes.append(f"grades : {row['value']}/{row['out_of']} coef {row['coefficient']}")
                if t == "caffeine_intakes" and not (0 < row["mg"] <= 2000):
                    problemes.append(f"caffeine_intakes.mg = {row['mg']}")
        self.assertEqual(sorted(set(problemes))[:20], [], f"{len(set(problemes))} valeurs aberrantes")


if __name__ == "__main__":
    unittest.main()
