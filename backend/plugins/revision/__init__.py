"""Atelier de révision (fonctionnalité enfichable).

À partir des supports d'une matière : fiches de révision, QCM notés sur 20, carnet de notes avec
l'évolution des scores, exercices extraits des PDF du cours ou de PDF d'exercices corrigés en ligne.
Le niveau de difficulté est demandé pour chaque matière (et réglable chapitre par chapitre).

Activation : une ligne dans FEATURES (backend/config.py). Retrait : supprimer cette ligne.
Tout est dans ce dossier ; les tables (préfixe rev_) sont créées au démarrage, sans toucher au cœur.
"""
from backend.core.features import Feature, NavItem

from . import service, store
from .views import bp

feature = Feature(
    "revision",
    blueprints=[bp],
    # onglet de la barre du haut, à côté d'« Encadrement » : l'atelier est un espace à part entière
    nav=[NavItem("revision.index", "Réviser", order=2, section="site", opens="revision")],
    schema=store.SCHEMA,
    migrations=store.MIGRATIONS,
    # panneau en bas de la page de chaque matière, et la fonction qui le remplit
    panels={"subject": ["revision/panel.html"]},
    template_globals={"revision_summary": service.panel_summary},
)
