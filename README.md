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

## Structure

```
app/
  planner.py       moteur de planification (pur Python, testé)
  extractor.py     extraction du texte et des chapitres des documents
  calendar_ics.py  lecture/écriture iCalendar (récurrences comprises)
  resources.py     recherche de ressources (Wikipédia, web, plateformes)
  auth.py          inscription, connexion, rôles
  admin.py         espace administrateur (CRUD)
  routes.py        pages de l'application
  db.py            schéma SQLite
  templates/, static/
run.py
tests/
```

Données stockées dans `instance/` (base SQLite, fichiers déposés, clé secrète), exclu de git.
