"""Rivezli : backend Flask organisé en couches (le frontend est dans ../frontend).

  controllers/  routes HTTP (une fonctionnalité enfichable par module, voir core/features.py)
  services/     cas d'usage : orchestrent les DAO et les règles métier
  business/     règles métier pures (planification, rapprochement des noms, examens…)
  dao/          accès aux données : SQLite, fichier des établissements, sources web
  core/         transversal : sécurité, filtres d'affichage, fonctionnalités, événements
  tests/        tests automatisés
"""
import os
import sqlite3

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")

from . import config
from .business import accounts, agenda_analysis, exams, planner, subjects
from .business.text import JOURS_COURTS
from .core import features, filters, journal, security
from .dao import db


def create_app(test_config=None):
    if not (test_config or {}).get("TESTING"):  # les tests ne dépendent pas des clés de la machine
        config.load_env(os.path.join(ROOT, ".env"))  # clés d'API des modèles de langage (voir .env.example)
    # INSTANCE_DIR : dossier durable (base SQLite, documents envoyés) quand le disque du projet est éphémère chez l'hébergeur
    instance_dir = os.path.abspath(os.environ["INSTANCE_DIR"]) if os.environ.get("INSTANCE_DIR") else None
    app = Flask(__name__, instance_path=instance_dir, instance_relative_config=True,
                template_folder=os.path.join(FRONTEND, "templates"), static_folder=os.path.join(FRONTEND, "static"))
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_mapping(config.defaults(app.instance_path))
    if test_config:
        app.config.update(test_config)
    if app.config.get("PRODUCTION") and not app.config.get("TESTING"):
        problems = config.production_errors(app.config)
        if problems:  # DEP-02 : on s'arrête net plutôt que de démarrer mal protégé
            raise RuntimeError("Démarrage en production refusé :\n- " + "\n- ".join(problems))
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    proxies = int(os.environ.get("TRUST_PROXY", "1"))   # derrière le reverse proxy de l'hébergeur : vrai schéma https, vraie adresse
    if app.config.get("PRODUCTION") and proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    db.init_app(app)
    security.init_app(app)
    journal.init_app(app)
    filters.init_app(app)
    # Libellés partagés par les gabarits
    app.jinja_env.globals.update(
        goals=subjects.GOALS, comfort=subjects.COMFORT, kinds=planner.KIND_LABELS, methods=planner.METHODS,
        jours=JOURS_COURTS, event_kinds=agenda_analysis.EVENT_KINDS, levels=accounts.LEVELS, licenses=accounts.LICENSE, internship_choices=accounts.INTERNSHIP,
        study_fields=accounts.STUDY_FIELDS,
        exam_sources=exams.SOURCES, exam_soon_days=exams.EXAM_SOON_DAYS)
    features.load_features(app, app.config["FEATURES"] + app.config["EXTRA_FEATURES"])

    from .services import common
    with app.app_context():
        from .services import auth_service
        auth_service.ensure_admin()

    for code, message in ((403, "Accès réservé aux administrateurs."), (404, "Page introuvable."),
                          (413, "Fichier trop volumineux (25 Mo max).")):
        app.register_error_handler(code, lambda _e, c=code, m=message: (
            render_template("error.html", code=c, message=m), c))
    app.register_error_handler(400, lambda e: (render_template("error.html", code=400, message=e.description), 400))

    def refused_by_database(_e):
        """BDD-01 / BDD-06 : une valeur que la base refuse (contrainte d'intégrité) ou ne sait pas stocker
        (entier hors bornes) est une saisie invalide, pas une panne : tout ce que la requête a écrit est annulé."""
        db.rollback()
        common.discard_written()
        return render_template("error.html", code=400, message="Valeur refusée : vérifie ta saisie."), 400
    app.register_error_handler(sqlite3.IntegrityError, refused_by_database)
    # F-037 : requête interrompue par une erreur (la base est annulée) : ses fichiers déposés sont retirés
    app.teardown_request(lambda exc: common.discard_written() if exc is not None else None)
    app.register_error_handler(OverflowError, refused_by_database)
    return app
