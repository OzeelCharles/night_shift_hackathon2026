"""Version mobile : manifeste, service worker et page hors ligne (Rivezli s'installe comme une appli sur le téléphone).

Confidentialité : le service worker ne met en cache que des fichiers statiques et la page hors ligne. Jamais les pages
de l'application (planning, notes…) : elles contiennent des données personnelles et ne doivent pas rester sur un
téléphone partagé après une déconnexion.
"""
import hashlib
import os

from flask import Blueprint, Response, current_app, jsonify, render_template, url_for

from backend.core.features import Feature

bp = Blueprint("pwa", __name__)

# Fichiers statiques mis en cache à l'installation (le chemin est relatif à frontend/static).
PRECACHE = ["style.css", "pwa.js", "logo.png", "favicon-32.png", "icons/icon-192.png", "icons/icon-512.png"]
# Raccourcis proposés par un appui long sur l'icône (Android) : (route, libellé). Ignorés si la fonctionnalité est absente.
SHORTCUTS = [("main.dashboard", "Aujourd'hui"), ("planning.week", "Planning"), ("campus.index", "Campus"),
             ("caffeine.index", "Caféine")]
THEME, DARK_THEME = "#fcf4f1", "#17161f"


def _version() -> str:
    """Empreinte des fichiers précachés : le cache est renouvelé dès que l'un d'eux change."""
    digest = hashlib.sha1()
    for name in PRECACHE:
        with open(os.path.join(current_app.static_folder, name), "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()[:10]


@bp.route("/manifest.webmanifest")
def manifest():
    icon = lambda name, size, purpose: {"src": url_for("static", filename=f"icons/{name}"), "sizes": f"{size}x{size}",
                                        "type": "image/png", "purpose": purpose}
    body = {
        "name": "Rivezli",
        "short_name": "Rivezli",
        "description": "Ton année d'étudiant, enfin organisée : planning de révision, notes, campus et stage.",
        "lang": "fr",
        "start_url": url_for("main.dashboard") if "main.dashboard" in current_app.view_functions else "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": THEME,
        "theme_color": THEME,
        "categories": ["education", "productivity"],
        "icons": [icon("icon-192.png", 192, "any"), icon("icon-512.png", 512, "any"),
                  icon("icon-maskable-512.png", 512, "maskable")],
        "shortcuts": [{"name": label, "url": url_for(endpoint),
                       "icons": [{"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png"}]}
                      for endpoint, label in SHORTCUTS if endpoint in current_app.view_functions],
    }
    reply = jsonify(body)
    reply.mimetype = "application/manifest+json"
    reply.headers["Cache-Control"] = "public, max-age=3600"
    return reply


@bp.route("/sw.js")
def service_worker():
    """Servi à la racine : un service worker ne contrôle que les adresses situées sous son propre chemin."""
    precache = [url_for("pwa.offline")] + [url_for("static", filename=n) for n in PRECACHE]
    reply = Response(render_template("pwa/sw.js", version=_version(), precache=precache, offline=url_for("pwa.offline")),
                     mimetype="text/javascript")
    reply.headers["Cache-Control"] = "no-cache"          # le navigateur revérifie le fichier à chaque ouverture
    reply.headers["Service-Worker-Allowed"] = "/"
    return reply


@bp.route("/hors-ligne")
def offline():
    return render_template("pwa/offline.html")


feature = Feature("pwa", blueprints=[bp], template_globals={"pwa_enabled": True})
