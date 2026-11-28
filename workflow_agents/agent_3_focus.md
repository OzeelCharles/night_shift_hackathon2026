## Contexte

Rivezli est une app Flask/SQLite de planification de révisions (hackathon ENSAI). Elle a une architecture en couches (controllers/services/business/dao, vérifiée par `backend/tests/test_architecture.py`) et un système de plugins : un module expose `feature = Feature(...)` (voir le README, section « Ajouter une fonctionnalité », et `backend/tests/fake_feature.py` ; exemples complets dans `backend/plugins/revision/` et `backend/plugins/stages/`). Trois agents travaillent en parallèle, chacun sur son propre plugin : pour éviter les conflits, tu ne touches QUE ton dossier de plugin et ton fichier de test. Réponds et écris l'interface en français.

## Tâche : plugin « Mode Focus »

Crée le plugin « Mode Focus » dans `backend/plugins/focus/`, sur le modèle de `backend/plugins/stages/`.

Fonctionnalités :
- page « Focus » : lance la séance en cours ou la prochaine du planning du jour (voir `backend/dao/sessions.py` et le service de planning), avec un minuteur adapté à la méthode choisie dans les préférences (Pomodoro, sessions longues…, durées de séance et de pause) ; plein écran, son ou notification du navigateur en fin de bloc, pause / reprise, l'état survit à un rechargement (côté serveur ou localStorage) ;
- à la fin : marquer la séance comme faite en passant par le service existant (pas d'écriture SQL directe dans les tables des autres modules), noter sa concentration (1 à 5) et une note libre, enregistrées dans une table propre `fx_*` ;
- pendant la séance : rappel des chapitres à travailler et lien direct vers les fiches et QCM du plugin révision de la matière s'il est actif ;
- un widget discret (`widgets`) « Démarrer ma séance » quand une séance du planning est en cours ;
- un outil assistant en lecture seule (`Tool`) « mon_focus » (temps de concentration récent, moyenne de concentration).

## Contraintes

- Ne modifie PAS `backend/config.py` ni aucun fichier hors de `backend/plugins/focus/` et `backend/tests/test_focus.py` (teste l'activation avec `EXTRA_FEATURES=backend.plugins.focus`).
- Si un service du cœur manque pour cocher une séance, signale-le dans ton résumé plutôt que de modifier le cœur.
- `python -m unittest discover -s backend/tests -t .` doit passer en entier.
- Vérifie dans le navigateur (`$env:EXTRA_FEATURES="backend.plugins.focus"; python run.py`, http://127.0.0.1:5000).
- Commit sur ta branche à la fin avec un message clair.
- Termine par un résumé : fichiers créés, ligne à ajouter dans `FEATURES`, résultats des tests.
