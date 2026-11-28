"""Rivezli : backend Flask organisé en couches (le frontend est dans ../frontend).

  controllers/  routes HTTP (une fonctionnalité enfichable par module, voir core/features.py)
  services/     cas d'usage : orchestrent les DAO et les règles métier
  business/     règles métier pures (planification, rapprochement des noms, examens…)
  dao/          accès aux données : SQLite, fichier des établissements, sources web
  core/         transversal : sécurité, filtres d'affichage, fonctionnalités, événements
  tests/        tests automatisés
"""
import os

from flask import Flask, render_template

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")

from . import config
from .business import accounts, agenda_analysis, exams, planner, subjects
from .business.text import JOURS_COURTS
from .core import features, filters, security
from .dao import db


def create_app(test_config=None):
    if not (test_config or {}).get("TESTING"):  # les tests ne dépendent pas des clés de la machine
        config.load_env(os.path.join(ROOT, ".env"))  # clés d'API des modèles de langage (voir .env.example)
    app = Flask(__name__, instance_relative_config=True,
                template_folder=os.path.join(FRONTEND, "templates"), static_folder=os.path.join(FRONTEND, "static"))
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(config.defaults(app.instance_path))
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    security.init_app(app)
    filters.init_app(app)
    # Libellés partagés par les gabarits
    app.jinja_env.globals.update(
        goals=subjects.GOALS, comfort=subjects.COMFORT, kinds=planner.KIND_LABELS, methods=planner.METHODS,
        jours=JOURS_COURTS, event_kinds=agenda_analysis.EVENT_KINDS, levels=accounts.LEVELS, licenses=accounts.LICENSE, internship_choices=accounts.INTERNSHIP,
        study_fields=accounts.STUDY_FIELDS,
        exam_sources=exams.SOURCES, exam_soon_days=exams.EXAM_SOON_DAYS)
    # Sans doublon, ordre gardé : un plugin listé dans FEATURES et EXTRA_FEATURES n'est chargé qu'une fois.
    features.load_features(app, list(dict.fromkeys(app.config["FEATURES"] + app.config["EXTRA_FEATURES"])))

    with app.app_context():
        from .services import auth_service
        auth_service.ensure_admin()

    for code, message in ((403, "Accès réservé aux administrateurs."), (404, "Page introuvable."),
                          (413, "Fichier trop volumineux (25 Mo max).")):
        app.register_error_handler(code, lambda _e, c=code, m=message: (
            render_template("error.html", code=c, message=m), c))
    app.register_error_handler(400, lambda e: (render_template("error.html", code=400, message=e.description), 400))
    return app
