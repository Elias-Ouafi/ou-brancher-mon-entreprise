# Plan du projet

> Mis à jour le 23 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en ligne de
> commande, en local et à la demande.

## État

Le MVP est atteint depuis le 16 octobre. `essentiel` donne en 1,5 s environ le prix moyen et le
verdict carbone ; il répond juste sur 11 adresses de recette, de Paris à Mamoudzou (test
`test_recette_mvp.py`).

| Brique | Où |
|---|---|
| Ingestion PySpark (typage, UTC, clés du lot), écriture Arrow vers le bronze | `ingestion/transform.py`, `ingestion/spark.py` |
| Adresse → commune, département, région | `ingestion/geocode.py` (BAN) |
| Prix par tranche Eurostat, scénarios à 3 ans calibrés sur les erreurs passées | `fct_prix_entreprise_tranche` |
| Carbone par système électrique, seuil de 100 gCO₂e/kWh | `fct_carbone_territoire` |
| Part de la facture dans le chiffre d'affaires (`--ca`) | `essentiel`, `diagnose` |
| Diagnostic complet : industrie, réseau, zone RTE, site clé en main, risques | `diagnose`, `compare` |
| Carte 3D hexagonale | `carte` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |

**À surveiller** : les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 ; le
calendrier Tempo n'a tourné que sur des réponses simulées (pas de compte RTE).

## Suite

| Priorité | Étape | Bloqué par |
|---|---|---|
| 1 | Protéger `main` : CI et PR obligatoires | réglage GitHub, par le propriétaire |
| 2 | Pied de page des rapports : commande lancée et date des données | — |
| 3 | Carbone horaire en Corse et outre-mer (open data EDF SEI) | — |
| 4 | Vraie courbe de charge (`--courbe`, export Enedis) à la place du profil type | — |
| 5 | Part de la facture dans la valeur ajoutée (`--va`), critère du statut d'électro-intensif | — |
| 6 | Extension internationale, voir ci-dessous | licences Ember et contours à vérifier |
| 7 | Calendrier Tempo sur données réelles | compte RTE |
| 8 | Repère de prix au-delà de 150 000 MWh/an (contrats CAPN) ; classement pondéré dans `compare` | décision |

**Extension internationale**, dans cet ordre :
1. UE-27 à la maille pays : prix Eurostat (déjà ingéré, retirer le filtre France) et carbone
   Ember (CC BY 4.0) ; Nominatim pour trouver le pays.
2. Zones de prix (Italie, pays nordiques) : ENTSO-E et point-dans-polygone avec l'extension
   `spatial` de DuckDB.
3. Royaume-Uni (intensité régionale officielle) et États-Unis (EIA, eGRID), où prix et carbone
   dépendent vraiment de l'adresse.

## Ce que les études ont établi (9 et 15 octobre)

- **Les besoins des entreprises**, dans l'ordre : de la puissance disponible à une date connue
  (premier critère des gros projets ; zones RTE P1 dès fin 2028, P2 dès 2029) ; du foncier
  viabilisé ; une électricité bas-carbone ; un prix prévisible, qui ne décide que pour les
  électro-intensifs et les data centers ; la connaissance des risques du site.
- **En France, ni le prix ni le carbone ne dépendent de l'adresse** : d'où un diagnostic de
  contexte par adresse plutôt qu'un « prix à l'adresse ».
- **Limites de l'open data** : capacité de raccordement non publiée ; postes RTE sans
  coordonnées (maille du département seulement) ; ~40 % des IRIS sous secret statistique
  depuis 2022.

## Décisions en vigueur

| Date | Décision |
|---|---|
| 12 oct. | Prix historiques par tranche Eurostat, pas de prix horaire ; scénarios à 3 ans |
| 15 oct. | Comparaison sans classement |
| 16 oct. | MVP recentré sur deux réponses, prix moyen et verdict carbone ; sans `--conso`, tranche IA |
| 16 oct. | Verdict sur le facteur ADEME en cycle de vie, au seuil de 100 gCO₂e/kWh de la taxonomie |
| 16 oct. | Local et à la demande uniquement : aucune exécution planifiée |
| 18 oct. | Commentaires limités à la logique (le pourquoi) ; documentation en français ; licence MIT |
| 18 oct. | Sécurité : CI en lecture seule, actions figées par commit, HTTPS seulement, identifiants dans `identifiants.local.txt` (ignoré par git) |
| 18 oct. | Carte : page HTML autonome, three.js embarqué, grille hexagonale maison plutôt que H3 |
| 23 oct. | Outils Python et PySpark privilégiés : ingestion en PySpark (Java 17 requis, en local et en CI), silver et gold en dbt-duckdb |
| 23 oct. | `--ca` sans seuil : le statut d'électro-intensif se juge sur la valeur ajoutée ; `--ca` exige `--conso` |
| 23 oct. | Documentation réduite à deux fichiers : README et ce plan |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Spark pour silver et gold | dbt-duckdb suffit à ce volume |
| Site web, exécution planifiée, Airflow | Hors périmètre : ligne de commande, en local |
