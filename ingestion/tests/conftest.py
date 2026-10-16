from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ingestion.config import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TEST_BASE_URL = "https://odre.test/api/explore/v2.1"


# Neutralise toute variable ECO2MIX_* de la machine hôte
@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [k for k in os.environ if k.startswith("ECO2MIX_")]:
        monkeypatch.delenv(key, raising=False)


# Réglages de test : hôte factice, aucune attente entre deux tentatives
@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        api_base_url=TEST_BASE_URL,
        dataset_id="eco2mix-national-tr",
        retry_wait_seconds=0.0,
        bronze_dir=tmp_path / "bronze",
        duckdb_path=tmp_path / "warehouse" / "eco2mix.duckdb",
    )


# Réponse `/records` réaliste (2 enregistrements)
@pytest.fixture
def records_payload() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "records_page.json").read_text(encoding="utf-8"))


# Réponse `/exports/csv` réaliste (séparateur `;`, une valeur manquante)
@pytest.fixture
def export_csv() -> str:
    return (FIXTURES_DIR / "export_sample.csv").read_text(encoding="utf-8")


@pytest.fixture
def records_url() -> str:
    return f"{TEST_BASE_URL}/catalog/datasets/eco2mix-national-tr/records"


@pytest.fixture
def export_url() -> str:
    return f"{TEST_BASE_URL}/catalog/datasets/eco2mix-national-tr/exports/csv"


# Base DuckDB de bronze à gold (voir `medallion.py`), construite une fois par session
@pytest.fixture(scope="session")
def medallion_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from ingestion.tests import medallion

    if medallion.DBT_BIN is None:
        pytest.skip("dbt non installé (uv sync --group dbt)")
    return medallion.build_warehouse(tmp_path_factory.mktemp("medaillon"))


# Garde-fou explicite : toute tentative de connexion réelle échoue bruyamment
@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import socket

    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("appel réseau réel interdit dans la suite de tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    yield
