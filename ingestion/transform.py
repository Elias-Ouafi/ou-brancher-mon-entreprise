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
from pyspark.sql import Column as SparkColumn
from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from ingestion.datasets import INGESTED_AT_COLUMN, SOURCE_COLUMN, ColumnKind, DatasetSpec
from ingestion.spark import session

logger = logging.getLogger(__name__)

PARIS_TZ = ZoneInfo("Europe/Paris")
PARIS = "Europe/Paris"
ORDRE = "_ordre"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


class NonExistentLocalTimeError(ValueError):
    pass


# Valeur brute en texte : Spark reçoit une table homogène, le typage se fait ensuite
def _brut(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    return str(value)


def _texte(colonne: SparkColumn) -> SparkColumn:
    return F.when(F.trim(colonne) != "", F.trim(colonne))


# Instant ODRÉ en UTC : avec décalage il est déjà absolu ; sans, c'est l'heure de Paris
def _instant_utc(df: DataFrame, name: str, groupe: Sequence[str]) -> DataFrame:
    texte = F.regexp_replace(F.regexp_replace(F.trim(F.col(name)), " ", "T"), "[zZ]$", "+00:00")
    absolu = texte.rlike(r"[+-]\d{2}:?\d{2}$")
    local = F.when(~absolu, texte.cast("timestamp"))
    ete = F.to_utc_timestamp(local, PARIS)
    ambigu = F.from_utc_timestamp(ete + F.expr("INTERVAL 1 HOUR"), PARIS) == local
    occurrence = F.row_number().over(Window.partitionBy(*groupe, local).orderBy(ORDRE))
    df = df.withColumn(
        f"_{name}_inexistant", local.isNotNull() & (F.from_utc_timestamp(ete, PARIS) != local)
    ).withColumn(
        f"_{name}_utc",
        F.when(absolu, texte.cast("timestamp"))
        .when(ambigu & (occurrence > 1), ete + F.expr("INTERVAL 1 HOUR"))
        .otherwise(ete),
    )
    if inexistant := df.filter(F.col(f"_{name}_inexistant")).select(name).first():
        raise NonExistentLocalTimeError(
            f"{inexistant[0]!r} n'existe pas dans le fuseau Europe/Paris (passage à l'heure d'été)"
        )
    if illisible := df.filter(F.col(f"_{name}_utc").isNull()).select(name).first():
        raise ValueError(f"horodatage illisible : {illisible[0]!r}")
    return df.drop(name, f"_{name}_inexistant").withColumnRenamed(f"_{name}_utc", name)


# Mesure en double ; une valeur illisible devient nulle et est signalée
def _mesure(df: DataFrame, name: str) -> DataFrame:
    valeur = F.expr(f"try_cast(trim(`{name}`) as double)")
    illisibles = df.filter(_texte(F.col(name)).isNotNull() & valeur.isNull())
    for (exemple,) in illisibles.select(name).limit(3).collect():
        logger.warning("valeur non numerique ignoree colonne=%s valeur=%r", name, exemple.strip())
    return df.withColumn(name, valeur)


def _table_brute(records: Sequence[dict[str, Any]], spec: DatasetSpec) -> pa.Table:
    colonnes = {c.name: pa.array([_brut(r.get(c.name)) for r in records], pa.string())
                for c in spec.columns}  # fmt: skip
    colonnes[ORDRE] = pa.array(range(len(records)), pa.int64())
    return pa.table(colonnes)


# Enregistrements typés et normalisés en UTC, avec la date d'ingestion
def records_to_table(
    records: Sequence[dict[str, Any]],
    *,
    spec: DatasetSpec,
    ingested_at: datetime,
) -> pa.Table:
    if ingested_at.tzinfo is None:
        raise ValueError("ingested_at doit être un datetime aware")
    if not records:
        return spec.schema.empty_table()

    technical = {INGESTED_AT_COLUMN, SOURCE_COLUMN}
    seen: set[str] = set().union(*(r.keys() for r in records))
    if missing := sorted(set(spec.schema.names) - seen - technical):
        logger.warning("colonnes absentes de la reponse API, remplies a null : %s", missing)

    df = session().createDataFrame(_table_brute(records, spec))
    requises = [c.name for c in spec.keys if not c.vide_si_absent]
    complet = df.filter(_tout([_texte(F.col(n)).isNotNull() for n in requises]))
    if (ecartes := df.count() - complet.count()) > 0:
        logger.warning("%d enregistrement(s) sans cle complete %s ignore(s)", ecartes, requises)
    if complet.isEmpty():
        return spec.schema.empty_table()
    df = complet

    groupe = [c.name for c in spec.keys if c.kind is not ColumnKind.INSTANT]
    for column in spec.columns:
        if column.kind is ColumnKind.INSTANT:
            df = _instant_utc(df, column.name, groupe)
        elif column.kind is ColumnKind.MEASURE:
            df = _mesure(df, column.name)
        elif column.vide_si_absent:
            df = df.withColumn(column.name, F.coalesce(_texte(F.col(column.name)), F.lit("")))
        else:
            df = df.withColumn(column.name, _texte(F.col(column.name)))

    df = (
        df.withColumn(INGESTED_AT_COLUMN, F.lit(ingested_at.astimezone(UTC)).cast("timestamp"))
        .withColumn(SOURCE_COLUMN, F.lit(spec.dataset_id))
        .orderBy(*spec.key_names, ORDRE)
        .select(*spec.schema.names)
    )
    return _au_schema(df.toArrow(), spec.schema)


def _tout(conditions: list[SparkColumn]) -> SparkColumn:
    resultat = F.lit(True)
    for condition in conditions:
        resultat = resultat & condition
    return resultat


def _au_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    return pa.Table.from_arrays([table.column(f.name).cast(f.type) for f in schema], schema=schema)


# Conversion d'une série d'horodatages ODRÉ, dans l'ordre : même règle que `records_to_table`
def normalize_datetimes(raw_values: Iterable[str]) -> list[datetime]:
    valeurs = list(raw_values)
    if not valeurs:
        return []
    table = pa.table({"t": pa.array(valeurs, pa.string()), ORDRE: range(len(valeurs))})
    df = _instant_utc(session().createDataFrame(table), "t", []).orderBy(ORDRE)
    return [v.replace(tzinfo=UTC) for v in df.toArrow().column("t").to_pylist()]


# Une date publiée, normalisée en UTC
def to_utc(raw: str) -> datetime:
    return normalize_datetimes([raw])[0]


# `run_id` utilisable comme nom de fichier sous Windows et Linux
def safe_run_id(run_id: str) -> str:
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
    nom = f"part-{safe_run_id(run_id)}"
    path = partition / f"{nom}.parquet"
    # Même run_id le même jour (deux mois Tempo chargés à la suite) : nouveau fichier, sans écraser
    rang = 2
    while path.exists():
        path = partition / f"{nom}-{rang}.parquet"
        rang += 1
    pq.write_table(table, path, compression="zstd")
    logger.info("parquet ecrit rows=%d path=%s", table.num_rows, path)
    return path
