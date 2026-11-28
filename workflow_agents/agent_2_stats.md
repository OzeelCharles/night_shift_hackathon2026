## Contexte

Rivezli est une app Flask/SQLite de planification de révisions (hackathon ENSAI). Elle a une architecture en couches (controllers/services/business/dao, vérifiée par `backend/tests/test_architecture.py`) et un système de plugins : un module expose `feature = Feature(...)` (voir le README, section « Ajouter une fonctionnalité », et `backend/tests/fake_feature.py` ; exemples complets dans `backend/plugins/revision/` et `backend/plugins/stages/`). Trois agents travaillent en parallèle, chacun sur son propre plugin : pour éviter les conflits, tu ne touches QUE ton dossier de plugin et ton fichier de test. Réponds et écris l'interface en français.

## Tâche : plugin « Statistiques »

Crée le plugin « Statistiques » dans `backend/plugins/stats/`, sur le modèle de `backend/plugins/stages/`.

Fonctionnalités, en lisant les données existantes (séances planifiées et cochées dans `backend/dao/sessions.py`, matières, notes de QCM du plugin révision `rev_*`, préférences) :
- page « Progression » : heures révisées par semaine et par matière, taux de séances faites / prévues, série de jours consécutifs, calendrier d'activité des 12 dernières semaines, avancement des chapitres par matière, compte à rebours des examens avec estimation « à l'heure / en retard » ;
- objectif hebdomadaire personnel (table propre `st_*`) et badges simples (première semaine complète, 7 jours d'affilée…) ;
- un widget compact (`widgets`) ou un panneau de page matière (`subject_panels`) ;
- un outil assistant en lecture seule (`Tool`) « ma_progression ».

Calculs dans des fonctions pures testables (pas de Flask ni de base dans la logique), graphiques en SVG/HTML sans nouvelle dépendance lourde, lisibles en thème clair et sombre.

## Contraintes

- Lecture seule sur les tables des autres modules.
- Ne modifie PAS `backend/config.py` ni aucun fichier hors de `backend/plugins/stats/` et `backend/tests/test_stats.py` (teste l'activation avec `EXTRA_FEATURES=backend.plugins.stats`).
- `python -m unittest discover -s backend/tests -t .` doit passer en entier.
- Vérifie dans le navigateur avec quelques données (`$env:EXTRA_FEATURES="backend.plugins.stats"; python run.py`, http://127.0.0.1:5000).
- Commit sur ta branche à la fin avec un message clair.
- Termine par un résumé : fichiers créés, ligne à ajouter dans `FEATURES`, résultats des tests.
