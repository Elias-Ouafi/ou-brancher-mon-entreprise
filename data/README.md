# `data/` — stockage local (non versionné)

Entièrement gitignoré, sauf ce README ; recréé à la demande par le pipeline.

```
data/
├── bronze/<jeu>/ingest_date=AAAA-MM-JJ/part-<run_id>.parquet   # un fichier par exécution, immuable
├── warehouse/eco2mix.duckdb                                    # schémas bronze, silver, gold
└── reports/                                                    # rapports générés (PDF, Markdown, PNG)
```

Jeux en bronze : éCO2mix temps réel et consolidé, calendrier Tempo, consommation par IRIS,
équilibre régional, contraintes régionales, contraintes par ouvrage, postes RTE, prix Eurostat.

| Couche | Emplacement | Écrite par | Mode |
|---|---|---|---|
| Bronze | `bronze/*.parquet` | `ingestion` | ajout seul ; **historique complet des révisions** (partition = date d'ingestion) |
| Bronze | schéma `bronze` | `ingestion` | MERGE sur la clé de chaque jeu : dernière version, sans doublon |
| Silver, gold | schémas `silver`, `gold` | dbt | reconstruites à chaque run |

**DuckDB n'accepte qu'un writer** : ne gardez pas de session `duckdb` ouverte pendant un
chargement ou un `dbt build`.

**Repartir de zéro** : `rm -rf data/bronze data/warehouse`, puis relancer les commandes de
chargement du README. Tout est ré-extractible ; l'historique éCO2mix complet tient en deux
appels d'export.

Volumes (15 octobre 2026) : 508 000 lignes consolidées depuis 2012, base de 235 Mo.
