"""Exemple de fonctionnalité enfichable, utilisé par les tests (et modèle pour en écrire une).

Elle ajoute : une page et son entrée de menu, une réaction à la création d'une matière,
et un panneau en bas de la page de chaque matière.
"""
import os

from flask import Blueprint, g, render_template_string

from app.core import events
from app.core.features import Feature, NavItem
from app.core.security import login_required

bp = Blueprint("demo", __name__, template_folder=os.path.join(os.path.dirname(__file__), "fake_templates"))
created: list[tuple[int, list[int]]] = []


@bp.route("/demo")
@login_required
def page():
    return render_template_string("<p>Page de démo pour {{ g.user.username }}</p>")


def on_subject_created(user_id, subject_ids, **_):
    created.append((user_id, subject_ids))
    return ["La démo a vu passer une nouvelle matière."]


feature = Feature(
    "demo",
    blueprints=[bp],
    nav=[NavItem("demo.page", "Démo", order=35)],
    subscriptions=[(events.SUBJECT_CREATED, on_subject_created)],
    subject_panels=["demo_panel.html"],
)
