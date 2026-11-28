"""Outils de l'assistant pour la vie étudiante : bibliothèque et restos U (Campus), compteur de caféine.

Lecture seule. Les affluences « en direct » viennent des signalements des étudiants ; sans signalement récent,
c'est une fréquentation habituelle estimée, et l'outil le dit pour que l'assistant ne la présente pas comme
une mesure.
"""
from __future__ import annotations

from datetime import datetime

from flask import url_for

from backend.business import caffeine as caffeine_rules
from backend.business import campus as campus_rules
from backend.business import matching
from backend.core.tools import Tool, params, text_param

from . import caffeine_service, campus_service


def _hours(profile: dict) -> str:
    return f"{min(profile)} h - {max(profile) + 1} h" if profile else ""


def _crowd(gauge: dict) -> str:
    """Affluence lisible, avec sa provenance (signalements récents ou estimation)."""
    live = gauge.get("live")
    if live:
        who = f"{live['count']} signalement(s) d'étudiants, le plus récent il y a {live['age']} min"
        return f"{live['label']} ({who}{'' if live['reliable'] else ', à confirmer'})"
    if gauge.get("level"):
        return f"{campus_rules.LEVELS[gauge['level']][0]} (estimation habituelle à cette heure, aucun signalement récent)"
    return "inconnue (lieu habituellement fermé à cette heure)"


# ---------------------------------------------------------------- bibliothèque
def bibliotheque(user, **_) -> dict:
    now = datetime.now()
    page = campus_service.bu_page(user, now)
    if not page.get("school"):
        return {"erreur": "L'étudiant n'a pas indiqué son établissement : il peut le faire dans « Mon profil »."}
    profile = campus_rules.typical_profile("bu", now.weekday())
    return {
        "etablissement": page["school"], "ville": page.get("city") or "",
        "fermee_aujourd_hui": page["closed_today"],
        "ouverte_maintenant": page["open_now"],
        "horaires_habituels_aujourd_hui": _hours(profile) or "fermée",
        "affluence_maintenant": _crowd(page["gauge"]) if page["open_now"] else "fermée à cette heure",
        "heures_les_plus_calmes_a_venir": [f"{h} h" for h in page["quiet"]],
        "remarque": "Horaires et fréquentation habituelle sont indicatifs (pas de source officielle) : pour un horaire "
                    "exact, conseille le site de la bibliothèque. Les étudiants peuvent signaler l'affluence sur la page.",
        "links": [{"title": "Bibliothèque", "items": [{"label": "Affluence et signalement", "url": url_for("campus.library")}]}],
    }


# ---------------------------------------------------------------- restos U
def _menu(code: int, now: datetime) -> dict | str:
    preview = campus_service.menu_preview(code, now)
    if preview.get("error"):
        return "menu momentanément indisponible"
    meals = {meal: {c["category"] or "plats": c["dishes"][:6] for c in cats} for meal, cats in preview["meals"].items()}
    return meals or "aucun menu publié pour aujourd'hui"


def _next_service(r: dict, now: datetime) -> str:
    """Prochain jour avec un service du midi ou du soir, d'après les jours d'ouverture du Crous."""
    for offset in range(1, 8):
        meals = campus_rules.meals_open(r.get("jours_ouvert"), (now.weekday() + offset) % 7) & {"midi", "soir"}
        if meals:
            day = "demain" if offset == 1 else campus_rules.DAYS_FR[(now.weekday() + offset) % 7]
            return f"{day} ({' et '.join(sorted(meals))})"
    return "inconnu"


def _pick(rows: list[dict], name: str) -> dict | None:
    if not name:
        return None
    found, _ = matching.best_match(name, rows, key=lambda r: r.get("nom") or "")
    if found:
        return found
    wanted = matching.normalize(name)
    return next((r for r in rows if wanted and wanted in matching.normalize(r.get("nom") or "")), None)


def restos_u(user, ville: str = "", restaurant: str = "", **_) -> dict:
    now = datetime.now()
    page = campus_service.restos_page(user, ville, now)
    if page.get("need_city"):
        return {"erreur": "Établissement et ville inconnus : demande à l'étudiant dans quelle ville il cherche."}
    if page.get("error"):
        return {"erreur": page["error"]}
    rows = page["restaurants"]
    if not rows:
        return {"resultat": f"Aucun restaurant Crous trouvé près de {page['place']}."}
    chosen = _pick(rows, restaurant) or next((r for r in rows if r["state"]["open_today"]), None)
    out = {
        "autour_de": page["place"],
        "dans_un_rayon_de_km": campus_rules.RADIUS_KM if page.get("inside") else None,
        "remarque_distance": "" if page.get("inside") else "Aucun dans le rayon : voici les plus proches.",
        "restaurants": [{"nom": r.get("nom"), "type": r.get("type") or "", "adresse": r.get("adresse") or "",
                         "distance_km": round(r["distance_km"], 1), "aujourd_hui": r["state"]["text"],
                         "prochain_service": _next_service(r, now),
                         "affluence_maintenant": _crowd(r["gauge"]) if r["state"]["now"] else "pas de service en cours"}
                        for r in rows[:6]],
        "links": [{"title": "Restos U", "items": [{"label": r.get("nom") or "Restaurant",
                                                   "url": url_for("campus.resto", code=r["code"])} for r in rows[:5]]}],
    }
    if restaurant and chosen is None:
        out["attention"] = f"Aucun restaurant proche ne s'appelle « {restaurant} »."
    if chosen:
        out["menu_du_jour"] = {"restaurant": chosen.get("nom"), "menus": _menu(chosen["code"], now)}
    else:
        out["menu_du_jour"] = "aucun restaurant proche n'est ouvert aujourd'hui"
    return out


# ---------------------------------------------------------------- caféine
def _hm(value) -> str | None:
    return value.strftime("%H:%M") if value else None


def cafeine(user, **_) -> dict:
    now = datetime.now()
    data = caffeine_service.overview(user, now)
    s = data["status"]
    if s["can_drink"]:
        next_ok = "dès maintenant"
    elif s["next_ok"].date() > now.date():
        next_ok = "demain (seuil du jour atteint)"
    else:
        next_ok = f"à partir de {_hm(s['next_ok'])}"
    return {
        "aujourd_hui_mg": s["total"], "seuil_quotidien_mg": s["limits"].daily, "part_du_seuil_pct": s["pct"],
        "seuil_depasse": s["over"],
        "cafeine_active_maintenant_mg": s["active"], "seuil_actif_en_meme_temps_mg": s["limits"].active,
        "une_canette_standard_80mg_possible": next_ok,
        "prises_du_jour": [f"{r['taken_at'][11:16]} : {r['label']} ({r['mg']} mg)" for r in data["today_intakes"]],
        "coucher_prevu": _hm(s["bed"]), "reveil_prevu": _hm(s["wake"]),
        "cafeine_au_coucher_mg": s["at_bedtime"],
        "risque_pour_l_endormissement": s["sleep_risk"],
        "heure_limite_pour_une_derniere_boisson": "plus de caféine aujourd'hui" if s["no_more_today"]
        else ("déjà passée" if s["cutoff_passed"] else _hm(s["cutoff"])),
        "sommeil_estime_min": s["sleep_estimated"], "besoin_de_sommeil_min": s["sleep_need"],
        "sept_derniers_jours_mg": [{"jour": h["day"].strftime("%d/%m"), "mg": h["mg"]} for h in data["history"]],
        "jours_sans_cafeine_d_affilee": data["free_streak"],
        "teneurs_des_boissons_mg": {label: mg for label, mg in caffeine_rules.DRINKS.values()},
        "reperes": f"EFSA : au plus {s['limits'].daily} mg par jour et {s['limits'].active} mg en une fois pour son "
                   f"profil ; demi-vie d'environ {caffeine_rules.HALF_LIFE_HOURS:g} h. Repères de prévention, "
                   "pas un avis médical.",
        "links": [{"title": "Caféine", "items": [{"label": "Compteur de caféine", "url": url_for("caffeine.index")}]}],
    }


CAMPUS_TOOLS = [
    Tool("bibliotheque",
         "Bibliothèque de l'établissement de l'étudiant : ouverte ou non, horaires habituels du jour, affluence "
         "maintenant (signalée par des étudiants, ou estimée) et heures les plus calmes à venir.",
         bibliotheque),
    Tool("restos_u",
         "Restaurants et cafétérias Crous autour de l'établissement (ou d'une ville) : ouverts aujourd'hui, "
         "distance, affluence, et menu du jour (du restaurant demandé, sinon du plus proche ouvert).",
         restos_u, params(ville=text_param("Ville, si l'étudiant en précise une (facultatif)."),
                          restaurant=text_param("Nom du restaurant dont il veut le menu (facultatif)."))),
]
CAFFEINE_TOOLS = [
    Tool("cafeine",
         "Compteur de caféine de l'étudiant : total du jour et seuil recommandé, caféine encore active, quand "
         "reprendre une boisson, caféine restante au coucher, heure limite pour une dernière boisson, sommeil "
         "estimé, 7 derniers jours et teneur en caféine des boissons courantes.",
         cafeine),
]
