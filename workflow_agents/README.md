# Workflow : 3 agents en parallèle sur Rivezli

Trois agents Claude Code développent chacun un **plugin indépendant** de l'app, en même temps, sans se marcher dessus :

| Agent | Plugin | Brief |
|---|---|---|
| Forum | `backend/plugins/forum/` | `agent_1_forum.md` |
| Statistiques | `backend/plugins/stats/` | `agent_2_stats.md` |
| Focus | `backend/plugins/focus/` | `agent_3_focus.md` |

Règle commune : chaque agent ne touche **que** son dossier de plugin et son fichier `backend/tests/test_<nom>.py`.
Personne ne modifie `backend/config.py` : on ajoute les 3 lignes `FEATURES` au moment de la fusion.

Prérequis : Git installé (`git --version`) et Claude Code (`claude`).

---

## Option A : un seul terminal, Claude orchestre les 3 sous-agents

Depuis la racine du projet :

```powershell
claude "$(Get-Content workflow_agents\orchestrateur.md -Raw)"
```

Claude lance alors 3 sous-agents en parallèle, chacun dans son propre worktree git, puis fait le bilan.

## Option B : trois terminaux, un agent par terminal (plus de contrôle)

1. Créer une copie de travail (worktree) et une branche par agent, depuis la racine du projet :

```powershell
git worktree add ..\rivezli-forum -b plugin-forum
git worktree add ..\rivezli-stats -b plugin-stats
git worktree add ..\rivezli-focus -b plugin-focus
```

2. Dans trois terminaux séparés (copier aussi le dossier `workflow_agents` ou donner le chemin absolu) :

```powershell
cd ..\rivezli-forum; claude "$(Get-Content ..\night_shift_hackathon2026\workflow_agents\agent_1_forum.md -Raw)"
```

```powershell
cd ..\rivezli-stats; claude "$(Get-Content ..\night_shift_hackathon2026\workflow_agents\agent_2_stats.md -Raw)"
```

```powershell
cd ..\rivezli-focus; claude "$(Get-Content ..\night_shift_hackathon2026\workflow_agents\agent_3_focus.md -Raw)"
```

Pense à copier le fichier `.env` dans chaque worktree si les agents doivent tester les fonctions IA (il est ignoré par git).

## Fusion à la fin

```powershell
git checkout fouad
git merge plugin-forum
git merge plugin-stats
git merge plugin-focus
```

Puis ajouter dans `FEATURES` de `backend/config.py` :

```python
    "backend.plugins.forum",  # forum d'entraide par matière
    "backend.plugins.stats",  # statistiques et progression
    "backend.plugins.focus",  # mode focus : minuteur lié au planning
```

Lancer les tests et l'app :

```powershell
python -m unittest discover -s backend/tests -t .
python run.py
```

Nettoyer les worktrees :

```powershell
git worktree remove ..\rivezli-forum; git worktree remove ..\rivezli-stats; git worktree remove ..\rivezli-focus
```
