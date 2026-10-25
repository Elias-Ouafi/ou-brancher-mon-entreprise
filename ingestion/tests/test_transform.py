from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pyarrow.parquet as pq
import pytest

from ingestion.datasets import KEY_COLUMN, NATIONAL_TR
from ingestion.transform import (
    NonExistentLocalTimeError,
    normalize_datetimes,
    records_to_table,
    to_utc,
    write_parquet,
)

SCHEMA = NATIONAL_TR.schema
INGESTED_AT = datetime(2026, 9, 7, 6, 30, tzinfo=UTC)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-07T03:00:00+00:00", utc(2026, 9, 7, 3, 0)),
        ("2026-09-07T03:00:00Z", utc(2026, 9, 7, 3, 0)),
        # Décalage explicite : l'instant est déjà non ambigu
        ("2026-09-07T05:00:00+02:00", utc(2026, 9, 7, 3, 0)),
        # Heure locale naïve, en heure d'été (UTC+2)
        ("2026-09-07 05:00:00", utc(2026, 9, 7, 3, 0)),
        # Heure locale naïve, en heure d'hiver (UTC+1)
        ("2026-01-15 05:00:00", utc(2026, 1, 15, 4, 0)),
    ],
)
def test_to_utc_accepts_both_representations(raw: str, expected: datetime) -> None:
    assert to_utc(raw) == expected


# Le saut 01:45 -> 03:00 en local doit rester continu en UTC
def test_march_gap_produces_contiguous_utc_series() -> None:
    instants = normalize_datetimes(
        [
            "2026-03-29 01:30:00",
            "2026-03-29 01:45:00",
            "2026-03-29 03:00:00",
            "2026-03-29 03:15:00",
        ]
    )

    assert instants == [
        utc(2026, 3, 29, 0, 30),
        utc(2026, 3, 29, 0, 45),
        utc(2026, 3, 29, 1, 0),
        utc(2026, 3, 29, 1, 15),
    ]
    steps = {b - a for a, b in pairwise(instants)}
    assert steps == {timedelta(minutes=15)}


# 02:30 n'existe pas le 29 mars 2026 : on refuse de deviner
def test_march_nonexistent_local_time_is_rejected() -> None:
    with pytest.raises(NonExistentLocalTimeError, match="heure d'été"):
        to_utc("2026-03-29 02:30:00")


# L'heure locale 02:00-02:45 apparaît deux fois : deux instants distincts
def test_october_repeated_hour_is_disambiguated_by_sequence() -> None:
    instants = normalize_datetimes(
        [
            "2026-10-25 01:45:00",
            "2026-10-25 02:00:00",
            "2026-10-25 02:15:00",
            "2026-10-25 02:30:00",
            "2026-10-25 02:45:00",
            "2026-10-25 02:00:00",
            "2026-10-25 02:15:00",
            "2026-10-25 02:30:00",
            "2026-10-25 02:45:00",
            "2026-10-25 03:00:00",
        ]
    )

    assert instants[0] == utc(2026, 10, 24, 23, 45)
    assert instants[1] == utc(2026, 10, 25, 0, 0)
    assert instants[5] == utc(2026, 10, 25, 1, 0)
    assert instants[-1] == utc(2026, 10, 25, 2, 0)

    # Aucun doublon, aucun retour en arrière : la clé de MERGE reste unique
    assert len(set(instants)) == len(instants)
    assert instants == sorted(instants)
    steps = {b - a for a, b in pairwise(instants)}
    assert steps == {timedelta(minutes=15)}


# Sans contexte, on retient la première occurrence (heure d'été)
def test_october_isolated_ambiguous_time_defaults_to_first_occurrence() -> None:
    assert to_utc("2026-10-25 02:30:00") == utc(2026, 10, 25, 0, 30)


def test_records_to_table_applies_schema_and_metadata() -> None:
    records = [
        {"date_heure": "2026-09-07T03:15:00+00:00", "consommation": 41800, "perimetre": "France"},
        {"date_heure": "2026-09-07T03:00:00+00:00", "consommation": 42000, "perimetre": "France"},
    ]

    table = records_to_table(records, spec=NATIONAL_TR, ingested_at=INGESTED_AT)

    assert table.schema == SCHEMA
    # La table est triée par clé, quel que soit l'ordre d'arrivée
    assert table.column(KEY_COLUMN).to_pylist() == [utc(2026, 9, 7, 3, 0), utc(2026, 9, 7, 3, 15)]
    assert table.column("consommation").to_pylist() == [42000.0, 41800.0]
    # Colonne du schéma absente de la réponse : présente et nulle
    assert table.column("nucleaire").to_pylist() == [None, None]
    assert table.column("source_dataset").to_pylist() == ["eco2mix-national-tr"] * 2
    assert table.column("ingested_at_utc").to_pylist() == [INGESTED_AT] * 2


# L'export CSV renvoie des chaînes ; les vides deviennent des nulls
def test_records_to_table_coerces_csv_strings() -> None:
    records = [
        {"date_heure": "2026-09-07T03:00:00+00:00", "consommation": "42000", "solaire": ""},
    ]

    table = records_to_table(records, spec=NATIONAL_TR, ingested_at=INGESTED_AT)

    assert table.column("consommation").to_pylist() == [42000.0]
    assert table.column("solaire").to_pylist() == [None]


def test_records_to_table_drops_rows_without_key() -> None:
    records = [
        {"date_heure": "2026-09-07T03:00:00+00:00", "consommation": 42000},
        {"date_heure": None, "consommation": 1},
        {"consommation": 2},
    ]

    table = records_to_table(records, spec=NATIONAL_TR, ingested_at=INGESTED_AT)

    assert table.num_rows == 1


def test_records_to_table_rejects_naive_ingested_at() -> None:
    with pytest.raises(ValueError, match="aware"):
        records_to_table([], spec=NATIONAL_TR, ingested_at=datetime(2026, 9, 7, 6, 30))


def test_records_to_table_handles_empty_input() -> None:
    table = records_to_table([], spec=NATIONAL_TR, ingested_at=INGESTED_AT)

    assert table.num_rows == 0
    assert table.schema == SCHEMA


def test_write_parquet_creates_hive_partition(tmp_path) -> None:
    table = records_to_table(
        [{"date_heure": "2026-09-07T03:00:00+00:00", "consommation": 42000}],
        spec=NATIONAL_TR,
        ingested_at=INGESTED_AT,
    )

    path = write_parquet(
        table,
        dataset_dir=tmp_path / "eco2mix_national_tr",
        # Un run_id peut contenir `:` et `+`, interdits dans un nom de fichier Windows
        run_id="scheduled__2026-09-07T06:00:00+00:00",
        ingest_date=date(2026, 9, 7),
    )

    assert path.parent.name == "ingest_date=2026-09-07"
    assert path.name == "part-scheduled__2026-09-07T06-00-00-00-00.parquet"
    reloaded = pq.read_table(path)
    assert reloaded.schema == SCHEMA
    assert reloaded.num_rows == 1


# Le bronze garde chaque extraction : un même run_id le même jour n'écrase pas la précédente
def test_write_parquet_never_overwrites_an_earlier_run(tmp_path) -> None:
    table = records_to_table(
        [{"date_heure": "2026-09-07T03:00:00+00:00", "consommation": 42000}],
        spec=NATIONAL_TR,
        ingested_at=INGESTED_AT,
    )
    chemins = [
        write_parquet(
            table, dataset_dir=tmp_path / "rte_tempo", run_id="manual", ingest_date=date(2026, 9, 7)
        )
        for _ in range(3)
    ]

    assert [c.name for c in chemins] == [
        "part-manual.parquet",
        "part-manual-2.parquet",
        "part-manual-3.parquet",
    ]
    assert all(pq.read_table(c).num_rows == 1 for c in chemins)
