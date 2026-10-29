---
name: essai-carte
description: Générer la carte 3D pour une adresse et la vérifier dans le navigateur intégré (rendu, console, deux vues). À utiliser après toute modification de reporting/carte.py, reporting/carte.html, ingestion/hexagones.py ou des modèles gold qu'affiche la carte.
---

# Essayer la carte

1. Générer sans ouvrir le navigateur du poste :
   `.\.venv\Scripts\python.exe -m ingestion.cli carte --adresse "3 allée des peupliers 91380 Chilly-Mazarin" --puissance 12 --sans-ouvrir`
   (`--reconstruire` si le gabarit ou les données ont changé et que la carte n'est pas refaite).
2. Servir `data/reports` : `preview_start` avec la configuration `carte` de
   `.claude/launch.json`, puis ouvrir `/carte.html`.
3. Contrôler : aucune erreur dans la console, la vue « Raccorder » (touche 1) s'ouvre sur la carte
   de l'adresse avec sa fiche « Brancher ici ? », la vue « Territoire » (touche 2) en couleurs
   réelles, les boutons de système électrique en bas à droite.
4. Capture d'écran comme preuve ; signaler tout écart avec les décisions « Carte » de PLAN.md.
