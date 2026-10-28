from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import duckdb
import pyarrow as pa

from ingestion.config import Settings, get_settings
from ingestion.datasets import NATIONAL_TR, DatasetSpec

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "bronze"

# Correspondance des types pyarrow vers DuckDB. Le schéma du spec reste la source de vérité unique
_DUCKDB_TYPES: tuple[tuple[object, str], ...] = (
    (pa.timestamp("us", tz="UTC"), "TIMESTAMPTZ"),
    (pa.float64(), "DOUBLE"),
    (pa.string(), "VARCHAR"),
)


@dataclass(frozen=True, slots=True)
class LoadResult:
    target: str
    rows_in_file: int
    rows_merged: int
    rows_inserted: int
    rows_updated: int

    @property
    def rows_deduplicated(self) -> int:
        return self.rows_in_file - self.rows_merged

    def as_dict(self) -> dict[str, int | str]:
        return {
            "target": self.target,
            "rows_in_file": self.rows_in_file,
            "rows_merged": self.rows_merged,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
        }


@runtime_checkable
class WarehouseLoader(Protocol):
    spec: DatasetSpec

    def ensure_table(self) -> None: ...

    def merge_parquet(self, parquet_path: Path | str) -> LoadResult: ...

    # Plus récent `date_heure` présent, ou `None` si la table est vide
    def latest_timestamp(self, *, measure: str | None = None) -> datetime | None: ...

    def count_distinct_instants(self, *, start: datetime, end: datetime) -> int: ...

    def row_count(self) -> int: ...

    def close(self) -> None: ...


def _duckdb_type(arrow_type: pa.DataType) -> str:
    for candidate, sql_type in _DUCKDB_TYPES:
        if arrow_type.equals(candidate):
            return sql_type
    raise TypeError(f"type pyarrow non pris en charge par la DDL DuckDB : {arrow_type}")


class DuckDBLoader:
    def __init__(
        self,
        database: Path | str,
        *,
        spec: DatasetSpec = NATIONAL_TR,
        schema: str = DEFAULT_SCHEMA,
        read_only: bool = False,
    ) -> None:
        self.database = database
        self.spec = spec
        self.schema = schema
        self.read_only = read_only
        self._connection: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        spec: DatasetSpec = NATIONAL_TR,
        read_only: bool = False,
    ) -> DuckDBLoader:
        settings = settings or get_settings()
        return cls(database=settings.duckdb_path, spec=spec, read_only=read_only)

    @property
    def table(self) -> str:
        return self.spec.table_name

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def _key_list(self) -> str:
        return ", ".join(self.spec.key_names)

    def _key_join(self, left: str, right: str) -> str:
        return " AND ".join(f"{left}.{name} = {right}.{name}" for name in self.spec.key_names)

    # Ordre de départage des lignes d'une même clé dans un lot
    @property
    def _dedup_order(self) -> str:
        if not self.spec.measures:
            return "ingested_at_utc DESC"
        completude = " + ".join(
            f"CASE WHEN {column.name} IS NOT NULL THEN 1 ELSE 0 END"
            for column in self.spec.measures
        )
        return f"ingested_at_utc DESC, ({completude}) DESC"

    @property
    def _instant_column(self) -> str:
        instant = self.spec.instant_column
        if instant is None:
            raise ValueError(
                f"{self.spec.dataset_id} n'est pas une serie temporelle : "
                "pas de controle temporel possible"
            )
        return instant

    @property
    def _column_list(self) -> str:
        return ", ".join(self.spec.schema.names)

    # Connexion paresseuse : rien n'est ouvert tant qu'aucune requête n'est émise
    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            if isinstance(self.database, Path) and not self.read_only:
                self.database.parent.mkdir(parents=True, exist_ok=True)
            self._connection = duckdb.connect(str(self.database), read_only=self.read_only)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> DuckDBLoader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def ensure_table(self) -> None:
        columns = ",\n    ".join(
            f"{field.name} {_duckdb_type(field.type)}" + ("" if field.nullable else " NOT NULL")
            for field in self.spec.schema
        )
        self.connection.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
        self.connection.execute(
            f"CREATE TABLE IF NOT EXISTS {self.qualified_table} (\n"
            f"    {columns},\n"
            f"    PRIMARY KEY ({self._key_list})\n"
            f")"
        )

    # Fusionne le Parquet sur `date_heure` (idempotent, révisions écrasées)
    def merge_parquet(self, parquet_path: Path | str) -> LoadResult:
        self.ensure_table()
        source = str(parquet_path)
        connection = self.connection

        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE _batch AS
                SELECT {self._column_list}
                FROM read_parquet($source)
                QUALIFY row_number() OVER (
                    PARTITION BY {self._key_list} ORDER BY {self._dedup_order}
                ) = 1
                """,
                {"source": source},
            )
            rows_in_file = self._scalar(
                "SELECT count(*) FROM read_parquet($source)", {"source": source}
            )
            rows_merged = self._scalar("SELECT count(*) FROM _batch")
            rows_updated = self._scalar(
                f"""
                SELECT count(*) FROM {self.qualified_table} t
                WHERE EXISTS (SELECT 1 FROM _batch b WHERE {self._key_join("b", "t")})
                """
            )
            connection.execute(
                f"""
                DELETE FROM {self.qualified_table} t
                WHERE EXISTS (SELECT 1 FROM _batch b WHERE {self._key_join("b", "t")})
                """
            )
            connection.execute(f"INSERT INTO {self.qualified_table} BY NAME SELECT * FROM _batch")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.execute("DROP TABLE IF EXISTS _batch")

        result = LoadResult(
            target=self.qualified_table,
            rows_in_file=rows_in_file,
            rows_merged=rows_merged,
            rows_inserted=rows_merged - rows_updated,
            rows_updated=rows_updated,
        )
        logger.info("merge %s", result.as_dict())
        return result

    # Base absente ouverte en lecture seule : à traiter comme vide, pas comme une panne
    @property
    def _database_is_missing(self) -> bool:
        return self.read_only and isinstance(self.database, Path) and not self.database.exists()

    def latest_timestamp(self, *, measure: str | None = None) -> datetime | None:
        if self._database_is_missing or not self._table_exists():
            return None
        predicate = ""
        if measure is not None:
            # Le nom est interpole dans le SQL : on le valide contre le schema
            if measure not in self.spec.schema.names:
                raise ValueError(f"colonne inconnue : {measure}")
            predicate = f" WHERE {measure} IS NOT NULL"
        value = self._scalar(
            f"SELECT max({self._instant_column}) FROM {self.qualified_table}{predicate}"
        )
        if value is None:
            return None
        # DuckDB renvoie un datetime aware ; on force UTC pour comparer sans surprise
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def count_distinct_instants(self, *, start: datetime, end: datetime) -> int:
        instant = self._instant_column
        if self._database_is_missing or not self._table_exists():
            return 0
        return self._scalar(
            f"""
            SELECT count(DISTINCT {instant}) FROM {self.qualified_table}
            WHERE {instant} >= $start AND {instant} < $end
            """,
            {"start": start, "end": end},
        )

    def row_count(self) -> int:
        if self._database_is_missing or not self._table_exists():
            return 0
        return self._scalar(f"SELECT count(*) FROM {self.qualified_table}")

    def _table_exists(self) -> bool:
        return bool(
            self._scalar(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = $schema AND table_name = $table
                """,
                {"schema": self.schema, "table": self.table},
            )
        )

    def _scalar(self, query: str, parameters: dict[str, object] | None = None):
        row = self.connection.execute(query, parameters or {}).fetchone()
        return None if row is None else row[0]


def build_loader(
    settings: Settings | None = None,
    *,
    spec: DatasetSpec = NATIONAL_TR,
    read_only: bool = False,
) -> WarehouseLoader:
    settings = settings or get_settings()
    if settings.warehouse_backend == "duckdb":
        return DuckDBLoader.from_settings(settings, spec=spec, read_only=read_only)
    raise NotImplementedError(f"backend warehouse inconnu : {settings.warehouse_backend}")
