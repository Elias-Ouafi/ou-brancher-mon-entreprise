# Feuille de route

> Mise à jour le 16 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer).
> Pourquoi ce périmètre : [cadrage](cadrage-besoins-entreprises.md). Après le MVP :
> [extension à d'autres pays](extension-internationale.md).

## Objectif du MVP

Une personne saisit une adresse ; l'outil répond à deux questions, en quelques lignes :

1. **Quel prix moyen de l'électricité vais-je payer ?** En €/kWh HTVA, avec la facture
   annuelle estimée si la consommation est connue.
2. **L'électricité y est-elle décarbonée ?** Oui ou non, avec l'intensité carbone en
   gCO₂/kWh et le seuil retenu.

Le diagnostic complet (réseau, raccordement, risques) reste disponible en complément.

## État du MVP (16 octobre 2026)

**Le MVP fonctionne** : `essentiel --adresse "…"` répond aux deux questions en 1,5 s environ,
sur 11 adresses de recette justes, Corse et outre-mer compris
([recette](exemples/recette_mvp.md)). Seule l'étape 9, un réglage GitHub, reste au
propriétaire du dépôt ; l'interface web (étape 7) n'a pas été retenue.

| Brique | Où |
|---|---|
| Adresse → commune, département, région, ZNI | `ingestion/geocode.py`, `dim_departement` |
| Prix moyen par tranche, tranche IA sans `--conso`, scénarios à 3 ans | `fct_prix_entreprise_tranche`, bloc 4 |
| Intensité carbone et verdict par système électrique (continent, Corse, chaque DROM) | `fct_carbone_territoire`, bloc 5 |
| Réponse courte, texte ou JSON ; même encadré en tête de `diagnose` et des rapports | `essentiel` (`ingestion/diagnostic.py`) |
| Rafraîchissement de toutes les sources, puis `dbt build` | `refresh`, workflow `refresh.yml` |
| 318 tests sans réseau, dont la recette ; dbt build en CI | `ingestion/tests/` |

**Encore à surveiller** : le workflow `refresh.yml` n'a pas encore tourné sur GitHub (la
commande `refresh` a été exécutée en local le 16 oct. : 9 étapes sur 9 en 52 s) ; les facteurs
ADEME des ZNI datent de décembre 2020.

## Étapes vers le MVP

| # | Étape | Fait |
|---|---|---|
| 1 | Trancher les trois décisions ci-dessous | 16 oct. : propositions validées |
| 2 | Intensité carbone par territoire | 16 oct. : `fct_carbone_territoire`, facteur ADEME en cycle de vie pour le continent (51,9 g, 2024), la Corse (594 g) et chaque DROM (702 à 957 g, déc. 2020) ; intensité directe RTE sur 12 mois en complément pour le continent (20 g au 8 oct. 2026) |
| 3 | Verdict « décarboné » | 16 oct. : `est_decarbone` au seuil de 100 gCO₂e/kWh, limite `carbone_territoire` |
| 4 | Prix par défaut sans `--conso` | 16 oct. : tranche IA, sans facture, avec l'invitation à préciser `--conso` ; rappel de la péréquation en ZNI |
| 5 | Commande `essentiel` | 16 oct. : 5 lignes (adresse, territoire, prix, verdict carbone, « à savoir ») en texte ou JSON avec les limites ; même encadré en tête de `diagnose`, des rapports Markdown et PDF ; ligne « Électricité décarbonée » dans `compare` ; adresse introuvable → message clair et code 1 |
| 6 | Rafraîchissement simple | 16 oct. : `refresh` (une source en panne n'arrête pas les autres, code 1 si échec) ; workflow GitHub Actions chaque lundi, warehouse gardé en cache et publié en artefact, vérification de Lyon et d'Ajaccio sur la vraie base |
| 7 | Interface web | non retenue (décision du 16 oct. : ligne de commande) |
| 8 | Recette | 16 oct. : 11 adresses réelles (Guadeloupe ajoutée), test de non-régression `test_recette_mvp.py` |
| 9 | **Protéger `main`** : CI et PR obligatoires | à faire, réglage GitHub par le propriétaire du dépôt |

**Critères de réussite du MVP**, tous atteints le 16 oct. : une adresse française quelconque
donne en moins de 5 secondes un prix en €/kWh et un verdict carbone sourcés ; les adresses de
recette sont justes, ZNI comprises ; une adresse introuvable donne un message clair, pas une
erreur ; les données se rafraîchissent sans intervention.

### Décisions (validées le 16 oct.)

| Décision | Proposition | Alternative |
|---|---|---|
| Seuil « décarboné » | **100 gCO₂e/kWh**, le seuil de la taxonomie européenne pour la production d'électricité : défendable et publié | Comparer à la moyenne de l'UE, ce qui donne un repère et non un oui/non |
| Prix par défaut sans `--conso` | **Tranche Eurostat la plus basse (IA, < 20 MWh/an)**, celle des TPE et commerces | Exiger `--conso` ; ou ajouter les ménages (`nrg_pc_204`, même API) si le public inclut des particuliers |
| Interface | **Ligne de commande** (décision du 9 oct., « ne fais pas de site »), Streamlit en option à l'étape 7 | Streamlit dès le MVP : c'est la seule façon de toucher des non-développeurs |

### Choix techniques

- **Python, dbt et DuckDB, sans PySpark** : le plus gros jeu fait 500 000 lignes et le
  `dbt build` complet dix secondes. PySpark ajouterait une JVM et un cluster sans aucun gain à
  ce volume ; DuckDB lit les mêmes Parquet et le passage à `dbt-spark` reste possible si le
  volume est multiplié par mille (voir [extension](extension-internationale.md)).
- **Airflow hors du MVP** : jamais lancé, il exige Docker. Un workflow planifié suffit pour une
  dizaine d'appels par jour ; les DAGs restent en place pour plus tard.
- **Seeds plutôt qu'API** pour les facteurs ZNI : un chiffre par territoire et par an, mis à
  jour une fois par an.

## Après le MVP

| Priorité | Étape | Bloqué par |
|---|---|---|
| 1 | Extension à d'autres pays : [pistes](extension-internationale.md) | MVP |
| 2 | Intensité carbone ZNI horaire (open data EDF SEI par territoire) | — |
| 3 | Situer l'électro-intensité : facture estimée rapportée au chiffre d'affaires (`--ca`) | — |
| 4 | Importer la vraie courbe de charge (export Enedis) à la place du profil type | — |
| 5 | Airflow en exploitation 7 jours, alerte en cas d'échec ; DAG des référentiels | Docker Desktop |
| 6 | Calendrier Tempo sur données réelles | compte RTE, par l'utilisateur |
| 7 | Repère de prix au-delà de 150 000 MWh (contrats CAPN) | décision |
| 8 | Classement pondéré dans `compare` | décision : poids à exposer et justifier |

## Fait

| Domaine | Livré |
|---|---|
| Socle | Ingestion ODRÉ, RTE et Eurostat idempotente (MERGE, clés composites et optionnelles), UTC et changements d'heure, quota suivi ; médaillon DuckDB + dbt ; trois DAGs Airflow vérifiés en CI |
| Territoire | Référentiel commune → département → région ; pression industrielle (millésime sans secret statistique) ; tension régionale sur 12 mois |
| Réseau | Seuils de raccordement ; contraintes prospectives et postes RTE par département ; zone prioritaire RTE la plus proche (SDDR 2025) |
| Foncier et risques | Site clé en main France 2030 le plus proche ; risques Géorisques à l'adresse, avec repli communal |
| Prix | Coût horaire d'un profil type (HP/HC, CRE) ; prix Eurostat par tranche ; scénarios à 3 ans calibrés sur les erreurs passées, contrôle sur le passé affiché |
| Restitution | `diagnose` (texte, PDF, Markdown, JSON) ; `compare` (2 à 6 adresses, sans classement) ; limites affichées partout ; exemples réels dans `docs/exemples/` |

## Décisions

| Date | Décision |
|---|---|
| 9 oct. | Diagnostic de contexte et de risque par adresse, plutôt que « le prix et le CO₂ à l'adresse » |
| 10 oct. | Coût du profil sur l'option HP/HC, sans compte RTE |
| 10 oct. | `fct_diagnostic_territoire` à la maille du département ; la commune s'y joint à la lecture |
| 12 oct. | Prix fixes et historiques par tranche (Eurostat), pas de prix horaire ; horizon de 3 ans ; `--conso` saisi |
| 15 oct. | Fourchette des scénarios calibrée sur les erreurs passées de la méthode |
| 15 oct. | Contraintes et postes RTE au département : aucun n'est géolocalisé en open data |
| 15 oct. | Comparaison côte à côte, sans classement |
| 16 oct. | Verdict carbone sur le facteur ADEME en cycle de vie, comparé au seuil de 100 gCO₂e/kWh de la taxonomie (même périmètre) ; le `taux_co2` RTE, émissions directes seulement, reste un complément. Prix par défaut : tranche IA. Interface : ligne de commande |
| 16 oct. | Exploitation du MVP par `refresh` et un workflow GitHub Actions hebdomadaire, sans Airflow |
| 16 oct. | MVP recentré sur deux réponses à l'adresse, prix moyen et verdict carbone, le diagnostic complet devenant un complément. Compatible avec le cadrage du 9 oct. : la réponse est honnête si elle dit que le prix dépend de la consommation et non de l'adresse, et que le carbone ne change qu'entre le continent, la Corse et l'outre-mer |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout, y compris en ZNI pour les tarifs réglementés |
| Intensité carbone régionale en métropole | Pas de `taux_co2` régional ni de facteur ADEME opposable : le réseau continental est un seul système. La Corse et l'outre-mer, non interconnectés, ont leur facteur (fait le 16 oct.) |
| Capacité de raccordement chiffrée | Absente de l'open data pour la consommation |
| Distance au poste RTE | Postes publiés sans coordonnées |
| Prix spot horaire | Hors besoin (décision du 12 oct.) |
| Mix régional au pas 15 min | 2,86 M lignes pour un apport faible au diagnostic |
| PySpark | Aucun gain sous quelques centaines de millions de lignes ; DuckDB suffit |
| Migration BigQuery | `BigQueryLoader` à écrire derrière l'interface existante, sans intérêt tant que le local suffit |
