---
name: nouvelle-source
description: Ajouter une source de données publique au projet selon l'architecture médaillon (bronze Parquet + MERGE DuckDB, silver et gold dbt). À utiliser dès qu'une étape du plan introduit un nouveau jeu de données (Enedis, RTE, Géorisques, data.gouv.fr…).
---

# Ajouter une source

Modèle à suivre : le commit `242e88d` (DVF, étape 12), `git show 242e88d`.

## Avant de coder

1. Producteur officiel et licence ouverte, sinon s'arrêter et le dire (une copie tierce sert au
   mieux de repli signalé). Noter l'URL, la licence, la maille, le millésime, le volume.
2. Interroger l'API ou le fichier réel une fois pour voir les colonnes, les pièges (BOM, codes
   INSEE tronqués, secret statistique, pagination plafonnée) et le total annoncé.
3. Choisir le mode : médaillon (ci-dessous) pour un jeu national chargé par `refresh` ; **appel à
   la demande, sans stockage**, pour ce qui dépend du point et change sans prévenir (zone
   d'urbanisme, arrêtés sur l'eau, itinéraires). Modèle à la demande : `ingestion/eau.py` (client
   httpx, erreur propre `…Error`, `completer_…` dans `ingestion/diagnostic.py` qui journalise
   l'échec sans bloquer, coupé par `--sans-risques`). Noter le choix en décision datée dans PLAN.

## Fichiers à toucher, dans l'ordre

| Couche | Fichier |
|---|---|
| Spécification du jeu (colonnes typées, clé) | `ingestion/datasets.py` : un `DatasetSpec`, ajouté à `SPECS` |
| Extraction | `ingestion/extract_<source>.py` (httpx + tenacity, HTTPS seulement) |
| Chargement bronze (Parquet immuable puis `MERGE`) | `ingestion/pipeline.py` |
| Intégration au `refresh` | `ingestion/refresh.py` |
| Source dbt | `dbt/models/bronze/_bronze__sources.yml` |
| Silver nettoyé (codes INSEE en texte) | `dbt/models/silver/<jeu>.sql` + `_silver__models.yml` |
| Gold | `dbt/models/gold/fct_<indicateur>.sql` + `_gold__models.yml` (tests de données) |
| Limite de l'indicateur | `dbt/seeds/limites_methodologiques.csv` (chiffres de `diagnose` seulement) |
| Sortie | `ingestion/diagnostic.py` (libellé dans les chargements, ligne affichée) |
| Base synthétique qui reproduit les pièges | `ingestion/tests/medallion.py` |
| Tests sans réseau (`pytest-httpx`) | `ingestion/tests/test_<source>.py`, `test_refresh.py` |

## Ensuite

- `python .claude/scripts/verifier.py`, puis un `refresh` réel de la source et l'essai sur un
  projet de 1 à 50 MW.
- Skill `cloturer-etape` pour README, PLAN et la PR.
