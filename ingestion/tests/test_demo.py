from __future__ import annotations

from pathlib import Path

import pytest

from ingestion import cli, demo
from ingestion.tests import medallion
from reporting import carte


def test_demo_repond_sur_la_base_synthetique(
    medallion_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    settings = demo.settings_demo(tmp_path).model_copy(update={"duckdb_path": medallion_db})
    appels: list[dict] = []
    monkeypatch.setattr(demo, "preparer_base", lambda: settings)
    monkeypatch.setattr(
        carte, "mettre_a_jour_carte", lambda **kwargs: appels.append(kwargs) or Path("c.html")
    )

    assert cli.main(["demo", "--sans-ouvrir"]) == 0
    sortie = capsys.readouterr().out
    assert sortie.startswith("DÉMONSTRATION : base synthétique des tests, chiffres fictifs")
    assert "Place Bellecour 69002 Lyon" in sortie
    assert "pour 15 000 MWh (tranche ID" in sortie
    assert "Brancher ici ?" in sortie
    assert appels[0]["territoire"] == demo.TERRITOIRE
    assert appels[0]["settings"] is settings


def test_base_de_demo_relue_sans_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "eco2mix.duckdb").write_bytes(b"")

    def interdit(*args: object) -> None:
        raise AssertionError("base déjà construite")

    monkeypatch.setattr(medallion, "build_warehouse", interdit)
    assert demo.preparer_base(tmp_path).duckdb_path == tmp_path / "eco2mix.duckdb"


def test_base_de_demo_a_moitie_construite_effacee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def en_echec(dossier: Path) -> None:
        (dossier / "eco2mix.duckdb").write_bytes(b"partiel")
        raise AssertionError("dbt build en échec")

    monkeypatch.setattr(medallion, "DBT_BIN", "dbt")
    monkeypatch.setattr(medallion, "build_warehouse", en_echec)
    with pytest.raises(AssertionError):
        demo.preparer_base(tmp_path / "demo")
    assert not (tmp_path / "demo" / "eco2mix.duckdb").exists()
