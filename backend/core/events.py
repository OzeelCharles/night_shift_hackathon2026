"""Bus d'événements : les services annoncent ce qui s'est passé, les fonctionnalités réagissent.

Un abonné reçoit les données de l'événement en arguments nommés et peut renvoyer une liste de
messages à afficher à l'utilisateur. Les abonnements sont propres à chaque application Flask
(stockés dans app.extensions), ce qui permet de créer plusieurs applications (tests) sans fuite.
"""
from collections import defaultdict

from flask import current_app

# Événements émis par le cœur de l'application (arguments nommés entre parenthèses)
PROFILE_CHANGED = "profile.changed"   # (user_id)
SUBJECT_CREATED = "subject.created"   # (user_id, subject_id)
SUBJECT_DELETED = "subject.deleted"   # (user_id, subject_id)
AGENDA_CHANGED = "agenda.changed"     # (user_id)
PLAN_UPDATED = "plan.updated"         # (user_id)


def _registry(app) -> dict:
    return app.extensions.setdefault("events", defaultdict(list))


def subscribe(app, event: str, handler) -> None:
    _registry(app)[event].append(handler)


def emit(event: str, **payload) -> list[str]:
    """Prévient les abonnés ; renvoie les messages qu'ils souhaitent afficher."""
    messages: list[str] = []
    for handler in _registry(current_app)[event]:
        messages += list(handler(**payload) or [])
    return messages
