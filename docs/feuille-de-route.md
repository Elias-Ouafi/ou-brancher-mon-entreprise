# Feuille de route

> Mise à jour le 16 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en local
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
| 318 tests sans réseau, CI à chaque PR | `ingestion/tests/` |

**À surveiller** : les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 ;
le calendrier Tempo n'a tourné que sur des réponses simulées (pas de compte RTE).

## Suite

| Priorité | Étape | Bloqué par |
|---|---|---|
| 1 | Protéger `main` : CI et PR obligatoires | réglage GitHub, par le propriétaire |
| 2 | Extension à d'autres pays : [pistes](extension-internationale.md) | — |
| 3 | Carbone horaire en Corse et outre-mer (open data EDF SEI) | — |
| 4 | Facture rapportée au chiffre d'affaires (`--ca`) | — |
| 5 | Vraie courbe de charge (export Enedis) à la place du profil type | — |
| 6 | Calendrier Tempo sur données réelles | compte RTE |
| 7 | Repère de prix au-delà de 150 000 MWh/an ; classement pondéré dans `compare` | décision |

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
| 16 oct. | Commentaires réduits au strict nécessaire : une ligne au-dessus d'une fonction ou d'un bloc |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Spark pour silver, gold et la lecture | dbt-duckdb suffit à ce volume ; démarrer Spark ferait passer `essentiel` de 1,5 s à 6 s |
| Site web, exécution planifiée | Hors périmètre : ligne de commande, en local |
