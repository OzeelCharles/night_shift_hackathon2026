"""Accueil, tableau de bord du jour, préférences de travail."""
from datetime import date

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from backend.core.features import Feature, NavItem
from backend.core.security import login_required
from backend.services import planning_service, preference_service

from . import flash_report

bp = Blueprint("main", __name__)


# Vitrine de la page d'accueil : (route de la fonctionnalité, icône, titre, texte, la carte mène-t-elle à une page ?).
# Neuf outils = une grille de 3 × 3. Une carte n'apparaît que si la fonctionnalité est activée (backend/config.py).
# Pour en ajouter un dixième, en retirer un autre : la grille doit rester à 9 cartes, sinon une ligne reste incomplète.
SHOWCASE = [
    ("main.dashboard", "☀️", "Ta journée en un coup d'œil",
     "Ce qui t'attend aujourd'hui, les prochains jours et tes matières, sur une seule page.", True),
    ("planning.week", "📅", "Un planning qui tient dans ta semaine",
     "Rivezli répartit tes révisions jusqu'à l'examen et se réorganise après un imprévu.", True),
    ("agenda.index", "🗓️", "Ton emploi du temps, importé",
     "Importe ton agenda ou saisis tes cours : chaque créneau est rattaché tout seul à la bonne matière.", True),
    ("revision.index", "🧠", "Un atelier de révision",
     "Fiches, QCM notés sur 20, exercices corrigés et carnet de notes, à partir de tes propres cours.", True),
    ("dossier.index", "📊", "Ton dossier scolaire",
     "Tes notes, tes moyennes et ta progression matière par matière. Elles restent privées : toi seul les vois.", True),
    ("assistant.conversation", "✨", "Un assistant sur chaque page",
     "Pose une question sur tes cours, ton planning ou tes notes, joins une photo ou un PDF : il te répond.", False),
    ("campus.index", "🏛️", "La BU et le resto U",
     "L'affluence de la bibliothèque de ton école et des restos U autour de toi, avec le menu du jour.", True),
    ("stages.index", "💼", "Ta recherche de stage",
     "Des recherches prêtes à l'emploi, le suivi des offres, des lettres de motivation et un vérificateur de CV qui passe les filtres ATS.", True),
    ("caffeine.index", "☕", "Le compteur de caféine",
     "Café, boissons énergisantes : suis ta dose du jour et ce qu'il en reste au coucher, pour réviser sans te cramer.", True),
]


def _logo_color(position):
    """Jaune, menthe ou rose (0, 1, 2) : décalés d'une teinte à chaque ligne de 3, aucune couleur ne se répète
    dans une même ligne ni dans une même colonne."""
    return (position + position // 3) % 3


@bp.route("/")
def index():
    """Page d'accueil de Rivezli : présentation des fonctionnalités pour les étudiants (frontend/templates/home.html)."""
    shown = [row for row in SHOWCASE if row[0] in current_app.view_functions]
    features = [dict(endpoint=e, icon=i, title=t, text=x, link=link, hl=_logo_color(n))
                for n, (e, i, t, x, link) in enumerate(shown)]
    donation_url = (current_app.config.get("DONATION_URL") or "").strip()
    return render_template("home.html", features=features,
                           donation_url=donation_url if donation_url.startswith("https://") else None)


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
