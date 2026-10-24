from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ingestion import cli
from ingestion.config import Settings
from ingestion.courbe import CourbeError, lire_courbe
from ingestion.diagnostic import lire_diagnostic
from ingestion.tests.medallion import DBT_BIN, JOUR_BASCULE
from ingestion.tests.test_diagnostic import territoire
from ingestion.transform import PARIS_TZ

METADONNEES = (
    "Identifiant PRM;Type de donnees;Date de debut;Date de fin;Grandeur physique;"
    "Grandeur metier;Etape metier;Unite;Pas en minutes\n"
    "01234567890123;Courbe de charge;30/06/2026;01/07/2026;Energie active;Consommation;"
    "Comptage Brut;{unite};30\n"
)


# Journée du 30 juin 2026 au pas de 30 min : 2 kW, sauf 20 kW de 19 h à 21 h (pointe carbonée)
def export_enedis(unite: str = "W") -> bytes:
    debut = datetime(JOUR_BASCULE.year, JOUR_BASCULE.month, JOUR_BASCULE.day, tzinfo=PARIS_TZ)
    lignes = [METADONNEES.format(unite=unite), "Horodate;Valeur\n"]
    for n in range(1, 49):
        fin = debut + timedelta(minutes=30 * n)
        debut_pas = fin - timedelta(minutes=30)
        watts = 20_000 if 19 <= debut_pas.hour < 21 else 2_000
        valeur = watts / 1000 if unite == "kW" else watts
        lignes.append(f"{fin.isoformat()};{valeur}\n")
    return "".join(lignes).encode("utf-8")


def test_enedis_export_becomes_energy_per_interval() -> None:
    courbe = lire_courbe(export_enedis())

    assert courbe.pas_minutes == 30
    assert courbe.points[0] == (datetime(2026, 6, 29, 22, 0, tzinfo=UTC), 1.0)
    assert courbe.conso_kwh == pytest.approx(44 * 1.0 + 4 * 10.0)
    assert courbe.puissance_max_kw == pytest.approx(20)
    assert courbe.nb_jours == pytest.approx(1)
    assert courbe.conso_annuelle_mwh == pytest.approx(84 * 365 / 1000)


def test_power_in_kw_is_detected() -> None:
    assert lire_courbe(export_enedis("kW")).conso_kwh == pytest.approx(84)


@pytest.mark.parametrize("contenu", [b"date;kWh\n2026-01-01;3\n", b"Horodate;Valeur\n"])
def test_unreadable_curve_is_rejected(contenu: bytes) -> None:
    with pytest.raises(CourbeError):
        lire_courbe(contenu)


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")
def test_curve_feeds_volume_power_and_carbon(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon"), courbe=lire_courbe(export_enedis()), database=medallion_db
    )

    bilan = diagnostic.courbe
    assert bilan is not None
    assert diagnostic.conso_mwh == pytest.approx(84 * 365 / 1000, abs=0.05)
    assert diagnostic.seuil is not None and diagnostic.seuil.domaine_tension == "BT ≤ 36 kVA"
    assert bilan.part_hc == pytest.approx(16 / 84)
    assert bilan.cout_htva_eur_an is not None
    # Pointe placée sur le pic de CO₂ du réseau : la courbe émet plus que la moyenne horaire
    assert bilan.intensite_courbe_g_kwh > bilan.intensite_reseau_g_kwh


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")
def test_essentiel_reads_the_curve_file(
    medallion_db: Path,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fichier = tmp_path / "courbe.csv"
    fichier.write_bytes(export_enedis())
    monkeypatch.setattr(
        cli, "get_settings", lambda: settings.model_copy(update={"duckdb_path": medallion_db})
    )
    monkeypatch.setattr(cli, "geocode", lambda *args, **kwargs: territoire("69123", "Lyon"))
    arguments = ["essentiel", "--adresse", "Lyon", "--courbe", str(fichier), "--sans-carte"]

    assert cli.main(arguments) == 0
    assert "Votre courbe de charge : du 30/06/2026 au 30/06/2026" in capsys.readouterr().out
    assert cli.main([*arguments, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["courbe"]["puissance_max_kw"] == 20
    assert cli.main(["essentiel", "--adresse", "Lyon", "--courbe", str(tmp_path / "x.csv")]) == 1
