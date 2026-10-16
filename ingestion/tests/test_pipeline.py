from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.datasets import NATIONAL_CONS_DEF, NATIONAL_TR
from ingestion.pipeline import (
    StaleDataError,
    check_freshness,
    extract_to_parquet,
    run_ingestion,
)
from ingestion.warehouse import build_loader

NOW = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)


@pytest.fixture
def empty_payload() -> dict[str, Any]:
    return {"total_count": 0, "results": []}


def test_extract_writes_a_partitioned_parquet(
    httpx_mock: HTTPXMock,
    settings: Settings,
    records_payload: dict[str, Any],
) -> None:
    httpx_mock.add_response(json=records_payload)

    outcome = extract_to_parquet(settings=settings, run_id="manual-test", now=NOW)

    path = Path(outcome.parquet_path)
    assert path.exists()
    assert path.parent.name == "ingest_date=2026-09-07"
    assert path.parent.parent == NATIONAL_TR.bronze_dataset_dir(settings.bronze_dir)
    assert outcome.rows == 2
    # Une fenêtre horaire coûte un seul appel : c'est la base du calcul de quota
    assert outcome.api_calls == 1
    assert outcome.window_start == "2026-09-07T03:00:00+00:00"


# Une fenêtre vide ne doit pas faire échouer l'extraction : c'est le rôle du contrôle
def test_extract_writes_an_empty_file_when_nothing_is_published(
    httpx_mock: HTTPXMock,
    settings: Settings,
    empty_payload: dict[str, Any],
) -> None:
    httpx_mock.add_response(json=empty_payload)

    outcome = extract_to_parquet(settings=settings, now=NOW)

    assert outcome.rows == 0
    assert Path(outcome.parquet_path).exists()


# Deux runs sur la même fenêtre : les révisions écrasent, rien ne se duplique
def test_ingestion_is_idempotent_end_to_end(
    httpx_mock: HTTPXMock,
    settings: Settings,
    records_payload: dict[str, Any],
) -> None:
    httpx_mock.add_response(json=records_payload)
    httpx_mock.add_response(json=records_payload)

    _, first = run_ingestion(settings=settings, run_id="run-1", now=NOW)
    _, second = run_ingestion(settings=settings, run_id="run-2", now=NOW + timedelta(hours=1))

    assert first.rows_inserted == 2
    assert second.rows_inserted == 0
    assert second.rows_updated == 2

    loader = build_loader(settings)
    try:
        assert loader.row_count() == 2
        assert loader.latest_timestamp() == datetime(2026, 9, 7, 3, 15, tzinfo=UTC)
    finally:
        loader.close()


# Au premier run horaire, la table du consolidé doit exister (vide) pour dbt
def test_loading_one_dataset_creates_every_bronze_table(
    httpx_mock: HTTPXMock,
    settings: Settings,
    records_payload: dict[str, Any],
) -> None:
    httpx_mock.add_response(json=records_payload)

    run_ingestion(settings=settings, now=NOW)

    loader = build_loader(settings, spec=NATIONAL_CONS_DEF, read_only=True)
    try:
        assert loader.row_count() == 0
        assert loader.qualified_table == "bronze.national_cons_def"
        assert loader._table_exists()
    finally:
        loader.close()


def test_freshness_passes_within_the_threshold(
    httpx_mock: HTTPXMock,
    settings: Settings,
    records_payload: dict[str, Any],
) -> None:
    httpx_mock.add_response(json=records_payload)
    run_ingestion(settings=settings, now=NOW)

    report = check_freshness(settings=settings, now=NOW - timedelta(hours=1))

    assert report.is_fresh
    assert report.row_count == 2
    assert report.lag_hours == pytest.approx(1.75)


# Au-delà de 2 h de retard, le contrôle de fraîcheur doit échouer
def test_freshness_fails_when_data_is_late(
    httpx_mock: HTTPXMock,
    settings: Settings,
    records_payload: dict[str, Any],
) -> None:
    httpx_mock.add_response(json=records_payload)
    run_ingestion(settings=settings, now=NOW)

    with pytest.raises(StaleDataError, match="retard"):
        check_freshness(settings=settings, now=NOW + timedelta(hours=6))


# La dernière ligne publiée par RTE n'a pas encore ses mesures : elle ne compte pas
def test_freshness_ignores_the_unmeasured_leading_row(
    httpx_mock: HTTPXMock,
    settings: Settings,
) -> None:
    httpx_mock.add_response(
        json={
            "total_count": 2,
            "results": [
                {"date_heure": "2026-09-07T03:00:00+00:00", "consommation": 42000},
                {"date_heure": "2026-09-07T03:15:00+00:00", "consommation": None},
            ],
        }
    )
    run_ingestion(settings=settings, now=NOW)

    report = check_freshness(settings=settings, now=NOW - timedelta(hours=1))

    assert report.latest == datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
    assert report.latest_row == datetime(2026, 9, 7, 3, 15, tzinfo=UTC)
    assert report.lag_hours == pytest.approx(2.0)


# Des horodatages sans aucune mesure : le flux est mort, la tâche doit échouer
def test_freshness_fails_when_rows_have_no_measure(
    httpx_mock: HTTPXMock,
    settings: Settings,
) -> None:
    httpx_mock.add_response(
        json={
            "total_count": 1,
            "results": [{"date_heure": "2026-09-07T03:00:00+00:00", "consommation": None}],
        }
    )
    run_ingestion(settings=settings, now=NOW)

    with pytest.raises(StaleDataError, match="aucune valeur de consommation"):
        check_freshness(settings=settings, now=NOW)


# Avant le premier chargement, la base n'existe même pas : échec explicite
def test_freshness_fails_on_an_empty_warehouse(settings: Settings) -> None:
    assert not settings.duckdb_path.exists()

    with pytest.raises(StaleDataError, match="aucune donnee"):
        check_freshness(settings=settings, now=NOW)
