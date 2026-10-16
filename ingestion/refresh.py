"""Rafraîchissement complet des données du diagnostic, en une commande.

Enchaîne les extractions (éCO2mix consolidé puis temps réel, jeux territoriaux, prix
Eurostat), puis `dbt build`. C'est l'exploitation du MVP, sans Airflow : une tâche
planifiée (planificateur de tâches Windows, cron) ou le workflow GitHub Actions
`refresh.yml` suffit pour une dizaine d'appels d'API.

Une source en échec n'arrête pas les autres : son ancienne version reste en bronze, et
dbt reconstruit silver et gold avec ce qui est disponible. Le code de sortie signale
l'échec pour que la tâche planifiée le remonte.

Le calendrier Tempo n'en fait pas partie : il exige un compte RTE (commande `tempo`).
"""

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
from ingestion.pipeline import (
    run_consolidation,
    run_ingestion,
    run_prix_entreprises_ingestion,
)

logger = logging.getLogger(__name__)

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


@dataclass(frozen=True, slots=True)
class Etape:
    nom: str
    #: Message d'erreur, `None` si l'étape a réussi.
    erreur: str | None = None

    @property
    def reussie(self) -> bool:
        return self.erreur is None


def dbt_build(settings: Settings) -> None:
    """`dbt build` sur le warehouse configuré ; lève une erreur si un nœud échoue."""
    dbt = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt")
    if dbt is None:
        raise RuntimeError("dbt introuvable (uv sync --group dbt)")
    resultat = subprocess.run(
        [dbt, "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)],
        env={**os.environ, "ECO2MIX_DUCKDB_PATH": str(Path(settings.duckdb_path).resolve())},
        capture_output=True,
        text=True,
        check=False,
    )
    if resultat.returncode != 0:
        derniere_ligne = (resultat.stdout.strip().splitlines() or ["sans sortie"])[-1]
        raise RuntimeError(f"dbt build en echec : {derniere_ligne}")


def run_refresh(
    *, settings: Settings | None = None, months: int | None = None, run_id: str = "refresh"
) -> list[Etape]:
    """Toutes les extractions, puis dbt ; une étape en échec n'arrête pas les suivantes.

    `months` : profondeur relue dans l'éCO2mix consolidé (180 pour un premier chargement,
    depuis 2012 ; par défaut, la fenêtre de consolidation des réglages).
    """
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
        (
            "prix-eurostat",
            lambda: run_prix_entreprises_ingestion(settings=settings, run_id=run_id),
        ),
        ("dbt-build", lambda: dbt_build(settings)),
    ]
    bilan = []
    for nom, executer in etapes:
        try:
            executer()
        except Exception as error:  # une source en panne n'arrête pas les autres
            logger.error("refresh %s en echec : %s", nom, error)
            bilan.append(Etape(nom, str(error) or type(error).__name__))
        else:
            logger.info("refresh %s ok", nom)
            bilan.append(Etape(nom))
    return bilan
