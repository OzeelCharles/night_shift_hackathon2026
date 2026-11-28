"""Lancement en développement : python run.py  puis ouvrir http://127.0.0.1:5000

En production (RIVEZLI_ENV=production), l'application se sert avec un serveur WSGI, jamais app.run() (F-06) :
    waitress-serve --listen=127.0.0.1:8000 run:app        (Windows)
    gunicorn -w 3 -t 60 -b 127.0.0.1:8000 run:app          (Linux)
derrière un proxy HTTPS (Caddy ou Nginx).
"""
import os
import sys

from backend import create_app

app = create_app()

if __name__ == "__main__":
    if app.config.get("PRODUCTION"):
        sys.exit("Production : lance l'application avec waitress ou gunicorn (voir l'en-tête de run.py), "
                 "pas avec le serveur de développement.")
    # débogueur interactif : seulement en local, à la demande (FLASK_DEBUG=1), et jamais exposé au réseau
    app.run(host="127.0.0.1", debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
