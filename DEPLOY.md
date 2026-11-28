# Mettre Rivezli en ligne (et l'installer sur un téléphone)

Le dépôt est prêt pour un hébergeur qui lance une commande web (Render, Railway, Fly.io, un VPS…). Le `Procfile` contient la commande :

    gunicorn run:app --preload --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 120

## Variables d'environnement à définir chez l'hébergeur

| Variable | Rôle |
|---|---|
| `RIVEZLI_ENV=production` | cookie de session en https seulement, HSTS, clé secrète obligatoire |
| `SECRET_KEY` | longue chaîne aléatoire : `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | compte administrateur créé au premier démarrage |
| `INSTANCE_DIR` | dossier **durable** (disque persistant) pour la base SQLite et les documents envoyés |
| `GROQ_API_KEY` / `ANTHROPIC_API_KEY` | assistant et atelier « Réviser » (facultatif) |
| `DONATION_URL` | lien de don (facultatif) |
| `TRUST_PROXY` | nombre de proxies devant l'appli (1 par défaut ; 0 pour désactiver) |

Sans disque persistant, la base SQLite est effacée à chaque déploiement : prévois un disque monté (par exemple sur `/data`, puis `INSTANCE_DIR=/data`).
Ne mets jamais de clé dans le dépôt : elles se saisissent uniquement dans le tableau de bord de l'hébergeur.

## Installer l'appli sur le téléphone

Une fois le site ouvert en **https** :
- **Android (Chrome)** : bouton « Installer l'appli » en bas de la page d'accueil, ou menu ⋮ > « Installer l'application ».
- **iPhone (Safari)** : Partager > « Sur l'écran d'accueil ».

L'appli s'ouvre en plein écran sur « Aujourd'hui ». Hors connexion, une page d'attente s'affiche : les pages personnelles (planning, notes…) ne sont jamais conservées sur l'appareil.

## Essai en local, comme en production

    RIVEZLI_ENV=production SECRET_KEY=essai INSTANCE_DIR=/tmp/rivezli gunicorn run:app --preload --bind 127.0.0.1:5000
    (le cookie « Secure » demande https : la connexion ne fonctionne qu'à travers un proxy https, ou en laissant RIVEZLI_ENV vide)
