from __future__ import annotations

from typing import Any

import pytest

from ingestion import cli, refresh
from ingestion.config import Settings
from ingestion.datasets import TERRITORIAL_SPECS


@pytest.fixture
def appels(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    journal: list[str] = []

    def consolidation(*, spec: Any, months: int | None, **_: Any) -> None:
        journal.append(f"{spec.dataset_id}:{months}")

    def ingestion(*, spec: Any, **_: Any) -> None:
        if spec.dataset_id == "postes-electriques-rte":
            raise ConnectionError("ODRÉ injoignable")
        journal.append(spec.dataset_id)

    monkeypatch.setattr(refresh, "run_consolidation", consolidation)
    monkeypatch.setattr(refresh, "run_ingestion", ingestion)
    monkeypatch.setattr(
        refresh, "run_prix_entreprises_ingestion", lambda **_: journal.append("prix")
    )
    monkeypatch.setattr(refresh, "run_geo_ingestion", lambda **_: journal.append("geo"))
    monkeypatch.setattr(refresh, "run_gaspar_ingestion", lambda **_: journal.append("gaspar"))
    monkeypatch.setattr(
        refresh, "run_zonage_sismique_ingestion", lambda **_: journal.append("sismique")
    )
    monkeypatch.setattr(refresh, "run_edf_sei_ingestion", lambda **_: journal.append("edf-sei"))
    monkeypatch.setattr(
        refresh, "run_production_ingestion", lambda **_: journal.append("production")
    )
    monkeypatch.setattr(refresh, "run_fret_ingestion", lambda **_: journal.append("fret"))
    monkeypatch.setattr(refresh, "run_europe_ingestion", lambda **_: journal.append("europe"))
    monkeypatch.setattr(
        refresh, "run_enedis_entreprises_ingestion", lambda **_: journal.append("entreprises")
    )
    monkeypatch.setattr(
        refresh, "run_continuite_ingestion", lambda **_: journal.append("continuite")
    )
    monkeypatch.setattr(refresh, "dbt_build", lambda settings: journal.append("dbt"))
    return journal


def test_toutes_les_sources_puis_dbt_malgre_une_panne(
    appels: list[str], settings: Settings
) -> None:
    bilan = refresh.run_refresh(settings=settings, months=180)

    attendus = len(TERRITORIAL_SPECS) + 13
    assert len(bilan) == attendus
    assert appels[0] == "eco2mix-national-cons-def:180"
    assert appels[1] == "eco2mix-national-tr"
    assert appels[-9:] == [
        "gaspar", "sismique", "edf-sei", "production", "fret", "europe", "entreprises",
        "continuite", "dbt",
    ]  # fmt: skip
    assert [e.nom for e in bilan if not e.reussie] == ["postes-electriques-rte"]
    assert bilan[-1].reussie


def test_la_commande_signale_l_echec(
    appels: list[str], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["refresh"]) == 1
    assert "eco2mix-national-cons-def:None" in appels


def test_dbt_introuvable(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(refresh.shutil, "which", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="dbt introuvable"):
        refresh.dbt_build(settings)
