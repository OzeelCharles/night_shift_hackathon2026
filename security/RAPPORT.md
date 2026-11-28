# Rivezli — Rapport final du duel de sécurité

Rédigé par le manager le 2026-11-28. Branche `securite/duel`, rien n'a été poussé.
Commits : `861e47e` (tests d'attaque et fiches), `7ec912a` (corrections), suivi de ce rapport.

## 1. Résumé

1. **Manches** : 4 manches jouées sur 6. L'humain a demandé l'arrêt après la manche 4, puis un arrêt immédiat avec commit des corrections.
2. **Failles** : 39 fiches validées, dont 0 Critique, 1 Haute, 11 Moyennes et 27 Basses. 19 sont fermées (corrigées et contre-vérifiées), 6 sont corrigées mais pas contre-vérifiées et 14 restent ouvertes : 4 corrections ont été contournées et 10 fiches n'ont pas été corrigées.
3. **Score final** : attaquant 57, défenseur 16.
4. **Tests** : 345 tests. Les 52 échecs viennent tous des tests des fiches ouvertes, F-034 à F-043. La suite d'origine (164 tests) et le test d'architecture restent verts.
5. **Verdict : pas prêt pour la mise en ligne.** Toute la partie déploiement (DEP-01 à DEP-06) reste à faire, avec la journalisation (SEC-10), la concurrence en base (BDD-03, erreurs « database is locked » sous charge), les contraintes de base et la suppression de compte. Les failles graves trouvées (SSRF, force brute, énumération, redirection ouverte, bombe zip, déni de service par fichier) sont en revanche corrigées et vérifiées.

## 2. Tableau des failles

Statuts : **fermée** = corrigée, test vert, attaque et variantes rejouées par l'attaquant, contrôlée par le manager ;
**non vérifiée** = corrigée et test vert, mais l'attaquant ne l'a pas rejouée (arrêt du duel) ;
**ouverte** = non corrigée, ou correction contournée.

| ID | Gravité | Titre | Statut | Commit |
| --- | --- | --- | --- | --- |
| F-01 | Haute | SSRF par le lien d'une offre de stage (lecture complète de la page interne) | fermée | 7ec912a |
| F-028 | Moyenne | Contournement de F-01 : réponse au compte-gouttes, sans délai total | fermée | 7ec912a |
| F-02 | Moyenne | Rebinding DNS dans `pdf_text` | fermée | 7ec912a |
| F-03 | Moyenne | Aucune limite de tentatives (connexion, inscription) | fermée | 7ec912a |
| F-030 | Basse | Contournement de F-03 : rafale parallèle, 20 essais au lieu de 5 | fermée | 7ec912a |
| F-04 | Moyenne | Énumération de comptes (messages, temps de réponse) | fermée | 7ec912a |
| F-016 | Moyenne | Session valide après changement de mot de passe, durée de 31 jours | fermée | 7ec912a |
| F-08 | Moyenne | Bombe zip Office/ODF | fermée | 7ec912a |
| F-017 | Moyenne | Docx de 45 Ko qui bloque le serveur plus de 60 s | fermée | 7ec912a |
| F-020 | Moyenne | .ics de 5 Ko : 45 s de calcul (récurrence depuis l'an 1) | fermée | 7ec912a |
| F-025 | Moyenne | Redirection ouverte via `next` (tabulation, antislash) | fermée | 7ec912a |
| F-11 | Basse | 302 « succès » au lieu de 404 sur les suppressions des données d'autrui | fermée | 7ec912a |
| F-014 | Basse | Un admin peut se rétrograder lui-même | fermée | 7ec912a |
| F-018 | Basse | HTML renommé accepté, pas de `nosniff` | fermée | 7ec912a |
| F-022 | Basse | Photos sans limite de taille ni de nombre | fermée | 7ec912a |
| F-023 | Basse | Nom de fichier long : erreur 500 | fermée | 7ec912a |
| F-024 | Basse | .ics de plus de 2 Mo ou de plus de 5 000 événements accepté | fermée | 7ec912a |
| F-026 | Basse | `next` avec CRLF : erreur 500 | fermée | 7ec912a |
| F-027 | Basse | Recherche admin de 100 000 caractères : erreur 500 | fermée | 7ec912a |
| F-05 | Moyenne | Aucun en-tête de sécurité (CSP, X-Frame-Options…) | non vérifiée | 7ec912a |
| F-09 | Moyenne | Pas de quota LLM (coût) | non vérifiée | 7ec912a |
| F-033 | Basse | Réponse du LLM non validée (500, champs de 1 Mo) | non vérifiée | 7ec912a |
| F-031 | Basse | Filtre de mots interdits absent (F-12) | non vérifiée | 7ec912a |
| F-10 | Basse | KaTeX sans SRI | non vérifiée | 7ec912a |
| F-032 | Basse | Versions non épinglées | non vérifiée | 7ec912a |
| F-015 | Basse | Mots de passe faibles acceptés | ouverte (contournée par F-029 puis F-036) | 7ec912a (partiel) |
| F-029 | Basse | Contournement de F-015 : séparateurs | ouverte (contournée par F-036) | 7ec912a (partiel) |
| F-036 | Basse | Contournement de F-029 : symboles `* / + #` | ouverte | — |
| F-019 | Basse | Pas de quota de stockage | ouverte (contournée par F-034) | 7ec912a (partiel) |
| F-034 | Basse | Contournement de F-019 : envois simultanés | ouverte | — |
| F-021 | Basse | Règles .ics invalides : erreur 500 | ouverte (contournée par F-035) | 7ec912a (partiel) |
| F-035 | Basse | Contournement de F-021 : date UTC en l'an 1 ou 9999 | ouverte | — |
| F-037 | Basse | Fichier orphelin après une exception pendant un dépôt | ouverte | — |
| F-038 | Basse | Pas de suppression de compte par l'utilisateur (BDD-08) | ouverte | — |
| F-039 | Basse | Aucune contrainte CHECK en base (BDD-01) | ouverte | — |
| F-040 | Basse | Pas de WAL ni de busy_timeout : « database is locked » sous charge | ouverte | — |
| F-041 | Basse | `auth_attempts` non effacé à la suppression d'un compte | ouverte | — |
| F-042 | Basse | Valeurs extrêmes : 500, événement en l'an 9999 enregistré | ouverte | — |
| F-043 | Basse | Pas de journal structuré ni d'`audit_log` (SEC-10) | ouverte | — |

Constats de l'audit initial non attaqués : **F-06** (serveur de développement, débogueur), **F-07** (mot de passe admin
affiché dans le terminal). **F-13** est conforme : l'analyse statique du SQL est verte.

Conforme dès le départ, avec des tests de non-régression ajoutés : aucune injection SQL ; jeton CSRF exigé sur toutes
les routes POST ; aucune route GET qui modifie la base ; aucune XSS (environ 40 champs, 25 pages, contenu LLM compris) ;
isolation des données entre utilisateurs ; 403 sur `/admin` pour un non-admin ; dernier admin protégé ; traversée de
chemin bloquée ; PDF de 10 000 pages, chiffré ou corrompu sans erreur ; `foreign_keys` actif ; transactions annulées
sur erreur.

## 3. Risques résiduels

**Ouverts, à corriger avant la mise en ligne (par priorité)**

1. **F-040, concurrence SQLite** : avec plusieurs workers, des erreurs 500 « database is locked » sont probables. Il faut le mode WAL et un `busy_timeout` (BDD-03).
2. **F-043, aucun journal** : sans journal des connexions, des blocages et des actions admin, une attaque passe inaperçue (SEC-10).
3. **F-038 / F-041, données personnelles** : l'utilisateur ne peut pas supprimer son compte lui-même, et une trace hachée de son e-mail reste après la suppression. C'est un point RGPD (BDD-08).
4. **F-039 / F-042, cohérence des données** : la base accepte des valeurs incohérentes si la validation Python est contournée, et des valeurs extrêmes provoquent des erreurs 500.
5. **F-034 / F-037, quota de stockage** : le quota se dépasse par envois simultanés, et un fichier orphelin compte dans le quota.
6. **F-035, F-036** : erreur 500 sur des dates .ics extrêmes ; quelques variantes de mots de passe courants sont acceptées.

**Corrigés mais non contre-vérifiés** : F-05, F-09, F-10, F-031, F-032, F-033. Leurs tests passent, mais l'attaquant n'a
pas cherché de contournement. Il faudra les rejouer à la reprise. Les empreintes SRI de KaTeX (F-10) ont été écrites
sans accès réseau : si l'une est fausse, les formules s'affichent en LaTeX brut. À vérifier avec
`curl -s <url> | openssl dgst -sha384 -binary | openssl base64 -A`.

**Acceptés, sans fiche**

- **Inscription sans confirmation par e-mail** : elle révèle si une adresse est libre. La limite de 5 inscriptions par IP et par heure réduit ce sondage, sans le supprimer.
- **Déconnexion** : elle n'invalide pas un cookie déjà copié, qui reste valide 7 jours au plus. Le cahier ne l'exige pas.
- **Plancher de 0,5 s sur un échec de connexion** : il égalise les temps de réponse. Il s'applique après la limite par IP, donc environ 2,5 s de worker par IP et par quart d'heure. Une attaque distribuée pourrait occuper les workers. Ce point n'a pas été testé.
- **Tests de concurrence bornés** à 20 fils pendant 10 s, au lieu des 60 s du cahier.

**Non traités** (arrêt du duel) : signalement de bug (BUG-01 à BUG-06), migrations sûres (BDD-02), volume (BDD-05),
sauvegarde et restauration (BDD-07), déploiement (DEP-01 à DEP-06), F-06, F-07.

## 4. Checklist de mise en ligne (section 7 du cahier)

- [ ] Tous les tests et analyses de DEP-05 verts sur la branche principale. **Non** : 52 tests de sécurité échouent (fiches ouvertes), et la CI (bandit, pip-audit, gitleaks) n'existe pas.
- [ ] `RIVEZLI_ENV=production`, debug désactivé, `SECRET_KEY` de production. **Partiel** : `RIVEZLI_ENV` active déjà le cookie `Secure` et HSTS, mais les contrôles au démarrage (DEP-02) et l'interdiction de `FLASK_DEBUG` (F-06) manquent.
- [ ] Compte admin créé hors du terminal, `admin@revision.local` absent. **Non** : F-07 n'est pas traité.
- [ ] HTTPS actif, HSTS présent, note TLS A. **À faire par un humain** (hébergeur et proxy).
- [ ] En-têtes de SEC-09 présents en production (`curl -I`). **Code prêt** (F-05) ; vérification à faire après déploiement.
- [ ] Sauvegarde exécutée et restauration testée. **Non** : BDD-07 n'est pas traité.
- [x] Aucune clé, aucune base, aucun fichier `instance/` dans git. **Oui** pour la branche : `.gitignore` couvre `instance/`, `.env` et les clés, et les commits du duel ont été contrôlés. L'image Docker (DEP-04) n'existe pas.
- [ ] Rapport final relu par un humain. **À faire.**

## 5. Décisions à prendre par l'humain

1. **Reprendre le duel ?** Il reste à contre-vérifier la manche 3, à corriger F-034 à F-043, puis à traiter BUG, BDD-02/05/07 et DEP. Les tests des fiches ouvertes sont déjà écrits et échouent : ils servent de liste de travail. À la reprise, relancer le manager en lui donnant `security/BOARD.md` et ce rapport.
2. **Hébergeur cible** (VPS, Render, Railway, Fly.io…) : ce choix fixe DEP-01, DEP-03 et DEP-04.
3. **Budget IA** : le quota est réglé à 20 générations par jour et par utilisateur (`LLM_DAILY_QUOTA` dans `config.py`). Les fonctions IA restent-elles actives en production, avec quel budget mensuel ?
4. **Dépendances** (questions du défenseur) :
   - retirer `python-docx`, `python-pptx` et `odfpy`, que l'application n'importe plus depuis F-08/F-017 ?
   - déclarer `Pillow`, importé de façon facultative par `dao/vision.py` ?
   - lancer `pip-audit`, qui demande le réseau ?
5. **Commits groupés** : les corrections sont dans un seul commit (`7ec912a`) et non pas un commit par fiche, car plusieurs fiches modifient les mêmes fichiers. Sur décision humaine, ce commit contient aussi les 6 corrections non contre-vérifiées. Si vous voulez un historique fiche par fiche, il faut le reconstruire à la main avant la pull request.
6. **Fichiers non commités** : `LANCEMENT_DUEL.md` et `scripts/serveur_duel.py` sont à vous et sont laissés hors de git. Faut-il les ajouter ?
7. **Pull request** : c'est à vous de pousser `securite/duel` et d'ouvrir la pull request (`git push -u origin securite/duel`) après relecture.
