# Lancer le duel de sécurité (manager, attaquant, défenseur)

Le duel tourne dans **Claude Code** avec la fonction « agent teams » (expérimentale) :
le **manager** est la session principale ; il crée l'**attaquant** et le **défenseur** comme coéquipiers,
organise les manches, valide les failles et les corrections, fait les commits et t'écrit le compte rendu.

## 1. Préparer une copie dédiée au duel

Ne lance pas le duel dans ton dossier de travail habituel : utilise une copie neuve, sans vraie base ni clé d'API.

```powershell
git clone https://github.com/OzeelCharles/night_shift_hackathon2026 rivezli-duel
cd rivezli-duel
git switch -c securite/duel
pip install -r requirements.txt
```

## 2. Ajouter le kit

Décompresse `kit-duel.zip` **à la racine** de `rivezli-duel`. Tu dois obtenir :

```
rivezli-duel/
  CLAUDE.md                         règles communes aux 3 agents
  LANCEMENT_DUEL.md                 ce fichier
  .claude/settings.json             active les agent teams + permissions (push interdit, secrets illisibles)
  .claude/agents/manager.md         le manager (chef d'équipe)
  .claude/agents/attaquant.md       l'attaquant
  .claude/agents/defenseur.md       le défenseur
  scripts/serveur_duel.py           serveur jetable sur http://127.0.0.1:5001 (base temporaire, sans internet ni IA)
  security/CAHIER_DES_CHARGES.md    le cahier des charges
  security/BOARD.md                 tableau de bord et score
  security/LOG.md                   journal des décisions
  security/findings/_MODELE.md      modèle de fiche de faille
```

Vérifie que tout marche avant de lancer les agents :

```powershell
python -m unittest discover -s backend/tests -t .
python scripts/serveur_duel.py      # puis ouvre http://127.0.0.1:5001, connecte-toi avec alice@duel.local / AliceDuel-2026!, Ctrl+C
```

## 3. Lancer le duel

```powershell
claude --agent manager
```

- Au premier lancement, Claude Code demande si tu fais confiance au dossier : réponds oui (sinon les rôles
  de `.claude/agents/` ne sont pas appliqués).
- Le manager démarre seul (manche 0), puis crée l'équipe `attaquant` + `defenseur`.
- Pour moins de demandes d'autorisation, tu peux lancer `claude --agent manager --permission-mode acceptEdits`
  (les modifications de fichiers sont acceptées, les commandes restent soumises aux règles de `settings.json`).

## 4. Suivre et intervenir

- Les coéquipiers apparaissent sous la zone de saisie. **Flèches haut/bas** pour en choisir un, **Entrée** pour voir
  ce qu'il fait et lui écrire directement, **Échap** pour revenir. **Ctrl+T** affiche la liste des tâches.
- Les demandes d'autorisation des coéquipiers s'affichent dans la session du manager : c'est là que tu les acceptes.
- Le manager s'arrête et te pose une question pour toute faille critique, suppression de données, migration
  irréversible ou désaccord persistant (aussi noté `QUESTION` dans `security/LOG.md`).
- Si le manager se met à corriger lui-même : « Attends que tes coéquipiers terminent leurs tâches. »
- Si une tâche reste bloquée : demande au manager de relancer le coéquipier concerné.

Sous Windows Terminal ou dans le terminal de VS Code, l'affichage en panneaux séparés n'est pas disponible :
tout se passe dans une seule fenêtre (mode par défaut), c'est normal.

## 5. À la fin

1. Lis `security/RAPPORT.md` (résumé, failles, risques restants, checklist de mise en ligne, décisions à prendre).
2. Relis les commits : `git log --oneline securite/duel`.
3. C'est **toi** qui pousses et ouvres la pull request : `git push -u origin securite/duel`
   (les agents n'ont pas le droit de pousser).

## Bon à savoir

- Les agent teams sont expérimentales : une session reprise avec `/resume` ne récupère pas les coéquipiers ;
  dans ce cas, demande au manager de les recréer (le tableau de bord et les fiches sont dans les fichiers,
  rien n'est perdu).
- Trois agents consomment environ trois fois plus de quota qu'une session seule. Le duel s'arrête au plus tard
  après 6 manches.
- Documentation : https://code.claude.com/docs/en/agent-teams et https://code.claude.com/docs/en/sub-agents
