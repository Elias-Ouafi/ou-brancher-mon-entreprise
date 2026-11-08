from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import essentiel_en_dict, formater_diagnostic, lire_diagnostic
from ingestion.extract_zre import ZonesRepartitionError, lire_zones, telecharger_zones
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

WFS_URL = "https://sandre.test/geo/zrpe"

avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def _feature(gid: int, nom: str | None, type_zre: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "gid": gid,
            "CodeNatZone": f"0300{gid}",
            "TypeZone": "ZRE",
            "NomZone": nom,
            "StZone": "Validé",
            "TypeZRE": type_zre,
        },
        # Le Sandre publie parfois une troisième coordonnée : elle est ignorée
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[2.0, 48.0, 0], [3.0, 48.0, 0], [3.0, 49.0, 0], [2.0, 48.0, 0]]],
        },
    }


def _collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


def test_zones_keep_their_resource_and_an_empty_name() -> None:
    (zone,) = lire_zones(_collection(_feature(7, None, "ZRESout")))

    assert zone["gid"] == "7" and zone["type_zre"] == "ZRESout" and zone["nom_zone"] == ""
    assert json.loads(zone["geometrie"]) == [[[[2.0, 48.0], [3.0, 48.0], [3.0, 49.0], [2.0, 48.0]]]]
    assert (zone["lon_min"], zone["lat_max"]) == (2.0, 49.0)


def test_unknown_zone_type_is_refused() -> None:
    with pytest.raises(ZonesRepartitionError, match="type de ZRE inconnu"):
        lire_zones(_collection(_feature(1, "Albien", "Nappe")))


def test_download_checks_the_announced_count(httpx_mock: HTTPXMock, settings: Settings) -> None:
    reglages = settings.model_copy(update={"zones_repartition_eaux_url": WFS_URL})
    hits = re.compile(re.escape(WFS_URL) + ".*resultType=hits")
    page = re.compile(re.escape(WFS_URL) + ".*outputFormat=geojson")
    httpx_mock.add_response(url=hits, text='<wfs:FeatureCollection numberMatched="2" />')
    httpx_mock.add_response(
        url=page, json=_collection(_feature(1, "Albien", "ZRESout"), _feature(2, "", "Mixte"))
    )
    assert len(telecharger_zones(settings=reglages)) == 2

    httpx_mock.add_response(url=hits, text='<wfs:FeatureCollection numberMatched="3" />')
    httpx_mock.add_response(url=page, json=_collection(_feature(1, "Albien", "ZRESout")))
    with pytest.raises(ZonesRepartitionError, match="incompletes : 1 sur 3"):
        telecharger_zones(settings=reglages)


@avec_dbt
def test_diagnosis_places_the_address_in_its_zone(medallion_db: Path) -> None:
    lyon = lire_diagnostic(
        territoire("69123", "Lyon", latitude=45.76, longitude=4.85), database=medallion_db
    )

    # L'emprise de la zone superficielle couvre Lyon, son triangle ne le contient pas
    assert [(z.nom, z.ressource) for z in lyon.zones_repartition or []] == [
        ("Nappe de test", "eaux souterraines")
    ]
    texte = formater_diagnostic(lyon)
    assert "Tension sur la ressource : en zone de répartition des eaux" in texte
    assert "Nappe de test (eaux souterraines)" in texte
    assert "autorisation dès 8 m³/h" in texte
    assert essentiel_en_dict(lyon)["zones_repartition_eaux"]["seuil_autorisation_m3_h"] == 8
    assert "zones_repartition_eaux" in essentiel_en_dict(lyon)["limites"]

    ajaccio = lire_diagnostic(
        territoire("2A004", "Ajaccio", latitude=41.9, longitude=8.7), database=medallion_db
    )
    assert ajaccio.zones_repartition == []
    assert "hors zone de répartition des eaux" in formater_diagnostic(ajaccio)
