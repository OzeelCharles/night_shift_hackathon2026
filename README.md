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

## Atelier de révision (fonctionnalité enfichable)

Onglet **Réviser** de la barre du haut, à côté d'**Encadrement** (et un panneau en bas de la page de chaque matière). À partir des supports d'une matière :

- **Niveau de difficulté demandé** pour chaque matière avant toute révision (Facile, Moyen, Difficile ; proposition
  d'après l'aisance déclarée), réglable aussi **chapitre par chapitre**.
- **Fiches de révision** par chapitre : définitions, théorèmes et propriétés, formules, l'essentiel, mots-clés.
  En difficile, les définitions deviennent des cartes à retourner (rappel actif). Imprimables.
- **QCM notés sur 20**, sur toute la matière ou un chapitre, **rédigés par Claude après lecture des PDF du cours**
  (formules comprises : densités, lois, théorèmes et leurs hypothèses), mathématiques en LaTeX affichées par KaTeX.
  Le niveau change le nombre de questions (8, 10, 15), de choix (3, 4, 5), la proximité des distracteurs et ajoute
  −0,25 par erreur en difficile. Correction détaillée après la copie.
- **Carnet de notes** : courbe de la note au fil des essais, moyenne, meilleure note, progression, réussite par
  chapitre (« À retravailler » sous 50 %), historique avec accès aux corrections. Après 3 notes ≥ 16/20 (ou ≤ 8/20),
  l'atelier propose de changer de niveau.
- **Exercices** : énoncés « Exercice n » (et leur corrigé) repérés dans tes supports, puis dans des PDF d'exercices
  corrigés trouvés en ligne (adaptés au chapitre, à la difficulté et à ton niveau d'études), classés par chapitre
  avec une difficulté estimée. **Si la recherche ne trouve rien, Claude rédige des exercices corrigés d'après le cours.**
- **Notebooks Jupyter d'exercices** pour les cours de data science (régression, apprentissage, statistiques…) :
  d'abord des notebooks existants trouvés en ligne (GitHub, Kaggle, Colab ; lien « Ouvrir dans Colab » pour GitHub).
  **Si la recherche ne trouve rien, Claude en rédige un** : énoncés, code à compléter (`# TODO`) et corrigés en fin
  de notebook, sur données simulées, téléchargeable en `.ipynb`.

Sans support déposé, l'atelier renvoie vers la page de la matière (espace Encadrement) pour en déposer.

**Claude** (`ai.py`, modèle `claude-opus-5-5`, réglable par `REVISION_AI_MODEL` et `REVISION_AI_EFFORT`) lit les PDF
eux-mêmes : le texte extrait des PDF abîme les formules (σ devient ε, Σ devient !). Sans clé, ou si Claude ne répond
pas, les QCM sont fabriqués par des règles de traitement du texte (`generator.py`) et rien n'est rédigé par Claude.

La clé d'API n'est **jamais** dans le code ni dans git. Au choix :

- variable d'environnement, propre à ta session Windows (`setx` l'enregistre pour les prochains terminaux) :
  `setx ANTHROPIC_API_KEY "sk-ant-..."` ;
- ou fichier `instance/anthropic_api_key` contenant seulement la clé : le dossier `instance/` est ignoré par git
  (comme la clé secrète du site), et `.gitignore` exclut aussi tout fichier `anthropic_api_key` et `.env`.

**Alternative Groq** (offre gratuite) : une clé `gsk_…` dans `GROQ_API_KEY`, dans `instance/groq_api_key`, ou même
rangée dans `instance/anthropic_api_key`, est reconnue et prioritaire. QCM, exercices et notebooks passent alors par
Groq (`openai/gpt-oss-120b`, modifiable par `REVISION_GROQ_MODEL`). Groq ne lit pas les PDF : il reçoit un extrait du
texte des supports, limité à ~9 000 caractères pour tenir dans le quota gratuit (8 000 jetons par minute) ; compter
une minute entre deux générations.

**Lecture de photos** (même clé Groq, modèle `qwen/qwen3.8-27b`, modifiable par `VISION_MODEL`) :
- *Mon agenda › Ajouter depuis une photo* : une photo d'emploi du temps ou de liste d'examens devient des créneaux
  (source « photo », rattachés aux matières comme un import .ics, sans doublon si on renvoie la même photo) ;
- *page d'une matière › Ajouter depuis des photos* : chaque photo de notes, de polycopié ou du tableau est recopiée
  en Markdown (formules en LaTeX) et rangée comme support de cours : chapitres détectés, atelier de révision.
Code : `backend/dao/vision.py` (appel à Groq), `backend/business/timetable_photo.py` (créneaux tirés de la réponse).

**Recherche de stage** (plugin `backend/plugins/stages/`, espace « Stages » de la barre du haut, tables `stg_*`) :
- postes recherchés → recherches préremplies sur Indeed, LinkedIn, Glassdoor, Welcome to the Jungle, HelloWork,
  JobTeaser et Google. Ces sites interdisent l'aspiration de leurs résultats : on ouvre leur propre recherche ;
- offres suivies avec statut de candidature ; une offre est lue à partir de son lien quand la page publie ses
  données schema.org `JobPosting` (sinon on recopie l'intitulé et le texte) ;
- lettres de motivation rédigées par Groq (`openai/gpt-oss-120b`, modifiable par `STAGES_MODEL`) d'après un profil
  candidat valable pour toute filière (domaine, formation, compétences, expériences), avec ton et langue au choix ;
  sans clé, une lettre type remplie avec le profil. Lettres modifiables, copiables et téléchargeables.

**Plug and play** : tout est dans `backend/plugins/revision/` (routes, logique, gabarits, style) avec ses propres
tables (`rev_*`, créées au démarrage, supprimées en cascade avec la matière). Il est activé par une ligne de
`FEATURES` dans `backend/config.py` ; supprimer cette ligne retire la fonctionnalité sans rien casser.

## Campus : affluence de la BU et des restos U

Espace **Campus** de la barre du haut, deux onglets :

- **Bibliothèque** : l'affluence de la BU de l'établissement indiqué dans le profil.
- **Restos U** : les restaurants et cafétérias Crous autour de l'école (ville déduite de l'établissement, modifiable),
  avec leur état du jour, leur affluence et le **menu du jour**.

**D'où viennent les chiffres.** Il n'existe aucune source publique d'affluence en temps réel : l'API d'Affluences est
réservée aux établissements partenaires et les Crous n'en publient pas. Rivezli ne scrape donc rien et n'invente rien :
- l'indicateur **« en direct »** vient des signalements des étudiants (Vide, Calme, Animé, Plein), valables 45 min,
  pondérés par leur fraîcheur, avec une médiane pour qu'un signalement fantaisiste ne fasse pas basculer l'indicateur ;
  un signalement par lieu et par utilisateur toutes les 10 min ; une BU = un établissement ;
- **sans signalement récent**, la page affiche la fréquentation **habituelle** à cette heure, étiquetée « estimation »
  (profils types dans `backend/business/campus.py`), et propose les créneaux les plus calmes.

**Restaurants et menus** : API [CROUStillant](https://www.data.gouv.fr/dataservices/api-croustillant) (communautaire,
sans clé, 200 requêtes/min, usage non commercial), mise en cache 6 h (restaurants) et 30 min (menus) ; en cas de panne,
la dernière donnée connue est conservée. Ville géolocalisée par la Géoplateforme de l'IGN. Tout est coupé par
`ENABLE_WEB = False`. Si la BU de l'école figure sur Affluences, la page y renvoie pour le comptage officiel.

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
