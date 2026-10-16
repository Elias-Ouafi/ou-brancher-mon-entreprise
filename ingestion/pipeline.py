from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ingestion.config import Settings, get_settings
from ingestion.datasets import (
    BRONZE_SPECS,
    EUROSTAT_PRIX_ENTREPRISES,
    NATIONAL_TR,
    RTE_TEMPO,
    DatasetSpec,
)
from ingestion.extract_eurostat import EurostatClient
from ingestion.extract_odre import ExtractionWindow, OdreClient
from ingestion.extract_rte import DayRange, RteTempoClient
from ingestion.transform import PARIS_TZ, records_to_table, write_parquet
from ingestion.warehouse import LoadResult, build_loader

logger = logging.getLogger(__name__)

# Colonne témoin du contrôle de fraîcheur
FRESHNESS_MEASURE = "consommation"

# Pas de publication des datasets éCO2mix
SLOT_MINUTES = 15


# La donnée la plus récente du warehouse dépasse le retard toléré
class StaleDataError(RuntimeError):
    pass


# Le dernier mois consolidé disponible comporte trop de pas de temps manquants
class IncompleteMonthError(RuntimeError):
    pass


# Résultat d'une extraction, journalisé par la ligne de commande
@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    dataset_id: str
    parquet_path: str
    rows: int
    api_calls: int
    # Bornes de la fenêtre, absentes lors d'une extraction intégrale
    window_start: str | None
    window_end: str | None


# Écart entre la donnée la plus récente et l'instant du contrôle
@dataclass(frozen=True, slots=True)
class FreshnessReport:
    # Dernier horodatage effectivement mesuré : c'est lui qui décide
    latest: datetime | None
    # Dernier horodatage présent, mesuré ou non
    latest_row: datetime | None
    lag_hours: float | None
    max_lag_hours: int
    row_count: int

    @property
    def is_fresh(self) -> bool:
        return self.lag_hours is not None and self.lag_hours <= self.max_lag_hours


# Complétude d'un mois civil dans la table consolidée
@dataclass(frozen=True, slots=True)
class CoverageReport:
    month: str
    expected_slots: int
    actual_slots: int
    min_coverage: float

    @property
    def coverage(self) -> float:
        return self.actual_slots / self.expected_slots if self.expected_slots else 0.0

    @property
    def is_complete(self) -> bool:
        return self.coverage >= self.min_coverage

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "month": self.month,
            "expected_slots": self.expected_slots,
            "actual_slots": self.actual_slots,
            "coverage": round(self.coverage, 4),
        }


# Bornes UTC `[début, fin)` d'un mois civil français
def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=PARIS_TZ)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = datetime(next_year, next_month, 1, tzinfo=PARIS_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


# Fenêtre couvrant les `months` derniers mois civils, jusqu'à maintenant
def last_months_window(months: int, *, now: datetime | None = None) -> ExtractionWindow:
    reference = (now or datetime.now(UTC)).astimezone(PARIS_TZ)
    shifted = (reference.year * 12 + reference.month - 1) - months
    start, _ = month_bounds(shifted // 12, shifted % 12 + 1)
    return ExtractionWindow(start=start, end=reference.astimezone(UTC))


def _expected_slots(start: datetime, end: datetime) -> int:
    return int((end - start) / timedelta(minutes=SLOT_MINUTES))


# Extrait un dataset ODRÉ et l'écrit en Parquet typé
def extract_to_parquet(
    *,
    settings: Settings | None = None,
    spec: DatasetSpec = NATIONAL_TR,
    window: ExtractionWindow | None = None,
    run_id: str = "manual",
    now: datetime | None = None,
) -> ExtractionOutcome:
    settings = settings or get_settings()
    ingested_at = (now or datetime.now(UTC)).astimezone(UTC)

    if spec.is_time_series:
        window = window or ExtractionWindow.last_hours(settings.lookback_hours, now=ingested_at)
    elif window is not None:
        raise ValueError(
            f"{spec.dataset_id} n'a pas de dimension temporelle : fenetre inutilisable"
        )

    with OdreClient(settings, dataset_id=spec.dataset_id) as client:
        if window is None:
            # Tri sur la première colonne de clé : `order_by` sur `date_heure` provoquerait une 400
            records = client.fetch_all(order_by=spec.key_names[0])
        else:
            records = client.fetch_window(window)
        api_calls = client.calls.count

    if not records:
        logger.warning(
            "aucun enregistrement publie (%s, %s)", spec.dataset_id, window or "integral"
        )

    table = records_to_table(records, spec=spec, ingested_at=ingested_at)
    path = write_parquet(
        table,
        dataset_dir=spec.bronze_dataset_dir(settings.bronze_dir),
        run_id=run_id,
        ingest_date=ingested_at.date(),
    )
    return ExtractionOutcome(
        dataset_id=spec.dataset_id,
        parquet_path=str(path),
        rows=table.num_rows,
        api_calls=api_calls,
        window_start=None if window is None else window.start.isoformat(),
        window_end=None if window is None else window.end.isoformat(),
    )


# Jours Tempo utiles au rapport d'un mois, veille du 1er comprise
def tempo_days_for_month(month: date) -> DayRange:
    days = DayRange.month(month.year, month.month)
    return DayRange(days.start - timedelta(days=1), days.end)


# Extrait le calendrier Tempo d'une plage de jours vers un Parquet bronze
def extract_tempo_to_parquet(
    days: DayRange,
    *,
    settings: Settings | None = None,
    run_id: str = "manual",
    now: datetime | None = None,
) -> ExtractionOutcome:
    settings = settings or get_settings()
    ingested_at = (now or datetime.now(UTC)).astimezone(UTC)

    with RteTempoClient(settings) as client:
        records = client.fetch_calendar(days, today=ingested_at.astimezone(PARIS_TZ).date())
        api_calls = client.calls

    if not records:
        logger.warning("aucune couleur Tempo publiee sur %s", days)

    table = records_to_table(records, spec=RTE_TEMPO, ingested_at=ingested_at)
    path = write_parquet(
        table,
        dataset_dir=RTE_TEMPO.bronze_dataset_dir(settings.bronze_dir),
        run_id=run_id,
        ingest_date=ingested_at.date(),
    )
    return ExtractionOutcome(
        dataset_id=RTE_TEMPO.dataset_id,
        parquet_path=str(path),
        rows=table.num_rows,
        api_calls=api_calls,
        window_start=days.start.isoformat(),
        window_end=days.end.isoformat(),
    )


# Crée, vides, les tables bronze de tous les datasets connus
def ensure_bronze_tables(*, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    for spec in BRONZE_SPECS:
        loader = build_loader(settings, spec=spec)
        try:
            loader.ensure_table()
        finally:
            loader.close()


# Fusionne un Parquet dans la table bronze du dataset, sur la clé `date_heure`
def load_to_warehouse(
    parquet_path: Path | str,
    *,
    settings: Settings | None = None,
    spec: DatasetSpec = NATIONAL_TR,
) -> LoadResult:
    settings = settings or get_settings()
    ensure_bronze_tables(settings=settings)
    loader = build_loader(settings, spec=spec)
    try:
        return loader.merge_parquet(parquet_path)
    finally:
        loader.close()


# Vérifie le retard de la donnée la plus récente, en lecture seule
def check_freshness(
    *,
    settings: Settings | None = None,
    spec: DatasetSpec = NATIONAL_TR,
    now: datetime | None = None,
) -> FreshnessReport:
    settings = settings or get_settings()
    reference = (now or datetime.now(UTC)).astimezone(UTC)

    # La lecture seule évite de prendre le verrou d'écriture unique de DuckDB
    loader = build_loader(settings, spec=spec, read_only=True)
    try:
        latest = loader.latest_timestamp(measure=FRESHNESS_MEASURE)
        latest_row = loader.latest_timestamp()
        row_count = loader.row_count()
    finally:
        loader.close()

    lag = None if latest is None else (reference - latest) / timedelta(hours=1)
    report = FreshnessReport(
        latest=latest,
        latest_row=latest_row,
        lag_hours=None if lag is None else round(lag, 3),
        max_lag_hours=settings.freshness_max_lag_hours,
        row_count=row_count,
    )

    if latest is None:
        raise StaleDataError(
            "le warehouse ne contient aucune donnee"
            if row_count == 0
            else f"{row_count} ligne(s) presentes mais aucune valeur de {FRESHNESS_MEASURE}"
        )
    if not report.is_fresh:
        raise StaleDataError(
            f"donnee la plus recente {latest.isoformat()} "
            f"(retard {report.lag_hours} h > {report.max_lag_hours} h tolerees)"
        )

    logger.info(
        "freshness ok latest_measured=%s latest_row=%s lag_hours=%s rows=%d",
        latest.isoformat(),
        None if latest_row is None else latest_row.isoformat(),
        report.lag_hours,
        row_count,
    )
    return report


# Vérifie qu'un mois consolidé est complet, en lecture seule
def check_month_coverage(
    *,
    settings: Settings | None = None,
    spec: DatasetSpec,
    now: datetime | None = None,
) -> CoverageReport:
    settings = settings or get_settings()
    reference = (now or datetime.now(UTC)).astimezone(PARIS_TZ)

    loader = build_loader(settings, spec=spec, read_only=True)
    try:
        latest = loader.latest_timestamp()
        if latest is None:
            raise IncompleteMonthError(f"{spec.table_name} ne contient aucune donnee")

        year, month = _target_month(latest, reference)
        start, end = month_bounds(year, month)
        actual = loader.count_distinct_instants(start=start, end=end)
    finally:
        loader.close()

    report = CoverageReport(
        month=f"{year:04d}-{month:02d}",
        expected_slots=_expected_slots(start, end),
        actual_slots=actual,
        min_coverage=settings.consolidation_min_coverage,
    )

    if not report.is_complete:
        raise IncompleteMonthError(
            f"mois {report.month} incomplet : {report.actual_slots}/{report.expected_slots} "
            f"pas de temps ({report.coverage:.1%} < {report.min_coverage:.0%})"
        )

    logger.info("coverage ok %s", report.as_dict())
    return report


# Dernier mois civil révolu présent en base, exprimé en heure de Paris
def _target_month(latest: datetime, reference: datetime) -> tuple[int, int]:
    local = latest.astimezone(PARIS_TZ)
    year, month = local.year, local.month
    if (year, month) == (reference.year, reference.month):
        # Le mois courant n'est pas terminé : on juge le précédent
        previous = (year * 12 + month - 1) - 1
        year, month = previous // 12, previous % 12 + 1
    return year, month


# Extraction puis chargement, pour un rejeu manuel en une commande
def run_ingestion(
    *,
    settings: Settings | None = None,
    spec: DatasetSpec = NATIONAL_TR,
    window: ExtractionWindow | None = None,
    run_id: str = "manual",
    now: datetime | None = None,
) -> tuple[ExtractionOutcome, LoadResult]:
    settings = settings or get_settings()
    outcome = extract_to_parquet(
        settings=settings, spec=spec, window=window, run_id=run_id, now=now
    )
    result = load_to_warehouse(outcome.parquet_path, settings=settings, spec=spec)
    return outcome, result


# Calendrier Tempo : extraction puis chargement dans `bronze.rte_tempo`
def run_tempo_ingestion(
    days: DayRange,
    *,
    settings: Settings | None = None,
    run_id: str = "manual",
    now: datetime | None = None,
) -> tuple[ExtractionOutcome, LoadResult]:
    settings = settings or get_settings()
    outcome = extract_tempo_to_parquet(days, settings=settings, run_id=run_id, now=now)
    result = load_to_warehouse(outcome.parquet_path, settings=settings, spec=RTE_TEMPO)
    return outcome, result


# Prix Eurostat des entreprises : extraction intégrale (un appel), puis MERGE
def run_prix_entreprises_ingestion(
    *,
    settings: Settings | None = None,
    run_id: str = "manual",
    now: datetime | None = None,
) -> tuple[ExtractionOutcome, LoadResult]:
    settings = settings or get_settings()
    ingested_at = (now or datetime.now(UTC)).astimezone(UTC)
    with EurostatClient(settings) as client:
        records = client.fetch_prix_entreprises()
        api_calls = client.calls
    table = records_to_table(records, spec=EUROSTAT_PRIX_ENTREPRISES, ingested_at=ingested_at)
    path = write_parquet(
        table,
        dataset_dir=EUROSTAT_PRIX_ENTREPRISES.bronze_dataset_dir(settings.bronze_dir),
        run_id=run_id,
        ingest_date=ingested_at.date(),
    )
    outcome = ExtractionOutcome(
        dataset_id=EUROSTAT_PRIX_ENTREPRISES.dataset_id,
        parquet_path=str(path),
        rows=table.num_rows,
        api_calls=api_calls,
        window_start=None,
        window_end=None,
    )
    result = load_to_warehouse(path, settings=settings, spec=EUROSTAT_PRIX_ENTREPRISES)
    return outcome, result


# Relit les N derniers mois consolidés et les fusionne
def run_consolidation(
    *,
    settings: Settings | None = None,
    spec: DatasetSpec,
    months: int | None = None,
    run_id: str = "manual",
    now: datetime | None = None,
) -> tuple[ExtractionOutcome, LoadResult]:
    settings = settings or get_settings()
    months = months if months is not None else settings.consolidation_lookback_months
    window = last_months_window(months, now=now)
    return run_ingestion(settings=settings, spec=spec, window=window, run_id=run_id, now=now)
