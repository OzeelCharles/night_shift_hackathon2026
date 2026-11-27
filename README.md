# Rivezli — planificateur de révisions

Hackathon Sevel Lab ENSAI, novembre 2026.

Rivezli construit un planning de révision personnalisé à partir de :
- **l'agenda** de l'étudiant (facultatif) : import `.ics` (Google Agenda, Outlook, ENT, cours récurrents compris) ou saisie manuelle ;
- **ses supports de cours** (PDF, PPTX, DOCX, ODT, ODP, TXT/MD) dont les chapitres sont extraits automatiquement, **ou du seul nom de la matière** (plan proposé à partir de Wikipédia) ;
- **ses préférences** : méthode de travail (Pomodoro, sessions longues, rappel actif / flashcards, fiches, mixte), durée des séances, pauses, temps max par jour, horaires, jours de repos, marge autour des événements ;
- **son objectif** : préparer un examen (date) ou être à jour (heures par semaine) ;
- **son aisance** dans chaque matière (1 à 5), qui ajuste le temps d'étude et d'entraînement.

Il propose aussi des **ressources en ligne** liées au cours : articles Wikipédia, cours et exercices corrigés en PDF (recherche web), liens vers YouTube, Khan Academy, OpenClassrooms, FUN MOOC et Wikiversité.

Un **compte est obligatoire** pour utiliser le service. Un **administrateur** dispose d'un CRUD complet sur les utilisateurs (création, modification, rôle, désactivation, réinitialisation du mot de passe, suppression) et peut consulter et supprimer les matières.

## Lancer l'application

```powershell
pip install -r requirements.txt
python run.py
```

Puis ouvrir http://127.0.0.1:5000.

Au **premier lancement**, un compte administrateur est créé et ses identifiants s'affichent dans le terminal.
On peut les fixer à l'avance :

```powershell
$env:ADMIN_EMAIL = "admin@ecole.fr"; $env:ADMIN_PASSWORD = "un-mot-de-passe-solide"; python run.py
```

## Tests

```powershell
python -m unittest discover -s backend/tests -t .
```

## Fonctionnement du planning

1. Chaque matière est découpée en tâches :
   - **examen** : étude de chaque chapitre (90 min × facteur d'aisance), révisions espacées à J+1, J+3, J+7 et J+14, puis annales et examen blanc sur le dernier quart de la période ; rien n'est placé le jour de l'examen ;
   - **être à jour** : budget hebdomadaire réparti entre avancement (50 à 60 %) et révision des chapitres déjà vus.
2. Les créneaux libres sont calculés jour par jour (horaires, jours de repos, agenda + marge).
3. Les tâches sont placées par **échéance la plus proche**, en respectant l'ordre étude → révision et en **alternant les matières**.
4. Si le temps manque, l'application l'indique matière par matière.
5. Après un imprévu, « Régénérer » conserve les séances cochées comme faites, ne replanifie pas les chapitres déjà étudiés et replace le reste.

Le planning s'exporte en `.ics` pour l'importer dans n'importe quel agenda.

## Agenda intelligent

- **Rapprochement des noms** (`backend/business/matching.py`) : « TD Proba B204 » est rattaché à la matière « Probabilités »
  par similarité cosinus sur les mots (racines, abréviations), seuil 0,75 calibré sur des cas réels.
- **Suggestions de matières** (`backend/business/embeddings.py`, `backend/business/agenda_analysis.py`) : à la création d'une matière, les cours
  de l'agenda sans matière sont proposés, puis classés pendant la saisie par similarité cosinus entre embeddings.
  Les embeddings sont des vecteurs de 512 dimensions obtenus par hachage de traits (racines, débuts de mots,
  n-grammes de caractères), sans modèle à télécharger ; `embed()` est le seul point à remplacer pour brancher
  un modèle sémantique.
- **Dates d'examen**, par ordre de priorité : la date choisie par l'utilisateur ; sinon la prochaine épreuve de
  l'emploi du temps (« Partiel de probabilités », « DS de maths »…, y compris notée « journée entière ») ou ajoutée
  à la main sur la page de la matière ; sinon, **par défaut, la dernière séance de cours**. Un cours ajouté après
  la dernière séance repousse automatiquement l'examen.
- **Mise à jour de l'emploi du temps** : un nouvel import est synchronisé (UID des événements) : seuls les cours
  ajoutés, déplacés ou supprimés changent, les rattachements corrigés à la main sont conservés, les cours répétés
  pour chaque groupe de TD sont fusionnés, et le planning est *réparé* : seules les séances devenues impossibles
  sont replacées.
- **Ressources d'examen** : à moins de 21 jours de l'examen, annales et sujets corrigés (de l'établissement d'abord)
  et vidéos de révision sont ajoutés automatiquement.

## Dossier scolaire

Espace à part dans la barre du haut (à côté d'Encadrement). Il liste toutes les matières de l'utilisateur, notées
ou non : celles qu'il a créées et les cours de son agenda, au départ « Sans note ». Pour chaque note : valeur,
barème, coefficient, intitulé et date. Le dossier affiche la moyenne de chaque matière (sur 20, pondérée),
l'effet de la dernière note (en progrès / en baisse), une mini-courbe par matière et la courbe de la moyenne
générale (avec infobulle et tableau des valeurs).

- **Confidentialité** : les notes ne sont lues et modifiées que par leur propriétaire (toutes les requêtes filtrent
  sur l'utilisateur connecté) ; l'administration n'y a pas accès ; supprimer un compte supprime ses notes.
- **Historique** : une note saisie pour un cours de l'agenda est rattachée à la matière quand celle-ci est créée ;
  supprimer une matière garde ses notes (« ancienne matière »).
- Code : `backend/business/grades.py` (calculs), `backend/services/dossier_service.py`, `backend/dao/grades.py`,
  `backend/controllers/dossier.py`, `frontend/templates/dossier/index.html`.

## Établissements de France

L'autocomplétion de l'école ou de l'université (inscription, profil, admin) cherche parmi environ 18 000 collèges,
lycées et établissements du supérieur, dans `backend/data/ecoles_france.json`. Ce fichier est construit à partir des
données publiques de l'Éducation nationale et de l'Enseignement supérieur (annuaire de l'éducation, Parcoursup,
Mon Master…). Pour le mettre à jour :

```powershell
python scripts/build_schools.py
```

## Architecture en couches

Chaque couche n'utilise que celles du dessous (vérifié par `backend/tests/test_architecture.py`) :

```
controllers/   HTTP : lit la requête, appelle un service, affiche (flash + gabarit). Aucune règle métier.
    │
services/      Cas d'usage : orchestrent DAO et règles métier, renvoient un Report (messages à afficher).
    │
business/      Règles pures, testables seules : aucun Flask, aucune base, aucun réseau.
dao/           Accès aux données : SQLite (une fonction par requête), fichier des écoles, sources web.

core/          Transversal : sécurité (connexion, CSRF), filtres d'affichage, fonctionnalités, événements.
```

```
backend/
  __init__.py            create_app : configuration, base, sécurité, chargement des fonctionnalités
  config.py              configuration par défaut, liste des fonctionnalités (FEATURES)
  core/                  security.py, filters.py, features.py (plugins), events.py (bus d'événements)
  controllers/           auth, main (tableau de bord, réglages), planning, agenda, subjects, resources, api, admin
  services/              auth, preference, planning, agenda, subject, exam, resource, suggestion, admin
  business/              planner, matching, embeddings, agenda_analysis, exams, subjects, accounts,
                         resource_rules, school_search, calendar_ics, extractor, text
  dao/                   db (schéma, migrations, transaction par requête), users, preferences, subjects,
                         documents, agenda, sessions, resources, schools, web
  data/                  liste des établissements de France
  tests/                 tests automatisés (et exemple de plugin : fake_feature.py)
frontend/                interface : templates/ (gabarits Jinja) et static/ (CSS…), voir frontend/README.md
scripts/build_schools.py
run.py
```

Chaque requête HTTP est une transaction : tout ce qu'elle écrit est validé à la fin, ou annulé en cas d'erreur.

## Ajouter une fonctionnalité (plugin)

Une fonctionnalité est un module qui expose une variable `feature`. Exemple complet : `backend/tests/fake_feature.py`.

```python
from flask import Blueprint
from app.core import events
from app.core.features import Feature, NavItem

bp = Blueprint("stats", __name__, template_folder="templates")

@bp.route("/stats")
def page(): ...

def on_subject_created(user_id, subject_ids, **_):
    return ["Message affiché à l'utilisateur (facultatif)."]

feature = Feature(
    "stats",
    blueprints=[bp],                                         # ses pages
    nav=[NavItem("stats.page", "Statistiques", order=35)],   # son entrée de menu (admin=True : réservée aux admins)
    subscriptions=[(events.SUBJECT_CREATED, on_subject_created)],  # ses réactions aux événements
    subject_panels=["stats_panel.html"],                     # un panneau en bas de chaque page matière
)
```

Pour l'activer sans toucher au cœur : variable d'environnement `EXTRA_FEATURES=mon_paquet.stats`
(plusieurs modules séparés par des virgules), ou clé `EXTRA_FEATURES` passée à `create_app`.

Événements disponibles (`backend/core/events.py`) : `profile.changed`, `subject.created`, `subject.deleted`,
`agenda.changed`, `plan.updated`. Les fonctionnalités du cœur utilisent le même mécanisme : par exemple, la
recherche de ressources réagit à `subject.created` et `profile.changed`.

Données stockées dans `instance/` (base SQLite, fichiers déposés, clé secrète), exclu de git.
