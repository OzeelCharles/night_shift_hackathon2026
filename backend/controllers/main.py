"""Accueil, tableau de bord du jour, préférences de travail."""
from datetime import date

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required
from backend.services import planning_service, preference_service

from . import flash_report

bp = Blueprint("main", __name__)


# Vitrine de la page d'accueil : (route de la fonctionnalité, icône, titre, texte, couleur de surligneur 0-5).
# Une carte n'apparaît que si la fonctionnalité est activée (backend/config.py) : retirer une fonctionnalité,
# ou en brancher une nouvelle (ajouter sa ligne ici), met la page à jour sans autre changement.
SHOWCASE = [
    ("planning.week", "📅", "Un planning qui tient dans ta semaine",
     "Importe ton agenda ou saisis tes cours, ajoute tes matières : Rivezli répartit tes révisions jusqu'à l'examen "
     "et se réorganise après un imprévu.", 0),
    ("revision.index", "🧠", "Un atelier de révision",
     "Fiches, QCM notés sur 20, exercices corrigés et carnet de notes, à partir de tes propres cours.", 4),
    ("dossier.index", "📊", "Ton dossier scolaire",
     "Tes notes, tes moyennes et ta progression matière par matière. Elles restent privées : toi seul les vois.", 1),
    ("campus.index", "🏛️", "La BU et le resto U",
     "L'affluence de la bibliothèque de ton école et des restos U autour de toi, avec le menu du jour.", 3),
    ("stages.index", "💼", "Ta recherche de stage",
     "Des recherches prêtes à l'emploi sur les grands sites, le suivi des offres repérées et des lettres de motivation.", 5),
    ("community.index", "💬", "La communauté",
     "Un forum d'entraide (exercices, concours, orientation), des messages privés et le partage de polys et de diapos.", 2),
    ("caffeine.index", "☕", "Le compteur de caféine",
     "Café, boissons énergisantes : suis ta dose du jour et ce qu'il en reste au coucher, pour réviser sans te cramer.", 0),
]


@bp.route("/")
def index():
    """Page d'accueil de Rivezli : présentation des fonctionnalités pour les étudiants (frontend/templates/home.html)."""
    features = [dict(endpoint=e, icon=i, title=t, text=x, hl=c) for e, i, t, x, c in SHOWCASE
                if e in current_app.view_functions]
    return render_template("home.html", features=features)


@bp.route("/encadrement")
def encadrement():
    """Entrée « Encadrement » de la page de départ : ouvre l'espace du planificateur, directement
    sur l'onglet « Aujourd'hui ». Un visiteur (sans compte) voit d'abord sa présentation."""
    if g.user:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@bp.route("/tableau-de-bord")
@login_required
def dashboard():
    return render_template("dashboard.html", date_fromiso=date.fromisoformat,
                           **planning_service.dashboard(g.user["id"]))


@bp.route("/preferences", methods=("GET", "POST"))
@login_required
def preferences():
    uid = g.user["id"]
    if request.method == "POST":
        error = preference_service.save(uid, request.form)
        if error:
            flash(error, "error")
            return redirect(url_for("main.preferences"))
        flash_report(planning_service.update(uid, "Préférences enregistrées."))
        return redirect(url_for("planning.week"))
    _, row = preference_service.load(uid)
    return render_template("preferences.html", p=row, rest=preference_service.rest_days_of(row))


feature = Feature("tableau-de-bord", blueprints=[bp],
                  nav=[NavItem("main.encadrement", "Encadrement", order=1, section="site", opens="encadrement"),
                       NavItem("main.dashboard", "Aujourd'hui", order=10),
                       NavItem("main.preferences", "Mes réglages", order=40)])
