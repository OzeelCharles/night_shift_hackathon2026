"""Outils de l'assistant fournis par la recherche de stage : où en est la recherche, et propositions de postes.

L'assistant ne voit pas les offres des sites d'emploi (ils interdisent la lecture automatique de leurs
résultats) : il propose des intitulés de postes adaptés à la description de l'étudiant et à son profil, et
l'outil affiche sous la réponse les recherches préremplies sur chaque site. Ajouter ces postes à la liste de
la page Stages se fait seulement si l'étudiant clique sur « Choisir ».
Les coordonnées (téléphone, e-mail) ne sont jamais envoyées au modèle.
"""
from __future__ import annotations

import re

from flask import url_for

from backend.core.tools import Action, Tool, params, text_param
from backend.dao import actions as actions_dao

from . import letters, service, sources, store

KIND = "stage_wishes"
MAX_TITLES = 5


def _profile(user) -> dict:
    """Ce que le compte et le profil candidat disent de l'étudiant (sans coordonnées), champs vides retirés."""
    me, p = letters.candidate(user), store.profile(user["id"])
    out = {"niveau": me["level"], "etablissement": me["school"], "ville_d_etudes": me["study_city"],
           "ville": me["home_city"], "domaine": me["field"], "formation": me["program"], "langues": me["languages"],
           "permis": me["license"], "metier_vise": p.get("goal"), "competences": p.get("skills"),
           "experiences": p.get("experiences"), "projets": p.get("projects"), "qualites": p.get("qualities"),
           "debut_souhaite": p.get("start"), "duree_souhaitee": p.get("duration")}
    return {k: v for k, v in out.items() if v}


def _page_link() -> dict:
    return {"label": "Ma recherche de stage", "url": url_for("stages.index")}


def ma_recherche_de_stage(user, **_) -> dict:
    uid = user["id"]
    page = service.page(user)
    return {
        "profil": _profile(user),
        "infos_manquantes_dans_le_compte": page["missing"],
        "postes_recherches": [w["title"] + (f" ({w['location']})" if w["location"] else "") for w in page["wishes"]],
        "offres_suivies": [{"poste": o["title"], "entreprise": o["company"], "lieu": o["location"],
                            "statut": service.STATUSES.get(o["status"], o["status"]), "ajoutee_le": o["created_at"][:10],
                            "lettres": o["n_letters"]} for o in store.offers(uid)],
        "lettres_de_motivation": len(page["letters"]),
        "remarque": "Sur la page Stages, l'étudiant peut suivre ses offres, changer leur statut et générer une lettre "
                    "de motivation pour chacune.",
        "links": [{"title": "Stages", "items": [_page_link()]}],
    }


def _titles(postes: str) -> list[str]:
    out: list[str] = []
    for t in re.split(r"[;\n|]+", postes or ""):
        t = " ".join(t.split()).strip(" -•,.")[:80]
        if len(t) >= 2 and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:MAX_TITLES]


def proposer_stages(user, description: str = "", postes: str = "", lieu: str = "", **_) -> dict:
    uid = user["id"]
    me = letters.candidate(user)
    location = " ".join((lieu or "").split())[:80] or me["home_city"] or me["study_city"]
    base = {"profil": _profile(user), "description_de_l_etudiant": description or "(aucune)",
            "lieu": location or "non précisé"}
    titles = _titles(postes)
    if not titles:
        return base | {"consigne": "Déduis de sa description et de son profil 3 à 5 intitulés de postes de stage "
                                   "précis (tels qu'on les tape sur un site d'offres), puis rappelle proposer_stages "
                                   "avec « postes » séparés par des points-virgules."}
    links = [{"title": t + (f" · {location}" if location else ""),
              "items": [{"label": l["site"], "url": l["url"]} for l in sources.search_links(t, location)]}
             for t in titles]
    out = base | {
        "postes_proposes": titles,
        "consigne": "Pour chaque poste, explique en une phrase pourquoi il correspond à sa description et à son profil "
                    "(compétences à mettre en avant). Les recherches préremplies sur les sites d'offres s'affichent "
                    "sous ta réponse. Tu ne vois pas les offres réelles : n'invente ni entreprise, ni offre, ni salaire.",
        "links": links,
    }
    known = {w["title"].lower() for w in store.wishes(uid)}
    new = [t for t in titles if t.lower() not in known]
    if not new:
        out["remarque"] = "Ces postes sont déjà dans sa liste (page Stages)."
        return out
    action_id = actions_dao.create(uid, KIND, {"titles": new, "location": location})
    out["action"] = {
        "id": action_id, "kind": KIND, "title": "Ajouter à ta recherche de stage", "notes": [],
        "options": [{"key": "ajouter", "label": f"Ajouter {len(new)} poste(s) à ma liste",
                     "details": [t + (f" · {location}" if location else "") for t in new],
                     "warning": ""}],
    }
    return out


def apply(user, action_id: int, choice: str) -> tuple[str | None, str | None]:
    """Ajoute les postes proposés à la liste de la page Stages (sans doublon)."""
    action = actions_dao.get_owned(action_id, user["id"])
    if action is None or action["kind"] != KIND:
        return None, "Proposition introuvable."
    if action["status"] != "pending":
        return None, "Cette proposition a déjà été traitée."
    if choice != "ajouter":
        return None, "Option inconnue."
    location = action["payload"].get("location") or ""
    known = {(w["title"].lower(), w["location"].lower()) for w in store.wishes(user["id"])}
    added = [t for t in action["payload"]["titles"] if (t.lower(), location.lower()) not in known]
    for t in added:
        store.add_wish(user["id"], t, location)
    actions_dao.close(action_id, user["id"], "done", choice)
    if not added:
        return "Ces postes étaient déjà dans ta liste : rien à ajouter.", None
    return (f"C'est fait : {len(added)} poste(s) ajouté(s) à ta recherche de stage ({', '.join(added)}). "
            "Sur la page Stages, tu retrouves les recherches préremplies sur chaque site, le suivi de tes "
            "candidatures et les lettres de motivation."), None


TOOLS = [
    Tool("ma_recherche_de_stage",
         "Recherche de stage de l'étudiant : son profil candidat, les postes qu'il recherche, les offres qu'il suit "
         "(avec leur statut : à postuler, envoyée, à relancer, entretien…) et ses lettres de motivation.",
         ma_recherche_de_stage),
    Tool("proposer_stages",
         "Propose des stages adaptés à ce que l'étudiant décrit (envies, domaine, compétences, lieu) et à son profil : "
         "affiche pour chaque intitulé de poste les recherches préremplies sur Indeed, LinkedIn, Welcome to the Jungle, "
         "JobTeaser… et propose de les ajouter à sa liste. Passe dans « postes » 3 à 5 intitulés précis que tu déduis "
         "de sa description.",
         proposer_stages,
         params(description=text_param("Ce que l'étudiant cherche, avec ses mots (facultatif)."),
                postes=text_param("Intitulés de postes séparés par des points-virgules, ex. « Data analyst ; "
                                  "Assistant chargé d'études statistiques »."),
                lieu=text_param("Ville ou région souhaitée (facultatif ; par défaut sa ville)."))),
]
ACTIONS = [Action(KIND, apply, "D'accord, je n'ajoute rien à ta liste.")]
