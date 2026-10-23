# Feuille de route

> Mise à jour le 23 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en local
> et à la demande. Après le MVP : [extension à d'autres pays](extension-internationale.md).

## État : MVP atteint le 16 octobre 2026

`essentiel --adresse "…"` donne en 1,5 s environ le prix moyen de l'électricité et le verdict
carbone, juste sur les [11 adresses de recette](exemples/recette_mvp.md), Corse et outre-mer
compris. `refresh` met toutes les sources à jour en une minute (9 étapes sur 9 le 16 oct.).

| Brique | Où |
|---|---|
| Adresse → commune, département, région | `ingestion/geocode.py` (BAN) |
| Prix par tranche Eurostat ; tranche IA (< 20 MWh/an) sans `--conso` ; scénarios à 3 ans | `fct_prix_entreprise_tranche` |
| Carbone par système électrique (continent, Corse, chaque DROM), seuil de 100 gCO₂e/kWh | `fct_carbone_territoire`, seed ADEME |
| Réponse courte en texte ou JSON, reprise en tête de `diagnose` et des rapports | `essentiel` |
| Diagnostic complet : industrie, réseau, raccordement, risques | `diagnose`, `compare` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |
| Carte 3D hexagonale : relief IGN, 4 lentilles, outre-mer en encarts | `carte`, `ingestion/hexagones.py`, `reporting/carte.html` |
| Facture rapportée au chiffre d'affaires (`--ca`), avec la fourchette des scénarios à 3 ans | `essentiel`, `diagnose` |
| 338 tests sans réseau, CI à chaque PR | `ingestion/tests/` |

**À surveiller** : les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 ;
le calendrier Tempo n'a tourné que sur des réponses simulées (pas de compte RTE).

## Suite

| Priorité | Étape | Bloqué par |
|---|---|---|
| 1 | Protéger `main` : CI et PR obligatoires | réglage GitHub, par le propriétaire |
| 2 | Extension à d'autres pays : [pistes](extension-internationale.md) | — |
| 3 | Carbone horaire en Corse et outre-mer (open data EDF SEI) | — |
| 4 | Vraie courbe de charge (export Enedis) à la place du profil type | — |
| 5 | Calendrier Tempo sur données réelles | compte RTE |
| 6 | Repère de prix au-delà de 150 000 MWh/an ; classement pondéré dans `compare` | décision |

## Décisions

| Date | Décision |
|---|---|
| 9 oct. | Diagnostic de contexte par adresse plutôt que « le prix et le CO₂ à l'adresse » ([cadrage](cadrage-besoins-entreprises.md)) |
| 12 oct. | Prix historiques par tranche Eurostat, pas de prix horaire ; scénarios à 3 ans |
| 15 oct. | Scénarios calibrés sur les erreurs passées ; comparaison sans classement |
| 16 oct. | MVP recentré sur deux réponses : prix moyen et verdict carbone, le diagnostic complet en complément |
| 16 oct. | Verdict sur le facteur ADEME en cycle de vie, au seuil de 100 gCO₂e/kWh de la taxonomie européenne ; le `taux_co2` RTE (émissions directes) reste un complément |
| 16 oct. | Sans `--conso`, tranche IA ; interface en ligne de commande |
| 16 oct. | Local et à la demande uniquement : aucune exécution planifiée ; Airflow supprimé |
| 16 oct. | Ingestion en PySpark (typage, UTC, clés du lot) ; Spark calcule et Arrow écrit, sans `winutils` sous Windows ; silver et gold restent en dbt-duckdb |
| 16 oct. | Python uniquement : PySpark retiré, l'ingestion revient à pyarrow ; plus besoin de Java |
| 16 oct. | Documentation en français uniquement ; code sous licence MIT |
| 16 oct. | Commentaires réduits au strict nécessaire : une ligne au-dessus d'une fonction ou d'un bloc |
| 18 oct. | Carte 3D en page HTML autonome écrite par Python ; three.js (MIT) embarqué, seul code JavaScript du dépôt, exécuté par le navigateur : l'installation ne change pas |
| 18 oct. | ETL confirmé en Python pur (pyarrow), sans PySpark ni Java ; commentaires limités à la logique (le pourquoi), 226 commentaires descriptifs retirés |
| 18 oct. | Sécurité : CI en lecture seule, actions GitHub figées par commit, identifiants RTE jamais transmis à dbt, téléchargements HTTPS uniquement, carte sans aucune requête externe (CSP) |
| 18 oct. | Identifiants dans `identifiants.local.txt`, ignoré par git et prioritaire sur `.env` ; un test vérifie qu'il n'est jamais versionné |
| 18 oct. | Grille hexagonale maison (cases régulières de 24 km) plutôt que H3 : rendu net et aucune dépendance ; relief IGN mis en cache par point dans le bronze |
| 23 oct. | `--ca` rapporte la facture estimée au chiffre d'affaires, sans seuil : le statut d'électro-intensif se juge sur la valeur ajoutée, que l'outil ne demande pas ; `--ca` exige `--conso` |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Spark (ingestion, silver, gold) | Exige Java ; pyarrow et dbt-duckdb suffisent à ce volume |
| Site web, exécution planifiée | Hors périmètre : ligne de commande, en local |
