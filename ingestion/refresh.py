from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ingestion.config import Settings, get_settings
from ingestion.datasets import NATIONAL_CONS_DEF, NATIONAL_TR, TERRITORIAL_SPECS
from ingestion.extract_geo import run_geo_ingestion
from ingestion.pipeline import (
    run_consolidation,
    run_continuite_ingestion,
    run_edf_sei_ingestion,
    run_enedis_entreprises_ingestion,
    run_europe_ingestion,
    run_fret_ingestion,
    run_gaspar_ingestion,
    run_ingestion,
    run_locaux_ingestion,
    run_prix_entreprises_ingestion,
    run_production_ingestion,
    run_zonage_sismique_ingestion,
)

logger = logging.getLogger(__name__)

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


@dataclass(frozen=True, slots=True)
class Etape:
    nom: str
    erreur: str | None = None

    @property
    def reussie(self) -> bool:
        return self.erreur is None


# dbt n'a besoin d'aucun identifiant : on ne lui transmet pas ceux de RTE
def _env_sans_secrets() -> dict[str, str]:
    return {cle: valeur for cle, valeur in os.environ.items() if not cle.startswith("ECO2MIX_RTE_")}


def dbt_build(settings: Settings) -> None:
    dbt = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt")
    if dbt is None:
        raise RuntimeError("dbt introuvable (uv sync --group dbt)")
    resultat = subprocess.run(
        [dbt, "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)],
        env={
            **_env_sans_secrets(),
            "ECO2MIX_DUCKDB_PATH": str(Path(settings.duckdb_path).resolve()),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if resultat.returncode != 0:
        derniere_ligne = (resultat.stdout.strip().splitlines() or ["sans sortie"])[-1]
        raise RuntimeError(f"dbt build en echec : {derniere_ligne}")


# Toutes les extractions, puis dbt ; une étape en échec n'arrête pas les suivantes
def run_refresh(
    *, settings: Settings | None = None, months: int | None = None, run_id: str = "refresh"
) -> list[Etape]:
    settings = settings or get_settings()
    etapes: list[tuple[str, Callable[[], object]]] = [
        (
            NATIONAL_CONS_DEF.dataset_id,
            lambda: run_consolidation(
                settings=settings, spec=NATIONAL_CONS_DEF, months=months, run_id=run_id
            ),
        ),
        (
            NATIONAL_TR.dataset_id,
            lambda: run_ingestion(settings=settings, spec=NATIONAL_TR, run_id=run_id),
        ),
        *[
            (
                spec.dataset_id,
                lambda spec=spec: run_ingestion(settings=settings, spec=spec, run_id=run_id),
            )
            for spec in TERRITORIAL_SPECS
        ],
        ("carte-geo", lambda: run_geo_ingestion(settings=settings, run_id=run_id)),
        (
            "prix-eurostat",
            lambda: run_prix_entreprises_ingestion(settings=settings, run_id=run_id),
        ),
        ("risques-gaspar", lambda: run_gaspar_ingestion(settings=settings, run_id=run_id)),
        (
            "zonage-sismique",
            lambda: run_zonage_sismique_ingestion(settings=settings, run_id=run_id),
        ),
        ("carbone-iles", lambda: run_edf_sei_ingestion(settings=settings, run_id=run_id)),
        ("production", lambda: run_production_ingestion(settings=settings, run_id=run_id)),
        ("fret", lambda: run_fret_ingestion(settings=settings, run_id=run_id)),
        ("locaux", lambda: run_locaux_ingestion(settings=settings, run_id=run_id)),
        ("europe", lambda: run_europe_ingestion(settings=settings, run_id=run_id)),
        (
            "entreprises-enedis",
            lambda: run_enedis_entreprises_ingestion(settings=settings, run_id=run_id),
        ),
        (
            "continuite-enedis",
            lambda: run_continuite_ingestion(settings=settings, run_id=run_id),
        ),
        ("dbt-build", lambda: dbt_build(settings)),
    ]
    bilan = []
    for nom, executer in etapes:
        try:
            executer()
        except Exception as error:
            logger.error("refresh %s en echec : %s", nom, error)
            bilan.append(Etape(nom, str(error) or type(error).__name__))
        else:
            logger.info("refresh %s ok", nom)
            bilan.append(Etape(nom))
    return bilan
