# Révisio — planificateur de révisions

Hackathon Sevel Lab ENSAI, novembre 2026.

Révisio construit un planning de révision personnalisé à partir de :
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
python -m unittest discover -s tests -t .
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

- **Rapprochement des noms** (`app/matching.py`) : « TD Proba B204 » est rattaché à la matière « Probabilités »
  par similarité cosinus sur les mots (racines, abréviations), seuil 0,75 calibré sur des cas réels.
- **Suggestions de matières** (`app/embeddings.py`, `app/suggestions.py`) : à la création d'une matière, les cours
  de l'agenda sans matière sont proposés, puis classés pendant la saisie par similarité cosinus entre embeddings.
  Les embeddings sont des vecteurs de 512 dimensions obtenus par hachage de traits (racines, débuts de mots,
  n-grammes de caractères), sans modèle à télécharger ; `embed()` est le seul point à remplacer pour brancher
  un modèle sémantique.
- **Dates d'examen** : un événement « Partiel de probabilités », « DS de maths », « Examen final - Droit »… fixe la date
  d'examen de la matière correspondante (y compris les épreuves notées « journée entière » dans le .ics).

## Établissements de France

L'autocomplétion de l'école ou de l'université (inscription, profil, admin) cherche parmi environ 18 000 collèges,
lycées et établissements du supérieur, dans `app/data/ecoles_france.json`. Ce fichier est construit à partir des
données publiques de l'Éducation nationale et de l'Enseignement supérieur (annuaire de l'éducation, Parcoursup,
Mon Master…). Pour le mettre à jour :

```powershell
python scripts/build_schools.py
```

## Structure

```
app/
  planner.py       moteur de planification (pur Python, testé)
  extractor.py     extraction du texte et des chapitres des documents
  calendar_ics.py  lecture/écriture iCalendar (récurrences comprises)
  matching.py      rapprochement des noms de cours (similarité cosinus)
  embeddings.py    embeddings d'intitulés et classement par similarité cosinus
  suggestions.py   suggestions de matières depuis l'agenda, lecture des dates d'examen
  schools.py       autocomplétion des établissements de France
  resources.py     recherche de ressources (Wikipédia, web, vidéos, plateformes) selon le niveau
  auth.py          inscription, connexion, profil, rôles
  admin.py         espace administrateur (CRUD)
  routes.py        pages de l'application
  db.py            schéma SQLite et migrations
  data/            liste des établissements de France
  templates/, static/
scripts/build_schools.py
run.py
tests/
```

Données stockées dans `instance/` (base SQLite, fichiers déposés, clé secrète), exclu de git.
