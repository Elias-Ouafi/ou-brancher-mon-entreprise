from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from ingestion.config import Settings
from ingestion.datasets import NATIONAL_TR
from ingestion.transform import records_to_table, write_parquet
from ingestion.warehouse import DuckDBLoader, LoadResult, WarehouseLoader, build_loader

T0 = "2026-09-07T03:00:00+00:00"
T1 = "2026-09-07T03:15:00+00:00"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def make_parquet(tmp_path: Path):
    counter = 0

    def _make(*batches: tuple[list[dict[str, Any]], datetime]) -> Path:
        nonlocal counter
        counter += 1
        tables = [
            records_to_table(records, spec=NATIONAL_TR, ingested_at=ingested_at)
            for records, ingested_at in batches
        ]
        return write_parquet(
            pa.concat_tables(tables),
            dataset_dir=tmp_path / "eco2mix_national_tr",
            run_id=f"test-{counter}",
            ingest_date=date(2026, 9, 7),
        )

    return _make


@pytest.fixture
def loader() -> DuckDBLoader:
    with DuckDBLoader(":memory:") as instance:
        yield instance


# Aucune logique métier ne doit dépendre de DuckDB : le protocole fait foi
def test_duckdb_loader_satisfies_the_warehouse_protocol(loader: DuckDBLoader) -> None:
    assert isinstance(loader, WarehouseLoader)


def test_build_loader_returns_the_configured_backend(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, duckdb_path=tmp_path / "wh" / "eco2mix.duckdb")

    built = build_loader(settings)

    assert isinstance(built, WarehouseLoader)
    assert isinstance(built, DuckDBLoader)
    assert built.qualified_table == "bronze.national_tr"


def test_ensure_table_is_idempotent(loader: DuckDBLoader) -> None:
    loader.ensure_table()
    loader.ensure_table()

    assert loader.row_count() == 0
    columns = loader.connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'bronze' AND table_name = 'national_tr'"
    ).fetchall()
    assert ("date_heure",) in columns
    assert ("ingested_at_utc",) in columns


# Le contrôle de fraîcheur peut s'exécuter avant tout chargement
def test_counters_are_safe_before_the_first_load(loader: DuckDBLoader) -> None:
    assert loader.row_count() == 0
    assert loader.latest_timestamp() is None


# L'unicité est structurelle, pas seulement procédurale
def test_primary_key_forbids_duplicate_keys(loader: DuckDBLoader) -> None:
    loader.ensure_table()
    insert = (
        "INSERT INTO bronze.national_tr (date_heure, ingested_at_utc, source_dataset) "
        "VALUES (TIMESTAMPTZ '2026-09-07 03:00:00+00', TIMESTAMPTZ '2026-09-07 06:00:00+00', 'ds')"
    )
    loader.connection.execute(insert)

    with pytest.raises(duckdb.ConstraintException):
        loader.connection.execute(insert)


def test_merge_inserts_new_rows(loader: DuckDBLoader, make_parquet) -> None:
    parquet = make_parquet(
        (
            [
                {"date_heure": T0, "consommation": 42000},
                {"date_heure": T1, "consommation": 41800},
            ],
            utc(2026, 9, 7, 6, 0),
        )
    )

    result = loader.merge_parquet(parquet)

    assert result == LoadResult(
        target="bronze.national_tr",
        rows_in_file=2,
        rows_merged=2,
        rows_inserted=2,
        rows_updated=0,
    )
    assert loader.row_count() == 2
    assert loader.latest_timestamp() == utc(2026, 9, 7, 3, 15)


# Rejouer une fenêtre déjà chargée ne crée aucun doublon
def test_merge_is_idempotent_when_replayed(loader: DuckDBLoader, make_parquet) -> None:
    parquet = make_parquet(
        (
            [
                {"date_heure": T0, "consommation": 42000},
                {"date_heure": T1, "consommation": 41800},
            ],
            utc(2026, 9, 7, 6, 0),
        )
    )

    loader.merge_parquet(parquet)
    replay = loader.merge_parquet(parquet)

    assert loader.row_count() == 2
    assert replay.rows_inserted == 0
    assert replay.rows_updated == 2
    assert loader.connection.execute(
        "SELECT count(DISTINCT date_heure) FROM bronze.national_tr"
    ).fetchone() == (2,)


# Les données temps réel sont révisées : la dernière version gagne
def test_merge_overwrites_revised_values(loader: DuckDBLoader, make_parquet) -> None:
    initial = make_parquet(([{"date_heure": T0, "consommation": 42000}], utc(2026, 9, 7, 6, 0)))
    revised = make_parquet(([{"date_heure": T0, "consommation": 42500}], utc(2026, 9, 7, 7, 0)))

    loader.merge_parquet(initial)
    result = loader.merge_parquet(revised)

    assert loader.row_count() == 1
    assert result.rows_updated == 1
    row = loader.connection.execute(
        "SELECT consommation, ingested_at_utc FROM bronze.national_tr"
    ).fetchone()
    assert row == (42500.0, utc(2026, 9, 7, 7, 0))


# Un même `date_heure` deux fois dans le fichier : une seule ligne insérée
def test_merge_keeps_the_latest_revision_within_a_single_batch(
    loader: DuckDBLoader, make_parquet
) -> None:
    parquet = make_parquet(
        ([{"date_heure": T0, "consommation": 42000}], utc(2026, 9, 7, 6, 0)),
        ([{"date_heure": T0, "consommation": 42500}], utc(2026, 9, 7, 7, 0)),
    )

    result = loader.merge_parquet(parquet)

    assert result.rows_in_file == 2
    assert result.rows_merged == 1
    assert result.rows_deduplicated == 1
    assert loader.row_count() == 1
    assert loader.connection.execute("SELECT consommation FROM bronze.national_tr").fetchone() == (
        42500.0,
    )


# Une fenêtre sans donnée publiée ne doit pas faire échouer le chargement
def test_merge_accepts_an_empty_batch(loader: DuckDBLoader, make_parquet) -> None:
    parquet = make_parquet(([], utc(2026, 9, 7, 6, 0)))

    result = loader.merge_parquet(parquet)

    assert result.rows_merged == 0
    assert loader.row_count() == 0


# RTE publie l'horodatage le plus récent avant d'en avoir les mesures
def test_latest_timestamp_can_ignore_unmeasured_rows(loader: DuckDBLoader, make_parquet) -> None:
    parquet = make_parquet(
        (
            [
                {"date_heure": T0, "consommation": 42000},
                {"date_heure": T1, "consommation": None},
            ],
            utc(2026, 9, 7, 6, 0),
        )
    )
    loader.merge_parquet(parquet)

    assert loader.latest_timestamp() == utc(2026, 9, 7, 3, 15)
    assert loader.latest_timestamp(measure="consommation") == utc(2026, 9, 7, 3, 0)


# Le nom est interpolé dans le SQL : il doit appartenir au schéma
def test_latest_timestamp_rejects_an_unknown_measure(loader: DuckDBLoader) -> None:
    loader.ensure_table()

    with pytest.raises(ValueError, match="colonne inconnue"):
        loader.latest_timestamp(measure="1=1; DROP TABLE bronze.national_tr")


# Le contrôle de fraîcheur lit sans prendre le verrou d'écriture de DuckDB
def test_file_database_is_created_and_readable_in_read_only(tmp_path: Path, make_parquet) -> None:
    database = tmp_path / "warehouse" / "eco2mix.duckdb"
    parquet = make_parquet(([{"date_heure": T0, "consommation": 42000}], utc(2026, 9, 7, 6, 0)))

    with DuckDBLoader(database) as writer:
        writer.merge_parquet(parquet)

    assert database.exists()

    with DuckDBLoader(database, read_only=True) as reader:
        assert reader.row_count() == 1
        assert reader.latest_timestamp() == utc(2026, 9, 7, 3, 0)
