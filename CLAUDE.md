# Où brancher mon entreprise : consignes pour Claude

Le projet se décrit dans [README.md](README.md) (usage, architecture) et [PLAN.md](PLAN.md) (état,
étapes, décisions en vigueur). Les lire avant toute étape ; ce fichier ne les répète pas.

## Environnement

- Windows, PowerShell. Python 3.12 dans `.venv` (pas de `uv` sur ce poste), Java 17 pour PySpark.
- Tout se lance par `.\.venv\Scripts\python.exe -m …` depuis la racine du dépôt.
- `data/` (bronze, warehouse DuckDB, rapports) n'est pas versionné et se recrée par
  `python -m ingestion.cli refresh --months 180`. DuckDB n'accepte qu'un writer : pas de session
  `duckdb` ouverte pendant un `refresh` ou un `dbt build`.
- `identifiants.local.txt` et `.env` ne se lisent pas et ne se citent jamais.

## Vérifier

`python .claude/scripts/verifier.py` joue dans l'ordre ce que la CI joue, puis l'essai réel :

1. `ruff check .` et `ruff format --check .`
2. `pytest` (473 tests sans réseau, dont un `dbt build` sur base synthétique)
3. essai réel : `essentiel` à Chilly-Mazarin, 12 MW et 70 000 MWh (warehouse et réseau requis)

`--rapide` saute pytest, `--sans-essai` saute l'essai réel. Une étape du plan n'est cochée qu'après
les trois, sur un projet de 1 à 50 MW.

## Règles du projet

- Persona : projets de 1 à 50 MW (data centers, IRVE, industrie, logistique). Les bureaux ne sont
  plus la cible.
- Local et à la demande : jamais de tâche planifiée, cron, workflow programmé ni Airflow.
- Nouvelle source : producteur officiel, licence ouverte ; bronze Parquet immuable + `MERGE`
  DuckDB, silver et gold en dbt, limite dans le seed `limites_methodologiques`
  (skill `nouvelle-source`).
- Code le plus simple possible ; commentaires sur la logique seulement (règle, piège d'une source,
  raison d'un choix), jamais d'en-tête d'utilité par fonction ou par modèle.
- Documentation en français, dans README et PLAN seulement. Tout chiffre a sa source et sa limite ;
  « non disponible » plutôt qu'un chiffre inventé.
- Tests sans réseau : API simulées (`pytest-httpx`), fixture `no_network`, `medallion_db` pour dbt.

## Git

`main` est protégée (PR obligatoire, CI verte sur branche à jour). Travailler sur une branche,
messages de commit en français, puis PR. Ne jamais pousser sur `main` ni forcer un push.
