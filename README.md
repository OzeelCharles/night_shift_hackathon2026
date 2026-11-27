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

## Clés d'API (modèles de langage)

Toutes les clés se mettent dans le fichier **`.env`**, à la racine du projet (à côté de `run.py`). Il est ignoré par
git. S'il n'existe pas, copier `.env.example` en `.env`. Coller chaque clé après le `=`, puis relancer `python run.py`.

| Variable | Fournisseur | Modèles | Utilisé par |
|---|---|---|---|
| `GROQ_API_KEY` | Groq, gratuit (console.groq.com, clé `gsk_…`) | `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` | assistant, atelier « Réviser », lecture de photos |
| `ANTHROPIC_API_KEY` | Claude, payant (console.anthropic.com, clé `sk-ant-…`) | `claude-opus-5-5`, `claude-haiku-5-5` | assistant (prioritaire sur Groq) ; atelier « Réviser » (seulement si `GROQ_API_KEY` est vide) |

Une variable définie dans le terminal reste prioritaire sur le `.env`. Les anciens fichiers `instance/groq_api_key`
et `instance/anthropic_api_key` sont encore lus s'ils existent.

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

La clé d'API n'est **jamais** dans le code ni dans git : elle se met dans le fichier `.env` (`ANTHROPIC_API_KEY=`),
voir « Clés d'API » plus bas.

**Alternative Groq** (offre gratuite) : si `GROQ_API_KEY` est remplie, elle est prioritaire. QCM, exercices et notebooks passent alors par
Groq (`openai/gpt-oss-120b`, modifiable par `REVISION_GROQ_MODEL`). Groq ne lit pas les PDF : il reçoit un extrait du
texte des supports, limité à ~9 000 caractères pour tenir dans le quota gratuit (8 000 jetons par minute) ; compter
une minute entre deux générations.

**Lecture de photos** (même clé Groq, modèle `qwen/qwen3.8-27b`, modifiable par `VISION_MODEL`) :
- *Mon agenda › Ajouter depuis une photo* : une photo d'emploi du temps ou de liste d'examens devient des créneaux
  (source « photo », rattachés aux matières comme un import .ics, sans doublon si on renvoie la même photo) ;
- *page d'une matière › Ajouter depuis des photos* : chaque photo de notes, de polycopié ou du tableau est recopiée
  en Markdown (formules en LaTeX) et rangée comme support de cours : chapitres détectés, atelier de révision.
Code : `backend/dao/vision.py` (appel à Groq), `backend/business/timetable_photo.py` (créneaux tirés de la réponse).

**Plug and play** : tout est dans `backend/plugins/revision/` (routes, logique, gabarits, style) avec ses propres
tables (`rev_*`, créées au démarrage, supprimées en cascade avec la matière). Il est activé par une ligne de
`FEATURES` dans `backend/config.py` ; supprimer cette ligne retire la fonctionnalité sans rien casser.

## Assistant (chatbot)

Bulle flottante en bas à droite de toutes les pages (compte connecté), agrandissable. Fournisseur choisi
automatiquement (`ASSISTANT_PROVIDER`, « auto ») :

- **Claude (Anthropic)** dès que `ANTHROPIC_API_KEY` est remplie : `claude-opus-5-5` (effort `low` pour une
  conversation rapide, consignes mises en cache), résumés par `claude-haiku-5-5`, photos lues directement,
  réponses affichées **au fil de l'écriture** (streaming, route `/assistant/messages/flux`) avec l'étape en cours
  (« Je cherche dans tes supports de cours… ») ; refus d'un filtre de sécurité relancé sur le modèle de secours
  conseillé (`fallbacks: "default"`) ;
- sinon **Groq, offre gratuite** : `openai/gpt-oss-120b` (résumés : `gpt-oss-20b`, photos : `qwen/qwen3.8-27b`),
  réponse affichée d'un bloc.

Une réponse coupée par la limite de longueur est signalée. Clés : fichier `.env` (voir « Clés d'API » ci-dessous) ;
sans clé, la bulle explique comment la configurer.

- **Outils** (lecture seule) : `programme_du_jour`, `chapitres`, `notes`, `graphique_notes`, et `graphique_qcm`
  fourni par l'atelier de révision. Le modèle les appelle pour répondre avec les vraies données de l'étudiant ;
  les graphiques s'affichent sous la réponse.
- **Historique** : chaque conversation est enregistrée (privée, supprimée avec le compte). À chaque question, on
  envoie les 8 derniers messages tels quels et un résumé des plus anciens, ce qui garde la conversation fluide sans
  dépasser le quota gratuit (environ 30 requêtes et 8 000 jetons par minute). Bouton « + » : nouvelle conversation.
- **Photo ou PDF joint** (trombone, Ctrl+V d'une image, ou glisser-déposer) : une photo est d'abord recopiée par
  `qwen/qwen3.8-27b` (texte et formules en LaTeX), un PDF est lu par l'extracteur de texte (un PDF scanné est refusé
  avec le conseil d'envoyer une photo). Ce contenu (6 000 caractères au plus) accompagne la question ; un extrait
  reste dans l'historique pour les questions suivantes. Les fichiers eux-mêmes ne sont pas conservés.
- **Affichage des réponses** : Markdown complet (titres, listes imbriquées, gras, italique, tableaux, citations,
  code, liens) rendu par marked, nettoyé par DOMPurify, et formules LaTeX (`$…$`, `$$…$$`, `\(…\)`, `\[…\]`) rendues
  par KaTeX. Les formules sont mises de côté avant le Markdown pour que leurs `_` et `*` restent intacts ; les
  formules des questions sont rendues aussi. Sans connexion aux bibliothèques, une mise en forme simple prend le relais.
- Quota atteint ou Groq injoignable : message lisible et bouton « Réessayer » ; rien n'est enregistré.
- Code : `backend/business/assistant.py` (consignes, fenêtre d'historique, graphiques), `backend/services/assistant_service.py`
  (boucle d'outils), `backend/services/assistant_tools.py`, `backend/dao/llm.py`, `backend/dao/chat.py`,
  `backend/controllers/assistant.py`, `frontend/templates/assistant/bubble.html`.
- À venir : modification du planning après confirmation (« j'ai raté ma séance »), forums.

### Recherche dans les supports de cours (RAG)

L'outil `chercher_dans_mes_cours` permet à l'assistant de répondre à partir des supports déposés par l'étudiant
(PDF, diapositives, Word, notes en photo), en citant la source : `[S1]` devient un petit numéro cliquable, et la
liste « Sources » sous la réponse ouvre le support à la bonne page.

- **Découpage** (`backend/business/chunking.py`) : passages d'environ 200 mots, coupés en fin de phrase, avec
  30 mots de chevauchement ; un titre de section commence un passage. Diaporamas (peu de mots par page) : une
  diapositive = un passage, titré par sa première ligne. Nettoyage des PDF LaTeX (accents « R´egression »),
  des en-têtes et pieds de page répétés, des diapositives de plan en double.
- **Embeddings** (`backend/dao/embedder.py`) : **EmbeddingGemma-300m**, en local avec fastembed (sans clé, sans
  quota). Retenu après un comparatif sur des questions reformulées : 6/6 contre 3/6 pour MiniLM, mpnet et potion
  multilingues. Téléchargé une fois (~1,2 Go) dans `instance/models`, puis chargé en 2 s, même hors ligne.
- **Recherche hybride** (`backend/business/retrieval.py`) : sens (cosinus) + mots-clés (BM25, index FTS5 de
  SQLite), fusionnés par RRF ; limitée aux supports de l'étudiant et, si elle est connue, à la matière. Chaque
  passage trouvé est donné avec ses voisins de la même section (5 200 caractères au plus pour le quota de Groq).
  Si des passages viennent de plusieurs matières, l'assistant demande laquelle.
- **Citations vérifiées** : un `[S7]` sans source est retiré de la réponse.
- **Indexation** en arrière-plan : à chaque dépôt (événement `documents.added`) et au démarrage pour les supports
  déjà déposés ; une question n'attend jamais (l'assistant signale un support encore en préparation). Supprimer un
  support supprime ses passages. Environ 100 s pour un diaporama de 300 pages.
- Code : `backend/services/rag_service.py`, `backend/dao/rag.py` (tables `doc_chunks`, `doc_chunks_fts`, `doc_index`).
- En test, le modèle est remplacé par les embeddings par hachage du projet (`RAG_EMBEDDINGS=False`).

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
    widgets=["stats_widget.html"],                           # un élément sur toutes les pages (ex. bulle de l'assistant)
    tools=[Tool("stats", "Ce que l'outil donne à l'assistant.", run)],  # un outil de l'assistant (core/tools.py)
)
```

Pour l'activer sans toucher au cœur : variable d'environnement `EXTRA_FEATURES=mon_paquet.stats`
(plusieurs modules séparés par des virgules), ou clé `EXTRA_FEATURES` passée à `create_app`.

Événements disponibles (`backend/core/events.py`) : `profile.changed`, `subject.created`, `subject.deleted`,
`agenda.changed`, `plan.updated`. Les fonctionnalités du cœur utilisent le même mécanisme : par exemple, la
recherche de ressources réagit à `subject.created` et `profile.changed`.

Données stockées dans `instance/` (base SQLite, fichiers déposés, clé secrète), exclu de git.
