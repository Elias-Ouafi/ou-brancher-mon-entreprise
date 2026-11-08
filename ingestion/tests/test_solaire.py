from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_solaire,
    essentiel_en_dict,
    formater_diagnostic,
    lignes_obligations_solaires,
    lire_diagnostic,
)
from ingestion.solaire import (
    Batiment,
    Productible,
    SolaireError,
    lire_batiment,
    lire_batiments,
    lire_productible,
    lire_productible_pvgis,
    part_toiture,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

WFS_URL = "https://wfs.test/ows"
PVGIS_URL = "https://pvgis.test/PVcalc"
LAT, LON = 45.75, 4.85
COS = math.cos(math.radians(LAT))
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


# Carré de `cote_m` mètres dont le coin sud-ouest est à (est_m, nord_m) de l'adresse, avec altitude
def batiment(usage: str, est_m: float, nord_m: float, cote_m: float) -> dict[str, Any]:
    x0 = LON + est_m / 1000 / (111.32 * COS)
    y0 = LAT + nord_m / 1000 / 111.32
    dx, dy = cote_m / 1000 / (111.32 * COS), cote_m / 1000 / 111.32
    anneau = [[x0, y0, 5], [x0 + dx, y0, 5], [x0 + dx, y0 + dy, 5], [x0, y0 + dy, 5], [x0, y0, 5]]
    return {
        "type": "Feature",
        "properties": {"usage_1": usage, "nature": "Industriel", "hauteur": 8.0},
        "geometry": {"type": "MultiPolygon", "coordinates": [[anneau]]},
    }


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


PVGIS = {
    "inputs": {"mounting_system": {"fixed": {"slope": {"value": 38}, "azimuth": {"value": -4}}}},
    "outputs": {"totals": {"fixed": {"E_y": 1153.12}}},
}


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"geoplateforme_wfs_url": WFS_URL, "pvgis_url": PVGIS_URL})


def test_building_under_the_address_wins_over_a_nearer_edge() -> None:
    b = lire_batiments(
        collection(batiment("Commercial", 30, 0, 20), batiment("Industriel", -50, -50, 100)),
        latitude=LAT,
        longitude=LON,
    )

    assert b is not None and b.usage == "Industriel" and b.contient_adresse
    assert b.emprise_m2 == pytest.approx(10_000, rel=0.01)
    assert b.hauteur_m == 8.0


def test_nearest_building_within_sixty_metres_otherwise_none() -> None:
    proche = lire_batiments(
        collection(batiment("Commercial", 30, 0, 20)), latitude=LAT, longitude=LON
    )
    assert proche is not None and not proche.contient_adresse
    assert proche.distance_m == pytest.approx(30, rel=0.02)
    assert (
        lire_batiments(collection(batiment("X", 200, 0, 20)), latitude=LAT, longitude=LON) is None
    )


def test_unexpected_payloads_are_refused() -> None:
    with pytest.raises(SolaireError):
        lire_batiments({}, latitude=LAT, longitude=LON)
    with pytest.raises(SolaireError):
        lire_productible({"outputs": {}})


def test_pvgis_yield_and_angles_are_read() -> None:
    assert lire_productible(PVGIS) == Productible(1153.12, 38, -4)


def test_queries_ask_a_one_kwc_system_and_a_box_around_the_address(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(url=re.compile("^" + re.escape(PVGIS_URL)), json=PVGIS)
    httpx_mock.add_response(
        url=re.compile("^" + re.escape(WFS_URL)), json=collection(batiment("Industriel", 0, 0, 10))
    )

    assert lire_productible_pvgis(latitude=LAT, longitude=LON, settings=reglages).kwh_par_kwc_an
    assert lire_batiment(latitude=LAT, longitude=LON, settings=reglages) is not None

    pvgis, wfs = (r.url.params for r in httpx_mock.get_requests())
    assert (pvgis["peakpower"], pvgis["loss"], pvgis["optimalangles"]) == ("1", "14", "1")
    assert wfs["typeNames"] == "BDTOPO_V3:batiment" and "BBOX(geometrie" in wfs["cql_filter"]


def test_dropped_connections_are_retried(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))
    httpx_mock.add_response(json=PVGIS)
    assert lire_productible_pvgis(latitude=LAT, longitude=LON, settings=reglages)


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_response(status_code=400, is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_solaire(diagnostic, settings=reglages)

    assert diagnostic.productible is None and diagnostic.solaire_erreur
    assert "Bâtiment et solaire non évalués" in formater_diagnostic(diagnostic)


@avec_dbt
def test_diagnosis_gives_the_building_and_the_yield(medallion_db: Path) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    b = Batiment("Industriel", "Industriel", 4200.0, 9.0, True, 0.0)
    avec = replace(
        base, batiment=b, productible=Productible(1153.12, 38, -4), solaire_consulte=True
    )

    texte = formater_diagnostic(avec)
    assert "bâtiment industriel sur l'adresse (4 200 m² au sol, 9,0 m de haut)" in texte
    assert "productible de 1 153 kWh par kWc et par an" in texte
    assert "inclinaison 38° optimale, pertes de 14 %, PVGIS" in texte
    assert essentiel_en_dict(avec)["solaire"]["productible"]["kwh_par_kwc_an"] == 1153.12
    assert "solaire" in essentiel_en_dict(avec)["limites"]

    sans = replace(avec, batiment=None)
    assert "aucun bâtiment de la BD TOPO à moins de 60 m" in formater_diagnostic(sans)


def test_roof_share_follows_the_dates_of_the_code() -> None:
    assert part_toiture(date(2026, 11, 8)) == (40, (date(2027, 7, 1), 50))
    assert part_toiture(date(2027, 7, 1)) == (50, None)
    assert part_toiture(date(2024, 1, 1)) == (30, (date(2026, 7, 1), 40))


def obligations(batiment: Batiment | None, departement: str = "91") -> list[str]:
    return lignes_obligations_solaires(
        batiment,
        code_departement=departement,
        solaire_consulte=True,
        aujourd_hui=date(2026, 11, 8),
    )


def test_obligations_name_the_roof_share_and_the_existing_building() -> None:
    neuf, existant, parking = obligations(Batiment("Industriel", None, 4200.0, 9.0, True, 0.0))
    assert "40 % de la toiture au moins, 50 % à compter du 1er juillet 2027" in neuf
    assert "4 200 m² au sol : même obligation au 1er janvier 2028" in existant
    assert "plus de 1 500 m²" in parking and "1er juillet 2026 dès 10 000 m²" in parking

    assert (
        "sous le seuil de 500 m²"
        in obligations(Batiment("Industriel", None, 300.0, 6.0, True, 0))[1]
    )
    assert "hors de la liste" in obligations(Batiment("Résidentiel", None, 900.0, 9.0, True, 0))[1]
    assert "usage inconnu" in obligations(Batiment("Indifférencié", None, 900.0, 9.0, True, 0))[1]
    assert "non identifié" in obligations(None)[1]


def test_parking_threshold_depends_on_the_overseas_department() -> None:
    assert "plus de 1 000 m²" in obligations(None, "974")[-1]
    assert "plus de 2 500 m²" in obligations(None, "973")[-1]
    assert "seuil non fixé" in obligations(None, "976")[-1]
    # Sans appel à la BD TOPO, pas de ligne sur le bâtiment existant
    hors_ligne = lignes_obligations_solaires(None, code_departement="2A", solaire_consulte=False)
    assert len(hors_ligne) == 2 and "plus de 1 500 m²" in hors_ligne[-1]


@avec_dbt
def test_diagnosis_lists_the_solar_obligations(medallion_db: Path) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    b = Batiment("Industriel", "Industriel", 4200.0, 9.0, True, 0.0)
    avec = replace(base, batiment=b, productible=Productible(1153.0, 38, -4), solaire_consulte=True)

    texte = formater_diagnostic(avec)
    assert "Obligation solaire : bâtiment neuf, extension ou rénovation lourde" in texte
    assert "(CCH L171-5)" in texte and "(loi APER, art. 40" in texte
    assert len(essentiel_en_dict(avec)["obligations_solaires"]) == 3
    assert "Obligation solaire : bâtiment existant" not in formater_diagnostic(base)
