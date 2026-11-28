# Tableau de bord du duel

Tenu par le manager. Statuts : `ouvert`, `reproduit`, `en_correction`, `a_verifier`, `ferme`, `rejete`, `conforme`.

Copie dédiée : worktree `rivezli-duel`, branche `securite/duel` (depuis `main`, 4baeea2).
Suite de départ : 164 tests, tous verts (2026-11-27).

## Score

| Manche | Attaquant | Défenseur | Remarque |
| --- | --- | --- | --- |
| 0 | 0 | 0 | préparation |
| 1 | 14 | 5 | Attaque : 8 fiches validées, F-01 (+3), F-02/F-03/F-04/F-016 (+2), F-11/F-014/F-015 (+1). Défense : F-02/F-04/F-016 (+2), F-11/F-014 (+1) du premier coup ; contournements de F-01, F-03, F-015 (−1 ×3) |
| 2 | 16 | (voir 2 vérif.) | 12 fiches validées : F-08/F-017/F-020/F-025 (+2), F-018/F-019/F-021/F-022/F-023/F-024/F-026/F-027 (+1) |
| 1 (vérif.) | 6 | — | 3 contournements : F-028, F-029, F-030 (+2 ×3) |
| 3 | 8 | 0 (non contre-vérifié) | 6 fiches validées : F-05/F-09 (+2), F-10/F-031/F-032/F-033 (+1) |
| 2 (vérif.) | 6 | 11 | Défenseur : F-08/F-017/F-020/F-025 (+2), F-018/F-022/F-023/F-024/F-026/F-027 (+1) du premier coup = +14 ; F-028 et F-030 refermées (0) ; contournements F-034/F-035/F-036 (attaquant +6, défenseur −3) ; F-019, F-021, F-029 (+1 chacune) retirées : contournées |
| 4 | 7 | — | 7 fiches validées, toutes Basse (+1) : F-037 à F-043 |
| **Total** | **57** | **16** | |

## Constats de l'audit initial

| ID | Gravité | Titre | Statut | Agent | Test | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | Haute | SSRF sur le lien d'une offre de stage (plugins/stages/service.py) | ferme | | | 7ec912a |
| F-02 | Moyenne | Rebinding DNS dans pdf_text (plugins/revision/sources.py) | ferme | | | 7ec912a |
| F-03 | Moyenne | Aucune limite de tentatives sur /connexion et /inscription | ferme | | | 7ec912a |
| F-04 | Moyenne | Énumération de comptes (inscription, compte désactivé) | ferme | | | 7ec912a |
| F-05 | Moyenne | Aucun en-tête de sécurité, cookie de session sans Secure | corrigé, non contre-vérifié | | | 7ec912a |
| F-06 | Moyenne | Serveur de développement app.run() et débogueur FLASK_DEBUG | ouvert (non traité) | | | |
| F-07 | Moyenne | Mot de passe admin initial affiché dans le terminal, adresse prévisible | ouvert (non traité) | | | |
| F-08 | Moyenne | Bombe zip Office/ODF, PDF sans limite de pages (à vérifier) | ferme | | | 7ec912a |
| F-09 | Moyenne | Injection de prompt et coût LLM sans quota | corrigé, non contre-vérifié | | | 7ec912a |
| F-10 | Basse | KaTeX depuis cdnjs sans SRI | corrigé, non contre-vérifié | | | 7ec912a |
| F-11 | Basse | DAO qui modifient ou suppriment par id seul (à vérifier) | ferme | | | 7ec912a |
| F-12 | Basse | Pas de signalement de bug, de mots interdits ni de journal d'audit | partiel : mots interdits corrigés (F-031), journal d'audit (F-043) et signalement de bug absents | | | |
| F-13 | Info | SQL en f-string sur des noms de colonnes en liste blanche | conforme (analyse statique verte) | | | |

## Exigences

| ID | Gravité | Titre | Statut | Agent | Test | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | | Injections SQL et fuite de données | ferme (conforme + F-027) | | | |
| SEC-02 | | Contrôle d'accès et isolation (IDOR) | ferme | | | |
| SEC-03 | | Authentification et sessions | partiel (F-015/F-036 ouvertes) | | | |
| SEC-04 | | CSRF et redirections | ferme | | | |
| SEC-05 | | Validation des entrées, XSS et mots interdits | corrigé, non contre-vérifié (XSS conforme) | | | |
| SEC-06 | | Fichiers déposés | partiel (F-034, F-035, F-037 ouvertes) | | | |
| SEC-07 | | Requêtes sortantes (SSRF) | ferme | | | |
| SEC-08 | | Fonctions IA (LLM) | corrigé, non contre-vérifié | | | |
| SEC-09 | | En-têtes HTTP, cookies et dépendances | corrigé, non contre-vérifié (HSTS/Secure seulement en production) | | | |
| SEC-10 | | Journalisation et audit | ouvert (F-043) | | | |
| BUG-01 | | Signalement de bug : formulaire | ouvert (non traité, duel arrêté) | | | |
| BUG-02 | | Signalement de bug : stockage | ouvert (non traité, duel arrêté) | | | |
| BUG-03 | | Signalement de bug : anti-abus | ouvert (non traité, duel arrêté) | | | |
| BUG-04 | | Signalement de bug : suivi utilisateur | ouvert (non traité, duel arrêté) | | | |
| BUG-05 | | Signalement de bug : espace admin | ouvert (non traité, duel arrêté) | | | |
| BUG-06 | | Signalement de bug : failles de sécurité | ouvert (non traité, duel arrêté) | | | |
| BDD-01 | | Contraintes d'intégrité | ouvert (F-039) | | | |
| BDD-02 | | Migrations sûres | ouvert (non traité, duel arrêté) | | | |
| BDD-03 | | Concurrence (WAL, busy_timeout) | ouvert (F-040) | | | |
| BDD-04 | | Transactions et cascades | ouvert (F-037, F-041) | | | |
| BDD-05 | | Volume | ouvert (non traité, duel arrêté) | | | |
| BDD-06 | | Entrées extrêmes | ouvert (F-042) | | | |
| BDD-07 | | Sauvegarde et restauration | ouvert (non traité, duel arrêté) | | | |
| BDD-08 | | Données personnelles, suppression de compte | ouvert (F-038) | | | |
| DEP-01 | | Serveur WSGI et proxy HTTPS | ouvert (non traité, duel arrêté) | | | |
| DEP-02 | | Configuration et secrets | ouvert (non traité, duel arrêté) | | | |
| DEP-03 | | Durcissement du système | ouvert (non traité, duel arrêté) | | | |
| DEP-04 | | Conteneur | ouvert (non traité, duel arrêté) | | | |
| DEP-05 | | Intégration continue | ouvert (non traité, duel arrêté) | | | |
| DEP-06 | | Exploitation | ouvert (non traité, duel arrêté) | | | |

## Fiches ouvertes pendant le duel

| ID | Gravité | Titre | Statut | Agent | Test | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| F-014 | Basse | Un admin peut se rétrograder lui-même | ferme | attaquant | test_sec_02_controle_acces.AccesAdministrationTests | 7ec912a |
| F-015 | Basse | Mots de passe faibles acceptés | ouvert (correction partielle, contournée) | attaquant | test_sec_03_authentification.PolitiqueDeMotDePasseTests | 7ec912a |
| F-016 | Moyenne | Session non invalidée au changement de mot de passe, durée 31 j | ferme | attaquant | test_sec_03_authentification.SessionsTests | 7ec912a |
| F-017 | Moyenne | Docx de 45 Ko qui bloque le traitement plus de 60 s | ferme | attaquant | test_sec_06_fichiers.CoutDeTraitementTests | 7ec912a |
| F-018 | Basse | HTML renommé accepté, /documents sans nosniff | ferme | attaquant | test_sec_06_fichiers.TypeParLeContenuTests | 7ec912a |
| F-019 | Basse | Pas de quota de 200 Mo par utilisateur | ouvert (correction partielle, contournée) | attaquant | test_sec_06_fichiers.QuotaTests | 7ec912a |
| F-020 | Moyenne | Récurrence .ics depuis l'an 1 : 45 s de calcul | ferme | attaquant | test_sec_06_fichiers.ImportIcsTests | 7ec912a |
| F-021 | Basse | Règles .ics invalides : erreur 500 | ouvert (correction partielle, contournée) | attaquant | test_sec_06_fichiers.ImportIcsTests | 7ec912a |
| F-022 | Basse | Photos sans limite de taille ni de nombre | ferme | attaquant | test_sec_06_fichiers.PhotosTests | 7ec912a |
| F-023 | Basse | Nom de fichier long : erreur 500 | ferme | attaquant | test_sec_06_fichiers.NomsDeFichiersTests | 7ec912a |
| F-024 | Basse | .ics de plus de 2 Mo et plus de 5 000 événements acceptés | ferme | attaquant | test_sec_06_fichiers.ImportIcsTests | 7ec912a |
| F-025 | Moyenne | Redirection ouverte via next (tabulation, antislash) | ferme | attaquant | test_sec_04_csrf_redirections.RedirectionsTests | 7ec912a |
| F-026 | Basse | next avec CRLF : erreur 500 | ferme | attaquant | test_sec_04_csrf_redirections.RedirectionsTests | 7ec912a |
| F-027 | Basse | Recherche admin de 100 000 caractères : erreur 500 | ferme | attaquant | test_sec_01_injections.InjectionsTests | 7ec912a |
| F-028 | Moyenne | Contournement F-01 : corps au compte-gouttes, safe_http sans délai total | ferme | attaquant | test_sec_07_ssrf.VariantesSsrfTests | 7ec912a |
| F-029 | Basse | Contournement F-015 : séparateurs dans un mot de passe courant | ouvert (correction partielle, contournée) | attaquant | test_sec_03_authentification.VariantesMotsDePasseTests | 7ec912a |
| F-030 | Basse | Contournement F-03 : rafale parallèle, 20 essais au lieu de 5 | ferme | attaquant | test_sec_03_authentification.VariantesLimiteTests | 7ec912a |
| F-031 | Basse | Exigence manquante : filtre de mots interdits (F-12) | corrigé, non contre-vérifié | attaquant | test_sec_05_moderation | 7ec912a |
| F-032 | Basse | requirements.txt sans versions épinglées | corrigé, non contre-vérifié | attaquant | test_sec_09_entetes.DependancesTests | 7ec912a |
| F-033 | Basse | Réponse du LLM non validée (500, champs de 1 Mo) | corrigé, non contre-vérifié | attaquant | test_sec_08_llm.ValidationReponseLlmTests | 7ec912a |
| F-034 | Basse | Contournement F-019 : envois simultanés dépassent le quota | ouvert (non corrigé) | attaquant | test_sec_06_fichiers.VariantesQuotaTests | |
| F-035 | Basse | Contournement F-021 : date UTC en l'an 1 ou 9999, erreur 500 | ouvert (non corrigé) | attaquant | test_sec_06_fichiers.VariantesIcsTests | |
| F-036 | Basse | Contournement F-029 : symboles * / + # dans un mot de passe courant | ouvert (non corrigé) | attaquant | test_sec_03_authentification.VariantesCorrectionsManche2Tests | |
| F-037 | Basse | Fichier orphelin dans uploads après une exception pendant un dépôt | ouvert (non corrigé) | attaquant | test_bdd_04_cascades.TransactionTests | |
| F-038 | Basse | Exigence manquante : suppression de compte par l'utilisateur (BDD-08) | ouvert (non corrigé) | attaquant | test_bdd_04_cascades.SuppressionParLUtilisateurTests | |
| F-039 | Basse | Aucune contrainte CHECK en base (BDD-01) | ouvert (non corrigé) | attaquant | test_bdd_01_contraintes.ContraintesTests | |
| F-040 | Basse | Pas de WAL ni busy_timeout : « database is locked » sous charge (BDD-03) | ouvert (non corrigé) | attaquant | test_bdd_03_concurrence.ConcurrenceTests | |
| F-041 | Basse | auth_attempts non effacé à la suppression d'un compte (BDD-04) | ouvert (non corrigé) | attaquant | test_bdd_04_cascades.SuppressionParAdminTests | |
| F-042 | Basse | Valeurs extrêmes : 500 et événement en 9999 écrit (BDD-06) | ouvert (non corrigé) | attaquant | test_bdd_06_extremes.ValeursExtremesTests | |
| F-043 | Basse | Exigence manquante : journal structuré et audit_log (SEC-10) | ouvert (non corrigé) | attaquant | test_sec_10_journal.JournalTests | |

## État final (2026-11-28, duel arrêté par l'humain après la manche 4)

- Fermées : 19 · corrigées non contre-vérifiées : 6 · ouvertes (contournées) : 4 · ouvertes non corrigées : 10.
- Suite complète : 345 tests, 52 échecs (tous dans les tests des fiches ouvertes F-034 à F-043), 1 ignoré.
- Commits : 861e47e (tests et fiches), 7ec912a (corrections).
