"""Vérificateur ATS de CV (fonctionnalité enfichable de l'espace « Stages »).

L'étudiant dépose son CV (PDF, DOCX ou ODT) : seul le texte est gardé, le fichier est supprimé aussitôt.
Rivezli signale ce qui gêne les logiciels de tri des candidatures (CV scanné, colonnes, tableaux, rubriques
ou coordonnées introuvables, longueur), puis compare le CV aux offres suivies dans « Stages » : score sur 100,
mots-clés trouvés et manquants, pastille sur la carte de chaque offre. Groq peut proposer des reformulations,
seulement sur clic explicite.

Activation : EXTRA_FEATURES=backend.plugins.verif_cv (a besoin du plugin stages, chargé avant).
Tout est dans ce dossier ; la table (préfixe vcv_) est créée au démarrage. stg_offers est lue sans être modifiée.
"""
from backend.core.features import Feature, NavItem

from . import verif_cv_database, verif_cv_service
from .verif_cv_pages import bp


def setup(app):
    if "stages" not in app.blueprints:
        raise RuntimeError("verif_cv a besoin du plugin stages : ajoute « backend.plugins.stages » aux "
                           "fonctionnalités chargées avant « backend.plugins.verif_cv ».")


feature = Feature(
    "verif_cv",
    blueprints=[bp],
    # onglets de l'espace « Stages » (seconde barre) : la page actuelle, puis la vérification du CV
    nav=[NavItem("stages.index", "Mes offres", order=10, section="stages"),
         NavItem("verif_cv.page", "Vérifier mon CV", order=20, section="stages")],
    schema=verif_cv_database.SCHEMA,
    panels={"stages_offer": ["verif_cv/verif_cv_offer_panel.html"]},   # pastille sur la carte de chaque offre
    template_globals={"vcv_offer_score": verif_cv_service.offer_score},
    setup=setup,
)
