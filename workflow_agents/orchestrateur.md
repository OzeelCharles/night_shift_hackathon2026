Tu es l'orchestrateur d'un workflow de 3 agents qui travaillent en parallèle sur l'app Rivezli (ce dépôt).

1. Lis `workflow_agents/agent_1_forum.md`, `workflow_agents/agent_2_stats.md` et `workflow_agents/agent_3_focus.md`.
2. Lance les 3 sous-agents EN MÊME TEMPS (trois appels à l'outil Agent dans un seul message), chacun avec `isolation: "worktree"` pour qu'il ait sa propre copie du dépôt et sa propre branche. Donne à chacun, comme consigne, le contenu complet de son fichier.
3. Quand ils ont tous fini, vérifie leur travail : relis les fichiers créés, contrôle qu'aucun n'a touché à `backend/config.py` ni à des fichiers hors de son plugin et de son test, et note le nom de chaque branche.
4. Fais-moi un bilan en français : pour chaque agent, ce qui a été fait, la branche, les tests (passés ou non), les problèmes signalés.
5. Ne fusionne rien et ne pousse rien sans mon accord. Si je dis oui : fusionne les 3 branches dans la branche courante, ajoute les 3 lignes dans `FEATURES` de `backend/config.py`, puis lance `python -m unittest discover -s backend/tests -t .`.
