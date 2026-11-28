"""Fonctionnalités enfichables (« plugins »).

Chaque fonctionnalité est un module qui expose une variable `feature` de type `Feature`.
La liste des modules chargés est dans la configuration (FEATURES, plus EXTRA_FEATURES pour
ajouter les vôtres sans toucher au cœur). Une fonctionnalité peut fournir :
- des blueprints Flask (ses pages et ses routes) ;
- des entrées de menu (pour tout utilisateur connecté, ou réservées aux administrateurs) ;
- des abonnements au bus d'événements (voir core/events.py) ;
- des panneaux insérés dans des pages du cœur, par emplacement (`panels`) : « subject » (bas de la page d'une
  matière), « agenda » (Mon agenda) ; les gabarits les incluent avec `panels.get("<emplacement>", [])` ;
- des éléments affichés sur toutes les pages d'un utilisateur connecté (gabarits Jinja, ex. la bulle de l'assistant) ;
- des outils que l'assistant peut appeler, et l'application des propositions qu'ils font valider par un
  bouton (`actions`) (voir core/tools.py) ;
- ses tables (`schema`, créées au démarrage) et les colonnes ajoutées depuis (`migrations`) ;
- des fonctions ou valeurs utilisables dans les gabarits (`template_globals`) ;
- une fonction `setup(app)` pour toute autre initialisation.
"""
import importlib
from dataclasses import dataclass, field
from typing import Callable

from flask import Blueprint, Flask

from . import events
from .tools import Action, Tool


@dataclass(frozen=True)
class NavItem:
    endpoint: str
    label: str
    order: int = 50        # position dans le menu (plus petit = plus à gauche)
    admin: bool = False    # réservé aux administrateurs
    moderator: bool = False  # réservé aux modérateurs (et aux administrateurs)
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
    panels: dict[str, list[str]] = field(default_factory=dict)   # {emplacement: [gabarits]}
    subject_panels: list[str] = field(default_factory=list)      # ancien nom de panels["subject"], encore accepté
    widgets: list[str] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)          # propositions de ses outils, validées par bouton
    schema: str = ""                                              # CREATE TABLE IF NOT EXISTS …
    migrations: dict[str, dict[str, str]] = field(default_factory=dict)   # {table: {colonne: déclaration}}
    template_globals: dict[str, object] = field(default_factory=dict)
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
        panels.setdefault("subject", []).extend(f.subject_panels)
    nav = sorted((item for f in loaded for item in f.nav), key=lambda i: i.order)
    app.extensions["tools"] = {t.name: t for f in loaded for t in f.tools}
    app.extensions["assistant_actions"] = {a.kind: a for f in loaded for a in f.actions}
    app.jinja_env.globals.update(nav_items=nav, panels=panels, subject_panels=panels["subject"] if panels else [],
                                 widgets=[w for f in loaded for w in f.widgets])
    return loaded
