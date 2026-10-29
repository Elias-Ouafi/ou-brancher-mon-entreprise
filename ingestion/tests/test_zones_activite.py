from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_zones_activite,
    essentiel_en_dict,
    formater_diagnostic,
    lire_diagnostic,
    verdict,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from ingestion.zones_activite import (
    ZoneActivite,
    ZonesActiviteError,
    lire_zones,
    lire_zones_activite,
)

WFS_URL = "https://wfs.test/ows"
LAT, LON = 45.75, 4.85
COS = math.cos(math.radians(LAT))
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


# Carré de `cote_km` km de côté dont le coin sud-ouest est à (est_km, nord_km) de l'adresse
def carre(nom: str, est_km: float, nord_km: float, cote_km: float) -> dict[str, Any]:
    x0 = LON + est_km / (111.32 * COS)
    y0 = LAT + nord_km / 111.32
    dx, dy = cote_km / (111.32 * COS), cote_km / 111.32
    anneau = [[x0, y0], [x0 + dx, y0], [x0 + dx, y0 + dy], [x0, y0 + dy], [x0, y0]]
    return {
        "type": "Feature",
        "properties": {"toponyme": nom},
        "geometry": {"type": "MultiPolygon", "coordinates": [[anneau]]},
    }


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"geoplateforme_wfs_url": WFS_URL})


def test_distance_surface_and_containment_are_measured_in_a_local_plan() -> None:
    zones = lire_zones(
        collection(carre("Loin", 2, 0, 1), carre("Autour", -1, -1, 2), carre("Pres", 0.5, -0.5, 1)),
        latitude=LAT,
        longitude=LON,
    )

    assert [z.nom for z in zones] == ["Autour", "Pres", "Loin"]
    assert zones[0].contient_adresse and zones[0].distance_km == 0
    assert zones[0].surface_ha == pytest.approx(400, rel=0.01)
    assert not zones[1].contient_adresse and zones[1].distance_km == pytest.approx(0.5, rel=0.01)
    assert zones[2].distance_km == pytest.approx(2, rel=0.01)


def test_unnamed_zone_and_other_geometries_are_handled() -> None:
    sans_nom = carre("", 1, 1, 1)
    sans_nom["properties"] = {"toponyme": None}
    point = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Point", "coordinates": [1, 1]},
    }

    [zone] = lire_zones(collection(sans_nom, point), latitude=LAT, longitude=LON)

    assert zone.nom == "zone sans nom"


def test_unexpected_payload_is_refused() -> None:
    with pytest.raises(ZonesActiviteError):
        lire_zones({}, latitude=LAT, longitude=LON)


def test_query_filters_the_nature_in_a_box_around_the_address(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(json=collection(carre("Proche", 1, 1, 1), carre("Hors rayon", 8, 8, 1)))

    nb, zones = lire_zones_activite(latitude=LAT, longitude=LON, settings=reglages)

    # La boîte carrée garde des coins au-delà de 5 km : le rayon est contrôlé à la lecture
    assert (nb, [z.nom for z in zones]) == (1, ["Proche"])
    requete = httpx_mock.get_requests()[0]
    assert "nature='Zone industrielle'" in requete.url.params["cql_filter"]
    assert requete.url.params["typeNames"] == "BDTOPO_V3:zone_d_activite_ou_d_interet"
    assert requete.headers["User-Agent"].startswith("ou-brancher-mon-entreprise")


def test_dropped_connections_are_retried(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))
    httpx_mock.add_response(json=collection())
    assert lire_zones_activite(latitude=LAT, longitude=LON, settings=reglages) == (0, [])


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_response(status_code=400, is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_zones_activite(diagnostic, settings=reglages)

    assert diagnostic.nb_zones_activite is None and diagnostic.zones_activite_erreur
    assert "Zones d'activité non consultées" in formater_diagnostic(diagnostic)


@avec_dbt
def test_diagnosis_lists_the_nearest_zones_and_the_address_inside_one(medallion_db: Path) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    zones = [
        ZoneActivite("ZI du Centre", 0.0, 120.0, True),
        ZoneActivite("ZA Est", 2.3, 8.5, False),
    ]
    avec = replace(base, nb_zones_activite=2, zones_activite=zones)

    texte = formater_diagnostic(avec)
    assert "2 zones d'activité ou industrielles à moins de 5 km" in texte
    assert "ZI du Centre (120 ha, l'adresse est dedans)" in texte
    assert "ZA Est (8,5 ha, à 2 km)" in texte
    foncier = next(c for c in verdict(avec) if c.nom == "Foncier")
    assert "Adresse dans la zone d'activité ZI du Centre" in foncier.detail
    assert essentiel_en_dict(avec)["zones_activite"]["nb_dans_le_rayon"] == 2

    aucune = replace(base, nb_zones_activite=0)
    assert "aucune zone d'activité ou industrielle à moins de 5 km" in formater_diagnostic(aucune)
