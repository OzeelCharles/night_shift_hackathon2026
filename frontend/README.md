# Frontend

Interface de Rivezli, servie par le backend Flask (`../backend`).

```
frontend/
  templates/   gabarits Jinja (pages HTML rendues par les contrôleurs du backend)
    base.html            mise en page commune : barre du haut (les espaces de Rivezli : Encadrement…)
                         et, en dessous, la barre des onglets de l'espace courant (Aujourd'hui, Planning…)
    _topbar.html         morceaux des barres : logo, compte, liens de menu d'une barre
    home.html            page de départ « / » : présentation de Rivezli aux étudiants (cartes des fonctionnalités
                         activées, étapes, appel à créer un compte ou se connecter), hors de tout espace (pas de seconde
                         barre). Les cartes viennent de SHOWCASE dans backend/controllers/main.py
    landing.html         présentation du planificateur, montrée à un visiteur qui ouvre « Encadrement » ;
                         un utilisateur connecté arrive directement sur l'onglet « Aujourd'hui »
    dashboard.html       « Aujourd'hui »
    plan.html            planning de la semaine
    calendar.html        « Mon agenda »
    preferences.html     « Mes réglages »
    subjects/            création / modification et page d'une matière
    auth/                inscription, connexion, profil
    admin/               espace administrateur
    dossier/             espace « Dossier scolaire » : notes privées, moyennes, courbes de progression
    assistant/bubble.html  bulle de l'assistant (chatbot), ajoutée en bas de toutes les pages d'un utilisateur
                         connecté (champ « widgets » des fonctionnalités, inclus par base.html)
    error.html           pages d'erreur
  static/      fichiers servis tels quels sous /static/ (style.css…)
```

Le backend y est relié dans `backend/__init__.py` (`template_folder` et `static_folder`).

Espaces : une page appartient à l'espace « encadrement » par défaut. Une page d'un futur espace écrit
`{% set space = "nom_de_l_espace" %}` juste après son `{% extends "base.html" %}` ; la barre du haut surligne
alors l'espace et la seconde barre affiche ses onglets (entrées de menu déclarées avec `section="nom_de_l_espace"`).
L'entrée de l'espace dans la barre du haut se déclare avec `section="site", opens="nom_de_l_espace"`.

Conventions à respecter dans les gabarits :
- liens : `url_for('<fonctionnalité>.<route>')`, par exemple `url_for('subjects.detail', subject_id=s.id)` ;
- tout formulaire POST contient `{{ csrf_field() }}` ;
- libellés partagés disponibles partout : `goals`, `comfort`, `kinds`, `methods`, `jours`, `event_kinds`,
  `levels`, `exam_sources`, `exam_soon_days` ; menu : `nav_items` ;
- les fichiers statiques s'appellent avec `url_for('static', filename='…')`.
