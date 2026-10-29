---
name: cloturer-etape
description: Clore une étape du plan : vérification complète (ruff, pytest, essai réel), mise à jour de README et PLAN, branche, commit et PR. À utiliser quand une étape de PLAN.md est terminée ou que l'utilisateur dit « c'est fini », « on clôt l'étape ».
---

# Clore une étape

1. **Vérifier** : `.\.venv\Scripts\python.exe .claude/scripts/verifier.py`. Tout doit être vert,
   y compris l'essai réel. Si le warehouse est absent ou périmé, proposer un `refresh` (deux
   minutes environ) plutôt que sauter l'essai. Ajouter un essai réel propre à l'étape, sur un
   projet de 1 à 50 MW (12 MW et 70 000 MWh à Chilly-Mazarin par défaut), et en citer la sortie.
2. **PLAN.md** : ligne dans le tableau « État », étape retirée de « Suite », décision datée dans
   « Décisions en vigueur » si un choix a été fait, « À surveiller » si une source a une limite
   nouvelle. Date du jour en tête du fichier.
3. **README.md** : commandes, sources et licences, nombre de tests si la suite a changé.
4. **Git** : branche depuis `main` à jour, commit en français (titre court, corps qui dit l'étape
   et le pourquoi), push, PR vers `main`. Ne jamais pousser sur `main`.
5. Rapporter : ce qui est vérifié, ce qui ne l'est pas, le lien de la PR.
