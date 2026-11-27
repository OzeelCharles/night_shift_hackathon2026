"""Fonctionnalités enfichables (« plugins »).

Chaque fonctionnalité est un module qui expose une variable `feature` de type `Feature`.
La liste des modules chargés est dans la configuration (FEATURES, plus EXTRA_FEATURES pour
ajouter les vôtres sans toucher au cœur). Tout se déclare, rien n'est à programmer :
- blueprints : ses pages et ses routes ;
- nav : ses entrées de menu (barre du haut, onglets d'un espace, administration) ;
- subscriptions : ses réactions aux événements (voir core/events.py) ;
- schema / migrations : ses tables (CREATE … IF NOT EXISTS) et les colonnes ajoutées depuis ;
- template_globals : les fonctions qu'appellent ses gabarits ;
- panels : ses panneaux dans les pages des autres, par emplacement (« subject » : page d'une matière,
  « agenda » : Mon agenda) ;
- setup(app) : pour tout le reste, s'il en reste.
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
    hidden_if: tuple[str, str] | None = None  # (colonne du compte, valeur) : entrée masquée si le compte a cette valeur


@dataclass
class Feature:
    name: str
    blueprints: list[Blueprint] = field(default_factory=list)
    nav: list[NavItem] = field(default_factory=list)
    subscriptions: list[tuple[str, Callable]] = field(default_factory=list)
    schema: str = ""
    migrations: dict[str, dict[str, str]] = field(default_factory=dict)
    template_globals: dict[str, Callable] = field(default_factory=dict)
    panels: dict[str, list[str]] = field(default_factory=dict)
    setup: Callable[[Flask], None] | None = None


def load_features(app: Flask, module_paths: list[str]) -> list[Feature]:
    from backend.dao.db import install

    loaded = []
    for path in module_paths:
        feature: Feature = importlib.import_module(path).feature
        for bp in feature.blueprints:
            app.register_blueprint(bp)
        for event, handler in feature.subscriptions:
            events.subscribe(app, event, handler)
        app.jinja_env.globals.update(feature.template_globals)
        with app.app_context():
            if feature.schema or feature.migrations:
                install(feature.schema, feature.migrations)
            if feature.setup:
                feature.setup(app)
        loaded.append(feature)
    app.extensions["features"] = loaded
    panels: dict[str, list[str]] = {}
    for f in loaded:
        for slot, templates in f.panels.items():
            panels.setdefault(slot, []).extend(templates)
    nav = sorted((item for f in loaded for item in f.nav), key=lambda i: i.order)
    app.jinja_env.globals.update(nav_items=nav, panels=panels)
    return loaded
