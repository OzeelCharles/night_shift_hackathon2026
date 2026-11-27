"""Atelier de révision (fonctionnalité enfichable).

À partir des supports d'une matière : fiches de révision, QCM notés sur 20, carnet de notes avec
l'évolution des scores, exercices extraits des PDF du cours ou de PDF d'exercices corrigés en ligne.
Le niveau de difficulté est demandé pour chaque matière (et réglable chapitre par chapitre).

Activation : une ligne dans FEATURES (backend/config.py). Retrait : supprimer cette ligne.
Tout est dans ce dossier ; les tables (préfixe rev_) sont créées au démarrage, sans toucher au cœur.
"""
from backend.core.features import Feature, NavItem

from . import service, store
from .assistant import TOOLS
from .views import bp

feature = Feature(
    "revision",
    blueprints=[bp],
    # onglet de la barre du haut, à côté d'« Encadrement » : l'atelier est un espace à part entière
    nav=[NavItem("revision.index", "Réviser", order=2, section="site", opens="revision")],
    panels={"subject": ["revision/panel.html"]},   # en bas de la page de chaque matière
    tools=TOOLS,  # l'assistant peut montrer la courbe des scores de QCM
    schema=store.SCHEMA,
    migrations=store.MIGRATIONS,
    template_globals={"revision_summary": service.panel_summary},   # utilisé par le panneau
)
