"""Construit app/data/ecoles_france.json : la liste des établissements scolaires et
d'enseignement supérieur de France, pour l'autocomplétion du profil.

Sources (données publiques, Licence Ouverte) :
- Annuaire de l'éducation (data.education.gouv.fr) : collèges et lycées ;
- Principaux établissements d'enseignement supérieur (data.enseignementsup-recherche.gouv.fr) : avec sigles ;
- Parcoursup : tous les établissements proposant une formation post-bac (BTS, prépas, IUT, écoles…) ;
- Mon Master : établissements délivrant des masters ;
- Effectifs d'étudiants inscrits par établissement ;
- une courte liste de grandes écoles hors de ces jeux de données (ENSAI, ENSAE…).

Usage : python scripts/build_schools.py   (à relancer de temps en temps pour mettre à jour)
"""
import json
import os
import re
import sys
import unicodedata

import requests

UA = {"User-Agent": "PlanificateurRevisions/1.0 (application educative)"}
ESR = "https://data.enseignementsup-recherche.gouv.fr/api/explore/v2.1/catalog/datasets"
EDU = "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets"
OUT = os.path.join(os.path.dirname(__file__), "..", "app", "data", "ecoles_france.json")

# Grandes écoles souvent absentes des jeux de données du ministère de l'Enseignement supérieur
EXTRA = [
    ("École nationale de la statistique et de l'analyse de l'information", "Bruz", "École", "ENSAI"),
    ("École nationale de la statistique et de l'administration économique", "Palaiseau", "École", "ENSAE Paris"),
    ("École polytechnique", "Palaiseau", "École", "X"),
    ("Institut national du service public", "Strasbourg", "École", "INSP"),
    ("École nationale de l'aviation civile", "Toulouse", "École", "ENAC"),
    ("École nationale supérieure de techniques avancées", "Palaiseau", "École", "ENSTA Paris"),
    ("Mines Paris - PSL", "Paris", "École", "Mines Paris"),
    ("École nationale des ponts et chaussées", "Champs-sur-Marne", "École", "Ponts ParisTech"),
    ("AgroParisTech", "Palaiseau", "École", ""),
    ("Sciences Po", "Paris", "École", ""),
    ("Sciences Po Rennes", "Rennes", "École", "IEP Rennes"),
    ("École spéciale militaire de Saint-Cyr", "Guer", "École", "Saint-Cyr"),
    ("École navale", "Brest", "École", ""),
    ("École de l'air et de l'espace", "Salon-de-Provence", "École", ""),
    ("École nationale de la magistrature", "Bordeaux", "École", "ENM"),
    ("École des hautes études en santé publique", "Rennes", "École", "EHESP"),
]


def export(base: str, dataset: str, select: str, where: str | None = None) -> list[dict]:
    params = {"select": select}
    if where:
        params["where"] = where
    r = requests.get(f"{base}/{dataset}/exports/json", params=params, headers=UA, timeout=180)
    r.raise_for_status()
    return r.json()


def key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def main():
    schools: dict[str, list] = {}

    def add(name, city="", kind="", sigle=""):
        name = re.sub(r"\s+", " ", (name or "")).strip()
        if len(name) < 3:
            return
        k = key(name) + "|" + key(city or "")
        cur = schools.get(k)
        if cur is None:
            schools[k] = [name, (city or "").strip().title() if (city or "").isupper() else (city or "").strip(),
                          kind, sigle or ""]
        elif sigle and not cur[3]:
            cur[3] = sigle

    steps = [
        ("Collèges et lycées", lambda: [add(r["nom_etablissement"], r["nom_commune"], r["type_etablissement"])
                                        for r in export(EDU, "fr-en-annuaire-education",
                                                        "nom_etablissement,nom_commune,type_etablissement",
                                                        'type_etablissement in ("Collège","Lycée")')]),
        ("Principaux établissements du supérieur", lambda: [
            add(r["uo_lib"], (r.get("localisation") or "").split(">")[-1], "Supérieur", r.get("sigle") or "")
            for r in export(ESR, "fr-esr-principaux-etablissements-enseignement-superieur", "uo_lib,sigle,localisation")]),
        ("Parcoursup", lambda: [add(r["g_ea_lib_vx"], r.get("ville_etab"), "Supérieur")
                                for r in export(ESR, "fr-esr-parcoursup", "g_ea_lib_vx,ville_etab")]),
        ("Mon Master", lambda: [add(r["eta_nom"], "", "Supérieur")
                                for r in export(ESR, "fr-esr-mon_master", "eta_nom")]),
        ("Effectifs par établissement", lambda: [
            add(r["etablissement_actuel_lib"], r.get("etablissement_commune"), "Supérieur")
            for r in export(ESR, "fr-esr-statistiques-sur-les-effectifs-d-etudiants-inscrits-par-etablissement",
                            "etablissement_actuel_lib,etablissement_commune")]),
    ]
    for label, step in steps:
        before = len(schools)
        try:
            step()
            print(f"{label} : +{len(schools) - before}")
        except requests.RequestException as e:
            print(f"{label} : ÉCHEC ({e})", file=sys.stderr)
    for row in EXTRA:
        add(*row)
    # Un même établissement peut apparaître avec et sans ville : la version sans ville est retirée
    # si une version avec ville existe. Les homonymes de villes différentes (les nombreux
    # « Collège Jean Moulin ») sont tous conservés.
    groups: dict[str, list[list]] = {}
    for row in schools.values():
        groups.setdefault(key(row[0]), []).append(row)
    rows = []
    for group in groups.values():
        with_city = [r for r in group if r[1]]
        keep = with_city or group[:1]
        sigle = next((r[3] for r in group if r[3]), "")
        for r in keep:
            r[3] = r[3] or sigle
        rows += keep
    rows.sort(key=lambda r: (key(r[0]), key(r[1])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": "data.education.gouv.fr, data.enseignementsup-recherche.gouv.fr (Licence Ouverte)",
                   "fields": ["nom", "ville", "type", "sigle"], "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(rows)} établissements écrits dans {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
