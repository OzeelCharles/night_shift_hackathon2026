# Frontend

Interface de Révisio, servie par le backend Flask (`../backend`).

```
frontend/
  templates/   gabarits Jinja (pages HTML rendues par les contrôleurs du backend)
    base.html            mise en page commune : barre du haut, menu, messages
    landing.html         accueil (visiteur non connecté)
    dashboard.html       « Aujourd'hui »
    plan.html            planning de la semaine
    calendar.html        « Mon agenda »
    preferences.html     « Mes réglages »
    subjects/            création / modification et page d'une matière
    auth/                inscription, connexion, profil
    admin/               espace administrateur
    error.html           pages d'erreur
  static/      fichiers servis tels quels sous /static/ (style.css…)
```

Le backend y est relié dans `backend/__init__.py` (`template_folder` et `static_folder`).

Conventions à respecter dans les gabarits :
- liens : `url_for('<fonctionnalité>.<route>')`, par exemple `url_for('subjects.detail', subject_id=s.id)` ;
- tout formulaire POST contient `{{ csrf_field() }}` ;
- libellés partagés disponibles partout : `goals`, `comfort`, `kinds`, `methods`, `jours`, `event_kinds`,
  `levels`, `exam_sources`, `exam_soon_days` ; menu : `nav_items` ;
- les fichiers statiques s'appellent avec `url_for('static', filename='…')`.
