# Recette du MVP : 11 adresses, prix et carbone

> Exécutée le 16 octobre 2026 avec `essentiel --adresse "…" --format json`, sur la base
> rafraîchie le même jour (`refresh`). Sans `--conso` : tranche IA (moins de 20 MWh par an).
> Le test `ingestion/tests/test_recette_mvp.py` fige les mêmes verdicts, sans réseau.

| Adresse saisie | Résolue (BAN) | Commune | Prix moyen HTVA | Décarbonée | gCO₂e/kWh (cycle de vie) |
|---|---|---|---|---|---|
| Place de l'Hôtel de Ville, 75004 Paris | rue | Paris (75056) | 0,2607 €/kWh | oui | 51,9 (France continentale) |
| 20 rue de la République, 69002 Lyon | numéro | Lyon (69123) | 0,2607 €/kWh | oui | 51,9 (France continentale) |
| Place du Général de Gaulle, 59000 Lille | rue | Lille (59350) | 0,2607 €/kWh | oui | 51,9 (France continentale) |
| Quai du Port, 13002 Marseille | rue | Marseille (13055) | 0,2607 €/kWh | oui | 51,9 (France continentale) |
| 1 cours Napoléon, 20000 Ajaccio | numéro | Ajaccio (2A004) | 0,2607 €/kWh | **non** | 594 (Corse) |
| Boulevard Paoli, 20200 Bastia | rue | Bastia (2B033) | 0,2607 €/kWh | **non** | 594 (Corse) |
| Place de la Victoire, 97110 Pointe-à-Pitre | rue | Pointe-à-Pitre (97120) | 0,2607 €/kWh | **non** | 702 (Guadeloupe) |
| Rue Victor Hugo, 97200 Fort-de-France | rue | Fort-de-France (97209) | 0,2607 €/kWh | **non** | 840 (Martinique) |
| Place des Palmistes, 97300 Cayenne | rue | Cayenne (97302) | 0,2607 €/kWh | **non** | 957 (Guyane) |
| Rue de Paris, 97400 Saint-Denis | rue | Saint-Denis (97411) | 0,2607 €/kWh | **non** | 780 (La Réunion) |
| Place Mariage, 97600 Mamoudzou | rue (voie voisine) | Mamoudzou (97611) | 0,2607 €/kWh | **non** | 780 (Mayotte) |

**Ce que montre la recette**

- Le prix est le même partout : il dépend de la consommation, pas de l'adresse (péréquation).
- Le verdict carbone ne change qu'entre systèmes électriques : oui sur tout le continent, non en
  Corse et dans chaque territoire d'outre-mer, dont l'électricité reste surtout thermique.
- Une adresse inconnue (`zzzz qqqq`) renvoie « adresse introuvable » et le code de sortie 1,
  sans trace d'erreur Python.
- Chaque réponse arrive en environ 1,5 s, géocodage compris.

**Sortie texte** (`essentiel --adresse "1 cours Napoléon, 20000 Ajaccio" --conso 60000`) :

```
1 Cours Napoléon 20000 Ajaccio
Commune Ajaccio (2A004) · Corse-du-Sud (2A) · Corse (94)

Prix moyen : 0,0916 €/kWh HTVA, soit environ 5 500 000 € par an pour 60 000 MWh (tranche IE ; Eurostat, S2 2025 – S1 2026)
Électricité décarbonée : non (Corse), 594 gCO₂e/kWh en cycle de vie, au-dessus du seuil de 100 gCO₂e/kWh
À savoir : Le prix ne dépend pas de l'adresse mais de la consommation (péréquation, Corse et outre-mer compris). Le carbone est la moyenne annuelle du réseau de ce territoire, pas celle d'un contrat de fourniture.
```
