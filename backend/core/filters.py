"""Filtres Jinja d'affichage en français."""
from backend.business import text
from backend.business.planner import fmt_minutes


def init_app(app):
    app.jinja_env.filters.update(
        hm=lambda v: text.to_datetime(v).strftime("%H:%M"),
        jour=text.day_fr,
        datefr=lambda v: text.date_fr(v) if v else "",
        duree=fmt_minutes,
        nombre=text.number_fr,
    )
