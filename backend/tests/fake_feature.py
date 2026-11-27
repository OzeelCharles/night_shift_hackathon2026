"""Exemple de fonctionnalité enfichable, utilisé par les tests (et modèle pour en écrire une).

Elle ajoute : une page et son entrée de menu, une réaction à la création d'une matière,
et un panneau en bas de la page de chaque matière.
"""
import os

from flask import Blueprint, g, render_template_string

from backend.core import events
from backend.core.features import Feature, NavItem
from backend.core.security import login_required

bp = Blueprint("demo", __name__, template_folder=os.path.join(os.path.dirname(__file__), "fake_templates"))
created: list[tuple[int, list[int]]] = []


@bp.route("/demo")
@login_required
def page():
    return render_template_string("<p>Page de démo pour {{ g.user.username }}</p>")


def hello(name):
    return f"Bonjour {name}"


def on_subject_created(user_id, subject_ids, **_):
    created.append((user_id, subject_ids))
    return ["La démo a vu passer une nouvelle matière."]


feature = Feature(
    "demo",
    blueprints=[bp],
    nav=[NavItem("demo.page", "Démo", order=35)],
    subscriptions=[(events.SUBJECT_CREATED, on_subject_created)],
    panels={"subject": ["demo_panel.html"]},
    # tables et fonctions de gabarit déclarées, sans code d'initialisation
    schema="CREATE TABLE IF NOT EXISTS demo_notes (id INTEGER PRIMARY KEY, note TEXT NOT NULL DEFAULT '');",
    migrations={"demo_notes": {"added_later": "TEXT NOT NULL DEFAULT ''"}},
    template_globals={"demo_hello": hello},
)
