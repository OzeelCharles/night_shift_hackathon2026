"""Contrôleurs HTTP : lisent la requête, appellent un service, affichent le résultat.
Chaque module expose `feature` (voir core/features.py)."""
from flask import flash


def flash_report(report) -> None:
    for message, category in report:
        flash(message, category)


def flash_errors(errors) -> None:
    for e in errors:
        flash(e, "error")
