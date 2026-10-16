from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from ingestion.datasets import INGESTED_AT_COLUMN, SOURCE_COLUMN, ColumnKind, DatasetSpec

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


class NonExistentLocalTimeError(ValueError):
    pass


def _parse_iso(raw: str) -> datetime:
    text = raw.strip().replace(" ", "T")
    if text.endswith(("z", "Z")):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text)


def _is_nonexistent(naive: datetime) -> bool:
    localized = naive.replace(tzinfo=PARIS_TZ)
    return localized.astimezone(UTC).astimezone(PARIS_TZ).replace(tzinfo=None) != naive


def _is_ambiguous(naive: datetime) -> bool:
    return (
        naive.replace(tzinfo=PARIS_TZ, fold=0).utcoffset()
        != naive.replace(tzinfo=PARIS_TZ, fold=1).utcoffset()
    )


# Instant ODRÉ en UTC : sans décalage c'est l'heure de Paris ; `previous` départage l'heure doublée
def to_utc(raw: str, previous: datetime | None = None) -> datetime:
    parsed = _parse_iso(raw)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    if _is_nonexistent(parsed):
        raise NonExistentLocalTimeError(
            f"{raw!r} n'existe pas dans le fuseau Europe/Paris (passage à l'heure d'été)"
        )
    candidate = parsed.replace(tzinfo=PARIS_TZ, fold=0).astimezone(UTC)
    if _is_ambiguous(parsed) and previous is not None and candidate <= previous:
        candidate = parsed.replace(tzinfo=PARIS_TZ, fold=1).astimezone(UTC)
    return candidate


def normalize_datetimes(raw_values: Iterable[str]) -> list[datetime]:
    instants: list[datetime] = []
    previous: datetime | None = None
    for raw in raw_values:
        previous = to_utc(raw, previous)
        instants.append(previous)
    return instants


# Mesure en float ; une valeur illisible devient nulle et est signalée
def _coerce_float(value: Any, *, column: str = "") -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        logger.warning("valeur non numerique ignoree colonne=%s valeur=%r", column, text)
        return None


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Un instant précédent par groupe : les régions publient le même instant chacune
def _normalize_instant_column(
    records: Sequence[dict[str, Any]],
    *,
    name: str,
    group_names: Sequence[str],
) -> list[datetime]:
    previous: dict[tuple[str | None, ...], datetime] = {}
    instants: list[datetime] = []
    for record in records:
        group = tuple(_coerce_str(record.get(g)) for g in group_names)
        value = to_utc(str(record[name]), previous.get(group))
        previous[group] = value
        instants.append(value)
    return instants


# Enregistrements bruts → table typée du spec : clés complètes, instants en UTC
def records_to_table(
    records: Sequence[dict[str, Any]],
    *,
    spec: DatasetSpec,
    ingested_at: datetime,
) -> pa.Table:
    if ingested_at.tzinfo is None:
        raise ValueError("ingested_at doit être un datetime aware")

    requises = [c.name for c in spec.keys if not c.vide_si_absent]
    usable = [r for r in records if all(_coerce_str(r.get(n)) for n in requises)]
    if (dropped := len(records) - len(usable)) > 0:
        logger.warning("%d enregistrement(s) sans cle complete %s ignore(s)", dropped, requises)

    if usable:
        seen: set[str] = set().union(*(r.keys() for r in usable))
        technical = {INGESTED_AT_COLUMN, SOURCE_COLUMN}
        if missing := sorted(set(spec.schema.names) - seen - technical):
            logger.warning("colonnes absentes de la reponse API, remplies a null : %s", missing)

    group_names = [c.name for c in spec.keys if c.kind is not ColumnKind.INSTANT]
    columns: dict[str, list[Any]] = {}
    for column in spec.columns:
        if column.kind is ColumnKind.INSTANT:
            columns[column.name] = _normalize_instant_column(
                usable, name=column.name, group_names=group_names
            )
        elif column.kind is ColumnKind.MEASURE:
            columns[column.name] = [
                _coerce_float(r.get(column.name), column=column.name) for r in usable
            ]
        elif column.vide_si_absent:
            columns[column.name] = [_coerce_str(r.get(column.name)) or "" for r in usable]
        else:
            columns[column.name] = [_coerce_str(r.get(column.name)) for r in usable]
    columns[INGESTED_AT_COLUMN] = [ingested_at.astimezone(UTC)] * len(usable)
    columns[SOURCE_COLUMN] = [spec.dataset_id] * len(usable)

    table = pa.table(columns, schema=spec.schema)
    return table.sort_by([(name, "ascending") for name in spec.key_names])


# `run_id` utilisable comme nom de fichier sous Windows et Linux
def _safe_run_id(run_id: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("-", run_id).strip("-") or "run"


# Bronze immuable : un fichier par exécution, partitionné par date d'ingestion
def write_parquet(
    table: pa.Table,
    *,
    dataset_dir: Path,
    run_id: str,
    ingest_date: date,
) -> Path:
    partition = dataset_dir / f"ingest_date={ingest_date.isoformat()}"
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / f"part-{_safe_run_id(run_id)}.parquet"
    pq.write_table(table, path, compression="zstd")
    logger.info("parquet ecrit rows=%d path=%s", table.num_rows, path)
    return path
