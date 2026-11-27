# Tableau de bord du duel

Tenu par le manager. Statuts : `ouvert`, `reproduit`, `en_correction`, `a_verifier`, `ferme`, `rejete`, `conforme`.

Copie dédiée : worktree `rivezli-duel`, branche `securite/duel` (depuis `main`, 4baeea2).
Suite de départ : 164 tests, tous verts (2026-11-27).

## Score

| Manche | Attaquant | Défenseur | Remarque |
| --- | --- | --- | --- |
| 0 | 0 | 0 | préparation |
| **Total** | **0** | **0** | |

## Constats de l'audit initial

| ID | Gravité | Titre | Statut | Agent | Test | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | Haute | SSRF sur le lien d'une offre de stage (plugins/stages/service.py) | ouvert | | | |
| F-02 | Moyenne | Rebinding DNS dans pdf_text (plugins/revision/sources.py) | ouvert | | | |
| F-03 | Moyenne | Aucune limite de tentatives sur /connexion et /inscription | ouvert | | | |
| F-04 | Moyenne | Énumération de comptes (inscription, compte désactivé) | ouvert | | | |
| F-05 | Moyenne | Aucun en-tête de sécurité, cookie de session sans Secure | ouvert | | | |
| F-06 | Moyenne | Serveur de développement app.run() et débogueur FLASK_DEBUG | ouvert | | | |
| F-07 | Moyenne | Mot de passe admin initial affiché dans le terminal, adresse prévisible | ouvert | | | |
| F-08 | Moyenne | Bombe zip Office/ODF, PDF sans limite de pages (à vérifier) | ouvert | | | |
| F-09 | Moyenne | Injection de prompt et coût LLM sans quota | ouvert | | | |
| F-10 | Basse | KaTeX depuis cdnjs sans SRI | ouvert | | | |
| F-11 | Basse | DAO qui modifient ou suppriment par id seul (à vérifier) | ouvert | | | |
| F-12 | Basse | Pas de signalement de bug, de mots interdits ni de journal d'audit | ouvert | | | |
| F-13 | Info | SQL en f-string sur des noms de colonnes en liste blanche | ouvert | | | |

## Exigences

| ID | Gravité | Titre | Statut | Agent | Test | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | | Injections SQL et fuite de données | ouvert | | | |
| SEC-02 | | Contrôle d'accès et isolation (IDOR) | ouvert | | | |
| SEC-03 | | Authentification et sessions | ouvert | | | |
| SEC-04 | | CSRF et redirections | ouvert | | | |
| SEC-05 | | Validation des entrées, XSS et mots interdits | ouvert | | | |
| SEC-06 | | Fichiers déposés | ouvert | | | |
| SEC-07 | | Requêtes sortantes (SSRF) | ouvert | | | |
| SEC-08 | | Fonctions IA (LLM) | ouvert | | | |
| SEC-09 | | En-têtes HTTP, cookies et dépendances | ouvert | | | |
| SEC-10 | | Journalisation et audit | ouvert | | | |
| BUG-01 | | Signalement de bug : formulaire | ouvert | | | |
| BUG-02 | | Signalement de bug : stockage | ouvert | | | |
| BUG-03 | | Signalement de bug : anti-abus | ouvert | | | |
| BUG-04 | | Signalement de bug : suivi utilisateur | ouvert | | | |
| BUG-05 | | Signalement de bug : espace admin | ouvert | | | |
| BUG-06 | | Signalement de bug : failles de sécurité | ouvert | | | |
| BDD-01 | | Contraintes d'intégrité | ouvert | | | |
| BDD-02 | | Migrations sûres | ouvert | | | |
| BDD-03 | | Concurrence (WAL, busy_timeout) | ouvert | | | |
| BDD-04 | | Transactions et cascades | ouvert | | | |
| BDD-05 | | Volume | ouvert | | | |
| BDD-06 | | Entrées extrêmes | ouvert | | | |
| BDD-07 | | Sauvegarde et restauration | ouvert | | | |
| BDD-08 | | Données personnelles, suppression de compte | ouvert | | | |
| DEP-01 | | Serveur WSGI et proxy HTTPS | ouvert | | | |
| DEP-02 | | Configuration et secrets | ouvert | | | |
| DEP-03 | | Durcissement du système | ouvert | | | |
| DEP-04 | | Conteneur | ouvert | | | |
| DEP-05 | | Intégration continue | ouvert | | | |
| DEP-06 | | Exploitation | ouvert | | | |

## Fiches ouvertes pendant le duel

| ID | Gravité | Titre | Statut | Agent | Test | Commit |
| --- | --- | --- | --- | --- | --- | --- |
