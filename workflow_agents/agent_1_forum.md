## Contexte

Rivezli est une app Flask/SQLite de planification de révisions (hackathon ENSAI). Elle a une architecture en couches (controllers/services/business/dao, vérifiée par `backend/tests/test_architecture.py`) et un système de plugins : un module expose `feature = Feature(...)` (voir le README, section « Ajouter une fonctionnalité », et `backend/tests/fake_feature.py` ; exemples complets dans `backend/plugins/revision/` et `backend/plugins/stages/`). Trois agents travaillent en parallèle, chacun sur son propre plugin : pour éviter les conflits, tu ne touches QUE ton dossier de plugin et ton fichier de test. Réponds et écris l'interface en français.

## Tâche : plugin « Forum »

Crée le plugin « Forum » (listé « À venir : forums » dans le README) dans `backend/plugins/forum/`, sur le modèle de `backend/plugins/stages/`.

Fonctionnalités :
- fils de discussion par matière (et général), questions avec titre et corps Markdown/LaTeX (réutiliser le rendu marked + DOMPurify + KaTeX déjà utilisé par l'assistant), réponses, vote « utile », marquer « résolu » par l'auteur ;
- visibilité entre étudiants du même établissement (champ école du compte) ;
- modération : l'admin peut supprimer messages et fils ; l'auteur peut modifier et supprimer les siens ;
- entrée de menu « Forum » (`NavItem`), et un panneau en bas de la page matière (`subject_panels`) listant les derniers fils de cette matière ;
- optionnel : un outil assistant en lecture seule (`Tool`) pour chercher dans le forum.

Tables propres préfixées `fo_*`, créées au démarrage, supprimées en cascade avec la matière ou le compte. CSRF et contrôle d'accès comme le reste de l'app.

## Contraintes

- Ne modifie PAS `backend/config.py` ni aucun fichier hors de `backend/plugins/forum/` et `backend/tests/test_forum.py` (teste l'activation avec `EXTRA_FEATURES=backend.plugins.forum`).
- Ajoute des tests unittest ; `python -m unittest discover -s backend/tests -t .` doit passer en entier.
- Vérifie que les pages s'affichent (`$env:EXTRA_FEATURES="backend.plugins.forum"; python run.py`, http://127.0.0.1:5000).
- Commit sur ta branche à la fin avec un message clair.
- Termine par un résumé : fichiers créés, ligne à ajouter dans `FEATURES`, résultats des tests.
