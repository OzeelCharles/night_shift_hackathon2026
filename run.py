"""Lancement : python run.py  puis ouvrir http://127.0.0.1:5000
Sur le même Wi-Fi, depuis un téléphone : HOST=0.0.0.0 python run.py"""
import os

from backend import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)))
