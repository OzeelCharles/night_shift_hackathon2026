"""Recherche de stage : profil candidat, postes recherchés, offres suivies, lettres de motivation."""
from __future__ import annotations

from urllib.parse import urlparse

from flask import abort, current_app

from backend.services.report import Report

from . import letters, sources, store

STATUSES = {"a_postuler": "À postuler", "postule": "Candidature envoyée", "relance": "À relancer",
            "entretien": "Entretien", "refuse": "Refusé", "accepte": "Accepté"}
# Exemples de postes, pour montrer que tout cursus est concerné (liste de suggestions du formulaire)
EXAMPLES = ["Assistant juridique", "Développeur web", "Infirmier en service de pédiatrie", "Chargé de communication",
            "Assistant ingénieur en bureau d'études", "Assistant chef de projet marketing", "Analyste financier",
            "Graphiste", "Assistant ressources humaines", "Data analyst", "Éducateur spécialisé",
            "Conducteur de travaux", "Assistant de laboratoire", "Réceptionniste en hôtellerie",
            "Technicien agricole", "Journaliste", "Assistant d'enseignement", "Commercial B2B"]
# Compétences proposées d'après le domaine du compte : un clic les ajoute, rien à retaper
SKILLS = {
    "droit": ["rédaction juridique", "recherche documentaire", "veille juridique", "analyse de contrats", "rigueur"],
    "informatique": ["Python", "JavaScript", "SQL", "Git", "développement web", "travail en méthode agile"],
    "data": ["Python", "SQL", "statistiques", "visualisation de données", "Excel avancé", "machine learning"],
    "infirm": ["soins de base", "prise de constantes", "transmissions", "hygiène hospitalière", "relation patient"],
    "sant": ["accueil des patients", "hygiène et asepsie", "travail en équipe pluridisciplinaire", "écoute"],
    "commerce": ["prospection", "négociation", "relation client", "CRM", "Excel", "veille concurrentielle"],
    "marketing": ["réseaux sociaux", "Canva", "études de marché", "rédaction web", "Google Analytics"],
    "communication": ["rédaction", "réseaux sociaux", "relations presse", "Adobe InDesign", "événementiel"],
    "journal": ["écriture journalistique", "interview", "vérification des sources", "montage vidéo"],
    "gestion": ["comptabilité", "Excel avancé", "contrôle de gestion", "analyse financière", "reporting"],
    "économie": ["analyse économique", "Excel", "statistiques", "veille sectorielle", "synthèse"],
    "ingénier": ["CAO (SolidWorks, CATIA)", "gestion de projet", "calcul de structures", "AutoCAD", "normes qualité"],
    "btp": ["lecture de plans", "AutoCAD", "suivi de chantier", "métrés", "sécurité sur chantier"],
    "architect": ["AutoCAD", "Revit", "maquettes", "dessin à main levée", "SketchUp"],
    "art": ["Adobe Photoshop", "Illustrator", "Figma", "direction artistique", "typographie"],
    "design": ["Figma", "UX research", "prototypage", "Adobe Illustrator", "design system"],
    "lettres": ["rédaction", "correction", "traduction", "analyse de textes", "recherche documentaire"],
    "langue": ["traduction", "interprétariat", "rédaction bilingue", "médiation culturelle"],
    "éducation": ["animation de groupe", "pédagogie", "préparation de séances", "suivi individualisé"],
    "social": ["accompagnement", "écoute active", "conduite d'entretien", "travail en réseau", "médiation"],
    "hôtel": ["accueil client", "réservation", "anglais professionnel", "service en salle", "gestion des plaintes"],
    "touris": ["accueil", "conseil en voyage", "langues étrangères", "logiciels de réservation"],
    "agri": ["agronomie", "conduite d'engins", "suivi de cultures", "soins aux animaux", "environnement"],
    "environnement": ["bilan carbone", "SIG (QGIS)", "réglementation environnementale", "terrain"],
    "sport": ["encadrement sportif", "préparation physique", "animation", "premiers secours (PSC1)"],
    "science": ["manipulations en laboratoire", "protocoles expérimentaux", "analyse de données", "rédaction scientifique"],
    "psycho": ["entretien", "observation", "écoute", "tests psychométriques"],
}
COMMON_SKILLS = ["Pack Office", "travail en équipe", "autonomie", "anglais", "organisation"]


def skill_suggestions(field: str, current: str) -> list[str]:
    key = (field or "").lower()
    found = [s for k, skills in SKILLS.items() if k in key for s in skills]
    have = {x.strip().lower() for x in (current or "").split(",")}
    out = []
    for s in found + COMMON_SKILLS:
        if s.lower() not in have and s not in out:
            out.append(s)
    return out[:10]


def _clean(v, limit: int) -> str:
    v = sources.fix_mojibake(v)   # texte recopié depuis une page mal décodée
    return "\n".join(" ".join(line.split()) for line in v.splitlines() if line.strip())[:limit]


def repair_saved_texts() -> int:
    """Répare les offres et lettres déjà enregistrées avec un texte mal décodé (« Ã© »). Renvoie le nombre de
    lignes corrigées ; un texte correct n'est jamais modifié."""
    return store.repair_texts(sources.fix_mojibake)


def _owned(row):
    if row is None:
        abort(404)
    return row


def page(user) -> dict:
    uid = user["id"]
    wishes = [dict(w, links=sources.search_links(w["title"], w["location"])) for w in store.wishes(uid)]
    profile = store.profile(uid)
    me = letters.candidate(user)
    missing = [label for key, label in (("field", "ton domaine"), ("program", "ta formation"),
                                         ("home_city", "ta ville")) if not me[key]]
    return {"profile": profile, "me": me, "missing": missing,
            "wishes": wishes, "offers": store.offers(uid), "letters": store.letters(uid),
            "skills": skill_suggestions(me["field"], profile.get("skills", "")),
            "default_location": me["home_city"] or me["study_city"],
            "statuses": STATUSES, "examples": EXAMPLES, "tones": letters.TONES,
            "languages": letters.LANGUAGES, "level_label": letters.LEVELS.get(user["level"] or "", ""), "ai": letters.available(), "web": current_app.config.get("ENABLE_WEB")}


def save_profile(user, form) -> Report:
    limits = {"skills": 1500, "experiences": 2500, "projects": 2500, "qualities": 500, "goal": 120}
    store.save_profile(user["id"], {k: _clean(form.get(k), limits.get(k, 200)) for k in store.PROFILE_FIELDS})
    return Report().success("C'est noté : tes prochaines lettres s'en serviront.")


def add_wish(user, form) -> Report:
    title, location = _clean(form.get("title"), 120), _clean(form.get("location"), 120)
    if len(title) < 2:
        return Report().error("Indique l'intitulé du poste recherché.")
    if any(w["title"].lower() == title.lower() and w["location"].lower() == location.lower()
           for w in store.wishes(user["id"])):
        return Report().info("Ce poste est déjà dans ta liste.")
    store.add_wish(user["id"], title, location)
    return Report().success(f"« {title} » ajouté : ouvre les recherches préremplies sur chaque site.")


def delete_wish(user, wish_id) -> Report:
    _owned(store.wish(wish_id, user["id"]))
    store.delete_wish(wish_id, user["id"])
    return Report().success("Poste retiré de ta liste.")


def _fetch(url: str) -> dict | None:
    """Lit l'offre à partir de son lien (données JobPosting de la page). None si la page refuse ou est muette."""
    if not current_app.config.get("ENABLE_WEB"):
        return None
    from backend.dao import safe_http, web
    try:  # URL fournie par l'utilisateur : uniquement via safe_http (adresses publiques, SEC-07)
        r = safe_http.get(url, headers=web.BROWSER)
    except safe_http.SafeHttpError:
        return None
    if r.status_code != 200:
        return None
    # décodé à partir des octets : sans charset annoncé, on lirait Latin-1 (« Ã© » au lieu de « é »).
    # safe_http.Response : en-têtes en minuscules, pas d'apparent_encoding.
    return sources.parse_offer(sources.decode_page(r.content, r.headers.get("content-type", "")))


def add_offer(user, form) -> Report:
    url = _clean(form.get("url"), 1000)
    if url and urlparse(url).scheme not in ("http", "https"):
        return Report().error("Le lien de l'offre doit commencer par http:// ou https://.")
    fields = {k: _clean(form.get(k), 200) for k in ("title", "company", "location")}
    description = _clean(form.get("description"), 6000)
    report = Report()
    if url and not (fields["title"] and description):
        found = _fetch(url)
        if found:
            fields = {k: fields[k] or found[k] for k in fields}
            description = description or found["description"]
            report.info("Offre lue à partir de son lien : vérifie les informations.")
        elif not fields["title"]:
            return report.error("Ce site ne laisse pas lire l'offre automatiquement : recopie au moins "
                                "l'intitulé (et si possible le texte de l'offre, pour une lettre sur mesure).")
    if not fields["title"]:
        return report.error("Indique l'intitulé de l'offre ou colle son lien.")
    store.add_offer(user["id"], fields["title"], fields["company"], fields["location"], url,
                    sources.site_name(url) if url else "", description)
    return report.success(f"Offre « {fields['title']} » ajoutée à ton suivi.")


def set_status(user, offer_id, status) -> Report:
    _owned(store.offer(offer_id, user["id"]))
    if status not in STATUSES:
        return Report().error("Statut inconnu.")
    store.set_status(offer_id, user["id"], status)
    return Report().success(f"Statut mis à jour : {STATUSES[status]}.")


def delete_offer(user, offer_id) -> Report:
    _owned(store.offer(offer_id, user["id"]))
    store.delete_offer(offer_id, user["id"])
    return Report().success("Offre retirée de ton suivi.")


def generate_letter(user, form) -> tuple[int | None, Report]:
    """Lettre pour une offre suivie, pour un poste recherché (candidature spontanée) ou pour une entreprise libre."""
    uid = user["id"]
    offer_id = int(form["offer_id"]) if str(form.get("offer_id", "")).isdigit() else None
    if offer_id:
        target = dict(_owned(store.offer(offer_id, uid)))
    elif str(form.get("wish_id", "")).isdigit():
        w = _owned(store.wish(int(form["wish_id"]), uid))
        target = {"title": w["title"], "location": w["location"], "company": _clean(form.get("company"), 200)}
    else:
        target = {"title": _clean(form.get("title"), 200), "company": _clean(form.get("company"), 200),
                  "location": _clean(form.get("location"), 120), "description": _clean(form.get("description"), 6000)}
    if not target.get("title"):
        return None, Report().error("Indique le poste visé.")
    profile = store.profile(uid)
    tone = form.get("tone") if form.get("tone") in letters.TONES else "sobre"
    language = form.get("language") if form.get("language") in letters.LANGUAGES else "fr"
    report = Report()
    content, author = None, "modèle"
    if letters.available():
        try:
            content, author = letters.write(profile, user, target, tone, language), "Groq"
        except letters.LetterError as e:
            report.warning(f"Groq n'a pas pu rédiger la lettre ({e}) : voici une lettre type à compléter.")
    if content is None:
        content = letters.template(profile, user, target)
    letter_id = store.add_letter(uid, offer_id, target["title"], target.get("company", ""), content, author)
    if author == "Groq":
        report.success("Lettre rédigée par Groq : relis-la et complète ce qui est entre crochets.")
    else:
        report.info("Lettre type remplie avec ton profil : complète ce qui est entre crochets.")
    return letter_id, report


def letter(user, letter_id):
    return _owned(store.letter(letter_id, user["id"]))


def save_letter(user, letter_id, content) -> Report:
    letter(user, letter_id)
    content = str(content or "").replace("\r\n", "\n").strip()[:12000]
    if not content:
        return Report().error("La lettre est vide.")
    store.update_letter(letter_id, user["id"], content)
    return Report().success("Lettre enregistrée.")


def delete_letter(user, letter_id) -> Report:
    letter(user, letter_id)  # 404 si la lettre n'est pas à l'utilisateur
    store.delete_letter(letter_id, user["id"])
    return Report().success("Lettre supprimée.")
