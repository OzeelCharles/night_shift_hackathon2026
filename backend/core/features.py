"""Fonctionnalités enfichables (« plugins »).

Chaque fonctionnalité est un module qui expose une variable `feature` de type `Feature`.
La liste des modules chargés est dans la configuration (FEATURES, plus EXTRA_FEATURES pour
ajouter les vôtres sans toucher au cœur). Une fonctionnalité peut fournir :
- des blueprints Flask (ses pages et ses routes) ;
- des entrées de menu (pour tout utilisateur connecté, ou réservées aux administrateurs) ;
- des abonnements au bus d'événements (voir core/events.py) ;
- des panneaux affichés en bas de la page d'une matière (gabarits Jinja) ;
- une fonction `setup(app)` pour toute autre initialisation.
"""
import importlib
from dataclasses import dataclass, field
from typing import Callable

from flask import Blueprint, Flask

from . import events


@dataclass(frozen=True)
class NavItem:
    endpoint: str
    label: str
    order: int = 50        # position dans le menu (plus petit = plus à gauche)
    admin: bool = False    # réservé aux administrateurs
    # Barre où l'entrée apparaît :
    # - « site » : barre du haut, visible de tous, qui liste les espaces de Rivezli (Encadrement…) ;
    # - sinon le nom d'un espace (« encadrement » par défaut) : ses onglets, dans la seconde barre.
    section: str = "encadrement"
    opens: str | None = None   # pour une entrée « site » : l'espace qu'elle ouvre (surligné quand on y est)


@dataclass
class Feature:
    name: str
    blueprints: list[Blueprint] = field(default_factory=list)
    nav: list[NavItem] = field(default_factory=list)
    subscriptions: list[tuple[str, Callable]] = field(default_factory=list)
    subject_panels: list[str] = field(default_factory=list)
    setup: Callable[[Flask], None] | None = None


def load_features(app: Flask, module_paths: list[str]) -> list[Feature]:
    loaded = []
    for path in module_paths:
        feature: Feature = importlib.import_module(path).feature
        for bp in feature.blueprints:
            app.register_blueprint(bp)
        for event, handler in feature.subscriptions:
            events.subscribe(app, event, handler)
        if feature.setup:
            feature.setup(app)
        loaded.append(feature)
    app.extensions["features"] = loaded
    nav = sorted((item for f in loaded for item in f.nav), key=lambda i: i.order)
    panels = [p for f in loaded for p in f.subject_panels]
    app.jinja_env.globals.update(nav_items=nav, subject_panels=panels)
    return loaded
