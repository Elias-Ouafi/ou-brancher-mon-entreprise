from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ingestion import extract_sismique
from ingestion.config import Settings
from ingestion.diagnostic import avec_risques_locaux, formater_diagnostic, lire_diagnostic
from ingestion.extract_sismique import (
    ZonageSismiqueError,
    contient,
    lire_page,
    telecharger_zonage,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

WFS_URL = "https://www.georisques.test/services"

avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def _feature(gid: str, zone: str, geometrie: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"gid": gid, "type_zone": zone[0], "zone_sismicite": zone},
        "geometry": geometrie,
    }


CARRE = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}


def test_page_gives_zone_polygons_and_bounding_box() -> None:
    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[10.123456, 40], [11, 40], [11, 41], [10.123456, 40]]],
            [[[12, 42], [13, 42], [13, 43.987654], [12, 42]]],
        ],
    }
    lignes = lire_page(
        {"features": [_feature("7", "3 - MODEREE", CARRE), _feature("8", "5 - FORTE", multi)]}
    )
    assert [(ligne["gid"], ligne["zone_sismicite"]) for ligne in lignes] == [
        ("7", "3"),
        ("8", "5"),
    ]
    multi_ligne = lignes[1]
    assert (multi_ligne["lon_min"], multi_ligne["lat_max"]) == (10.12346, 43.98765)
    assert len(json.loads(multi_ligne["geometrie"])) == 2


def test_unreadable_zone_is_rejected() -> None:
    with pytest.raises(ZonageSismiqueError, match="illisible"):
        lire_page({"features": [_feature("1", "zone inconnue", CARRE)]})


def test_point_in_polygon_respects_holes() -> None:
    exterieur = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    trou = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]
    polygones = [[exterieur, trou]]
    assert contient(polygones, 0.5, 0.5)
    assert not contient(polygones, 2, 2)
    assert not contient(polygones, 5, 2)


def _annonce(httpx_mock: HTTPXMock, nombre: int) -> None:
    httpx_mock.add_response(
        url=re.compile(r".*resultType=hits.*"),
        text=f'<wfs:FeatureCollection numberMatched="{nombre}" numberReturned="0"/>',
    )


def _pages(httpx_mock: HTTPXMock, pages: list[list[dict[str, Any]]]) -> None:
    for page in pages:
        httpx_mock.add_response(
            url=re.compile(r".*outputFormat=geojson.*"),
            json={"type": "FeatureCollection", "features": page},
        )


# Pages de deux polygones triés par gid, jusqu'au nombre annoncé par le service
def test_download_pages_through_the_wfs_layer(
    httpx_mock: HTTPXMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_sismique, "TAILLE_PAGE", 2)
    _annonce(httpx_mock, 3)
    _pages(
        httpx_mock,
        [
            [_feature("1", "1 - TRES FAIBLE", CARRE), _feature("2", "2 - FAIBLE", CARRE)],
            [_feature("3", "4 - MOYENNE", CARRE)],
        ],
    )
    reglages = settings.model_copy(update={"zonage_sismique_url": WFS_URL})
    lignes = telecharger_zonage(settings=reglages)
    assert [ligne["gid"] for ligne in lignes] == ["1", "2", "3"]
    pages = [r.url.params for r in httpx_mock.get_requests()][1:]
    assert [p["startIndex"] for p in pages] == ["0", "2"]
    assert {(p["typeNames"], p["sortBy"]) for p in pages} == {("ms:risq_zonage_sismique", "gid")}


# Un polygone servi deux fois (ordre instable) en cache un autre : téléchargement refusé
def test_incomplete_download_is_refused(
    httpx_mock: HTTPXMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_sismique, "TAILLE_PAGE", 2)
    _annonce(httpx_mock, 4)
    _pages(
        httpx_mock,
        [
            [_feature("1", "1 - TRES FAIBLE", CARRE), _feature("2", "2 - FAIBLE", CARRE)],
            [_feature("2", "2 - FAIBLE", CARRE), _feature("3", "4 - MOYENNE", CARRE)],
        ],
    )
    reglages = settings.model_copy(update={"zonage_sismique_url": WFS_URL})
    with pytest.raises(ZonageSismiqueError, match="incomplet : 3 sur 4"):
        telecharger_zonage(settings=reglages)


def _risques_locaux(medallion_db: Path, commune: str, latitude: float, longitude: float) -> Any:
    diagnostic = lire_diagnostic(
        territoire(commune, latitude=latitude, longitude=longitude), database=medallion_db
    )
    reglages = Settings(_env_file=None, duckdb_path=medallion_db)
    return avec_risques_locaux(diagnostic, settings=reglages)


# Sans l'API Géorisques, la zone vient du polygone qui contient l'adresse, pas de son emprise
@avec_dbt
@pytest.mark.parametrize(
    ("commune", "latitude", "longitude", "zone", "niveau"),
    [
        ("69123", 45.76, 4.85, "2", "faible"),
        ("2A004", 41.92, 8.74, "4", "moyenne"),
        # Dans le trou du carré corse : le polygone du trou s'applique
        ("2A004", 42.2, 9.1, "1", "très faible"),
    ],
)
def test_local_fallback_adds_the_seismic_zone(
    medallion_db: Path, commune: str, latitude: float, longitude: float, zone: str, niveau: str
) -> None:
    diagnostic = _risques_locaux(medallion_db, commune, latitude, longitude)
    risques = diagnostic.risques
    assert risques is not None
    seisme = next(r for r in risques.risques if r.code == "seisme")
    assert seisme.libelle == f"Séisme, zone {zone}"
    assert seisme.niveau == niveau
    assert not any(r.code == "gaspar_13" for r in risques.risques)
    texte = formater_diagnostic(diagnostic)
    assert f"Séisme, zone {zone} ({niveau})" in texte
    assert "règles parasismiques des bâtiments neufs" in texte


@avec_dbt
def test_point_outside_every_polygon_keeps_gaspar_alone(medallion_db: Path) -> None:
    diagnostic = _risques_locaux(medallion_db, "2A004", 30.0, 30.0)
    assert diagnostic.risques is not None
    assert not any(r.code == "seisme" for r in diagnostic.risques.risques)
    assert "règles parasismiques" not in formater_diagnostic(diagnostic)
