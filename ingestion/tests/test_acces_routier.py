from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.acces_routier import (
    Acces,
    AccesRoutierError,
    lire_acces,
    lire_autoroute,
)
from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_acces_routier,
    essentiel_en_dict,
    formater_diagnostic,
    lire_diagnostic,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

WFS_URL = "https://wfs.test/ows"
ITINERAIRE_URL = "https://route.test/itineraire"
LAT, LON = 45.75, 4.85
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def troncon(numero: str, est_deg: float) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"cpx_numero": numero},
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [[[LON + est_deg, LAT], [LON + est_deg, LAT + 0.01]]],
        },
    }


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"geoplateforme_wfs_url": WFS_URL, "itineraire_url": ITINERAIRE_URL}
    )


def test_nearest_motorway_section_gives_its_route_and_distance() -> None:
    nom, distance = lire_autoroute(
        collection(troncon("A7", 0.05), troncon("A46", 0.01)), latitude=LAT, longitude=LON
    )

    assert nom == "A46"
    assert distance == pytest.approx(0.78, abs=0.02)
    assert lire_autoroute(collection(), latitude=LAT, longitude=LON) is None


def test_unexpected_payload_is_refused() -> None:
    with pytest.raises(AccesRoutierError):
        lire_autoroute({}, latitude=LAT, longitude=LON)


def test_motorway_distance_and_car_times_to_the_given_poles(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(
        url=re.compile("^" + re.escape(WFS_URL)), json=collection(troncon("A46", 0.01))
    )
    httpx_mock.add_response(
        url=re.compile("^" + re.escape(ITINERAIRE_URL)), json={"distance": 12.4, "duration": 17.6}
    )

    acces = lire_acces(
        latitude=LAT,
        longitude=LON,
        poles=[("aeroport", "Lyon-Saint-Exupéry", 45.72, 5.08)],
        settings=reglages,
    )

    assert [a.type_acces for a in acces] == ["autoroute", "aeroport"]
    assert acces[0].duree_min is None
    assert (acces[1].distance_route_km, acces[1].duree_min) == (12.4, 17.6)
    params = httpx_mock.get_requests()[1].url.params
    assert params["profile"] == "car" and params["resource"] == "bdtopo-pgr"
    assert params["end"] == "5.08,45.72"
    cql = httpx_mock.get_requests()[0].url.params["cql_filter"]
    assert "nature='Type autoroutier'" in cql and "LIKE 'Autoroute%'" in cql


def test_a_failed_route_keeps_the_straight_line_distance(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(url=re.compile("^" + re.escape(WFS_URL)), json=collection())
    httpx_mock.add_response(url=re.compile("^" + re.escape(ITINERAIRE_URL)), status_code=400)

    [acces] = lire_acces(
        latitude=LAT, longitude=LON, poles=[("port", "Port", 45.9, 5.0)], settings=reglages
    )

    assert acces.duree_min is None and acces.distance_vol_km > 10


def test_dropped_connections_are_retried(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))
    httpx_mock.add_response(json=collection())
    assert lire_acces(latitude=LAT, longitude=LON, poles=[], settings=reglages) == []


@avec_dbt
def test_nearest_pole_of_each_mode_is_read_in_the_same_territory(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon", latitude=LAT, longitude=LON), database=medallion_db
    )

    modes = [mode for mode, *_ in diagnostic.poles_proches]
    assert modes == sorted(set(modes)) and set(modes) <= {"port", "aeroport", "rail"}


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_response(status_code=400, is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_acces_routier(diagnostic, settings=reglages)

    assert diagnostic.acces_routier is None and diagnostic.acces_routier_erreur
    assert "Accès routier non évalué" in formater_diagnostic(diagnostic)


@avec_dbt
def test_diagnosis_reads_times_and_flags_a_missing_motorway(medallion_db: Path) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    acces = [
        Acces("autoroute", "A46", 1.4, None, None),
        Acces("aeroport", "Lyon-Saint-Exupéry", 25.0, 31.2, 28.6),
        Acces("port", "Marseille", 280.0, None, None),
    ]
    avec = replace(base, acces_routier=acces)

    texte = formater_diagnostic(avec)
    assert (
        "Accès routier (voiture, pas de profil poids lourd) : autoroute A46 à 1 km à vol" in texte
    )
    assert "aéroport Lyon-Saint-Exupéry à 29 min (31 km)" in texte
    assert "port Marseille à 280 km à vol d'oiseau (trajet non calculé)" in texte
    assert essentiel_en_dict(avec)["acces_routier"]["acces"][0]["nom"] == "A46"
    assert "acces_routier" in essentiel_en_dict(avec)["limites"]

    sans = replace(base, acces_routier=[])
    assert "aucune autoroute à moins de 10 km" in formater_diagnostic(sans)
