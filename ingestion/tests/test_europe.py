from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli
from ingestion.config import Settings
from ingestion.europe import HorsUnionEuropeenneError, lire_pays, localiser
from ingestion.extract_ember import EmberError, lire_csv, telecharger_intensites
from ingestion.extract_eurostat import aplatir_json_stat
from ingestion.geocode import AdresseIntrouvableError

NOMINATIM_URL = "https://nominatim.test/search"
EMBER_URL = "https://ember.test/yearly.csv"
ENTETE_EMBER = (
    "Area,ISO 3 code,Year,Area type,Electricity source,Generation (TWh),"
    "Emissions intensity (gCO2e/kWh),EU member"
)


def reponse_nominatim(pays: str) -> list[dict[str, Any]]:
    return [
        {
            "display_name": "Via Roma, Milan, Lombardie, Italie",
            "lat": "45.4642",
            "lon": "9.1900",
            "address": {"country_code": pays},
        }
    ]


def test_country_code_of_the_cube_is_kept() -> None:
    cube = {
        "id": ["geo", "nrg_cons", "tax", "time"],
        "size": [2, 1, 1, 1],
        "dimension": {
            "geo": {"category": {"index": {"FR": 0, "IT": 1}, "label": {}}},
            "nrg_cons": {"category": {"index": {"MWH20-499": 0}, "label": {}}},
            "tax": {"category": {"index": {"X_VAT": 0}, "label": {}}},
            "time": {"category": {"index": {"2026-S1": 0}, "label": {}}},
        },
        "value": {"0": 0.20, "1": 0.26},
        "status": {},
    }

    assert [(r["pays"], r["prix_eur_kwh"]) for r in aplatir_json_stat(cube)] == [
        ("FR", 0.20),
        ("IT", 0.26),
    ]


def test_ember_keeps_eu_countries_total_generation() -> None:
    contenu = (
        f"{ENTETE_EMBER}\n"
        "France,FRA,2025,Country or economy,Total generation,540,41.2,True\n"
        "France,FRA,2025,Country or economy,Nuclear,360,5,True\n"
        "Norway,NOR,2025,Country or economy,Total generation,150,30,False\n"
        "EU,EU,2025,Region,Total generation,2700,210,\n"
        "Malta,MLT,2025,Country or economy,Total generation,2,,True\n"
    ).encode("utf-8-sig")

    assert lire_csv(contenu) == [
        {
            "iso3": "FRA",
            "annee": "2025",
            "pays_ember": "France",
            "production_twh": 540.0,
            "intensite_g_co2e_kwh": 41.2,
        }
    ]


def test_unexpected_ember_file_is_rejected() -> None:
    with pytest.raises(EmberError, match="colonnes absentes"):
        lire_csv(b"Area,Year\nFrance,2025\n")


def test_ember_file_is_downloaded(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(
        url=EMBER_URL,
        content=f"{ENTETE_EMBER}\nItaly,ITA,2025,Country or economy,Total generation,"
        "260,284,True\n".encode(),
    )
    reglages = settings.model_copy(update={"ember_url": EMBER_URL})

    assert [ligne["iso3"] for ligne in telecharger_intensites(settings=reglages)] == ["ITA"]


def test_address_abroad_gives_its_country(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(json=reponse_nominatim("it"))
    reglages = settings.model_copy(update={"nominatim_url": NOMINATIM_URL})

    localisation = localiser("Via Roma 1, Milano", settings=reglages)

    assert localisation.code_pays == "IT"
    assert localisation.latitude == pytest.approx(45.4642)
    requete = httpx_mock.get_requests()[0]
    assert requete.url.params["accept-language"] == "fr"


def test_unknown_address_abroad_is_reported(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(json=[])
    reglages = settings.model_copy(update={"nominatim_url": NOMINATIM_URL})

    with pytest.raises(AdresseIntrouvableError):
        localiser("nulle part", settings=reglages)


def test_country_is_compared_with_france_in_the_same_band(medallion_db: Path) -> None:
    italie, france = lire_pays("IT", conso_mwh=350, database=medallion_db)

    assert italie.pays == "Italie" and italie.code_tranche == "IB"
    assert italie.prix_reference_eur_kwh == pytest.approx(0.26)
    assert (italie.premier_semestre_reference, italie.dernier_semestre) == ("2025-S2", "2026-S1")
    assert italie.intensite_g_co2e_kwh == 300 and italie.est_decarbone is False
    assert france is not None
    assert france.prix_reference_eur_kwh == pytest.approx(0.20)
    assert (france.intensite_g_co2e_kwh, france.annee_carbone) == (40, 2025)


def test_country_without_figures_stays_listed(medallion_db: Path) -> None:
    allemagne, _ = lire_pays("DE", conso_mwh=350, database=medallion_db)

    assert allemagne.prix_reference_eur_kwh is None
    assert allemagne.est_decarbone is None


def test_country_outside_the_union_is_refused(medallion_db: Path) -> None:
    with pytest.raises(HorsUnionEuropeenneError, match="GB"):
        lire_pays("GB", database=medallion_db)


def test_missing_warehouse_asks_for_a_refresh(tmp_path: Path) -> None:
    from ingestion.diagnostic import DonneesAbsentesError

    with pytest.raises(DonneesAbsentesError, match="introuvable"):
        lire_pays("IT", database=tmp_path / "absent.duckdb")


def test_europe_command_end_to_end(
    httpx_mock: HTTPXMock,
    settings: Settings,
    medallion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    httpx_mock.add_response(json=reponse_nominatim("it"))
    reglages = settings.model_copy(
        update={"nominatim_url": NOMINATIM_URL, "duckdb_path": medallion_db}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    code = cli.main(["europe", "--adresse", "Via Roma 1, Milano", "--conso", "350"])

    sortie = capsys.readouterr().out
    assert code == 0
    assert "Pays : Italie (IT)" in sortie
    assert "0,2600 €/kWh HTVA pour la tranche IB" in sortie
    assert "(+30 % par rapport à la France)" in sortie
    assert "Électricité décarbonée : non, 300 gCO₂e/kWh" in sortie
    assert "France : 40 selon Ember" in sortie


def test_europe_command_refuses_a_country_outside_the_union(
    httpx_mock: HTTPXMock,
    settings: Settings,
    medallion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    httpx_mock.add_response(json=reponse_nominatim("gb"))
    reglages = settings.model_copy(
        update={"nominatim_url": NOMINATIM_URL, "duckdb_path": medallion_db}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    assert cli.main(["europe", "--adresse", "London"]) == 1
