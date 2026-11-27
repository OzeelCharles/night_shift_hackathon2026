"""Atelier de révision (fonctionnalité enfichable).

À partir des supports d'une matière : fiches de révision, QCM notés sur 20, carnet de notes avec
l'évolution des scores, exercices extraits des PDF du cours ou de PDF d'exercices corrigés en ligne.
Le niveau de difficulté est demandé pour chaque matière (et réglable chapitre par chapitre).

Activation : une ligne dans FEATURES (backend/config.py). Retrait : supprimer cette ligne.
Tout est dans ce dossier ; les tables (préfixe rev_) sont créées au démarrage, sans toucher au cœur.
"""
from flask import Flask

from backend.core.features import Feature, NavItem

from . import service, store
from .views import bp


def setup(app: Flask) -> None:
    with app.app_context():
        store.init_schema()
    # utilisé par le panneau ajouté en bas de la page de chaque matière
    app.jinja_env.globals["revision_summary"] = service.panel_summary


feature = Feature(
    "revision",
    blueprints=[bp],
    nav=[NavItem("revision.index", "Réviser", order=25)],
    subject_panels=["revision/panel.html"],
    setup=setup,
)
