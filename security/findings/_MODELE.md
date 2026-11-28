# F-XXX — <titre court>

- **Gravité** : Critique | Haute | Moyenne | Basse
- **Exigence** : SEC-xx / BUG-xx / BDD-xx / DEP-xx
- **Trouvée par** : attaquant, manche N, le AAAA-MM-JJ
- **Route / fichier** : `<MÉTHODE> <URL>` — `<chemin>:<ligne>`
- **Statut** : reproduit | en_correction | a_verifier | ferme | rejete

## Reproduction

1. ...
2. ...

Charge utilisée : `...`

Résultat observé : ...

Résultat attendu : ...

## Test qui reproduit

`backend/tests/security/test_sec_xx_<nom>.py::<Classe>::<test>` (échoue avant la correction)

## Correction (rempli par le défenseur)

- Fichiers modifiés : ...
- Principe : ...

## Vérification (rempli par l'attaquant puis le manager)

- Test d'origine vert et inchangé : oui / non
- Attaque rejouée : oui / non
- Variantes essayées : ...
- Suite complète verte (nombre de tests) : ...
- Commit : ...
