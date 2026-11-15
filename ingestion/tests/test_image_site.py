from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_image_site,
    diagnostic_en_dict,
    formater_diagnostic,
    libelle_prise_de_vue,
    lire_diagnostic,
)
from ingestion.image_site import (
    ImageSite,
    ImageSiteError,
    lire_dates,
    lire_image_site,
    telecharger_image,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from reporting.implantation import generer_rapport_implantation

WFS_URL = "https://wfs.test/ows"
WMS_URL = "https://wms.test/wms"
LAT, LON = 48.7, 2.31
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"geoplateforme_wfs_url": WFS_URL, "geoplateforme_wms_url": WMS_URL}
    )


def vol(annee: int, jour: str) -> dict[str, Any]:
    return {"properties": {"pva": annee, "date_vol": f"{jour}Z"}}


def collection(*vols: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(vols)}


def jpeg() -> bytes:
    sortie = BytesIO()
    Image.new("RGB", (8, 8), "#6b8e5a").save(sortie, "JPEG")
    return sortie.getvalue()


def test_the_latest_campaign_gives_the_dates() -> None:
    reponse = collection(
        vol(2021, "2021-08-15"),
        vol(2024, "2024-08-27"),
        vol(2024, "2024-08-22"),
        {"properties": {"pva": 2025}},
    )
    assert lire_dates(reponse) == (date(2024, 8, 22), date(2024, 8, 27))


def test_without_a_date_there_is_no_image() -> None:
    for reponse in ({}, collection(), collection({"properties": {"pva": 2024}})):
        with pytest.raises(ImageSiteError):
            lire_dates(reponse)


def test_dates_are_written_in_french() -> None:
    assert libelle_prise_de_vue(date(2024, 7, 10), date(2024, 7, 10)) == "le 10 juillet 2024"
    assert libelle_prise_de_vue(date(2024, 8, 22), date(2024, 8, 27)) == "du 22 au 27 août 2024"
    assert (
        libelle_prise_de_vue(date(2024, 8, 30), date(2024, 9, 2))
        == "du 30 août 2024 au 2 septembre 2024"
    )


def test_query_reads_the_mosaic_graph_at_the_address(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(json=collection(vol(2024, "2024-08-22")))

    image = lire_image_site(latitude=LAT, longitude=LON, settings=reglages)

    requete = httpx_mock.get_requests()[0].url
    assert str(requete).startswith(WFS_URL)
    assert requete.params["TYPENAMES"].endswith("GRAPHE-MOSAIQUAGE:graphe_bdortho")
    assert requete.params["BBOX"].startswith("48.69999,2.30999,48.70001,2.31001")
    assert image.debut == image.fin == date(2024, 8, 22)
    # Le carré de 400 m de côté est centré sur l'adresse (latitude d'abord en WMS 1.3.0)
    sud, ouest, nord, est = (
        float(v) for v in re.search(r"BBOX=([^&]+)", image.url)[1].replace("%2C", ",").split(",")
    )
    assert (sud + nord) / 2 == pytest.approx(LAT, abs=1e-5)
    assert (ouest + est) / 2 == pytest.approx(LON, abs=1e-5)
    assert (nord - sud) * 111.32 == pytest.approx(0.4, rel=0.01)


def test_wfs_failure_is_reported(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_response(status_code=400)
    with pytest.raises(ImageSiteError):
        lire_image_site(latitude=LAT, longitude=LON, settings=reglages)


def test_only_a_jpeg_is_an_image(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    site = ImageSite(date(2024, 8, 22), date(2024, 8, 22), 400, WMS_URL)
    httpx_mock.add_response(content=jpeg())
    assert telecharger_image(site, settings=reglages).startswith(b"\xff\xd8")
    # Le WMS répond une erreur en XML avec un code 200
    httpx_mock.add_response(content=b"<ServiceExceptionReport/>")
    with pytest.raises(ImageSiteError):
        telecharger_image(site, settings=reglages)


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_response(status_code=400, is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_image_site(diagnostic, settings=reglages)

    assert diagnostic.image_site is None and diagnostic.image_site_erreur
    assert "Image du site non consultée" in formater_diagnostic(diagnostic)


@avec_dbt
def test_diagnosis_and_pdf_carry_the_dated_image(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path, tmp_path: Path
) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    site = ImageSite(date(2024, 8, 22), date(2024, 8, 27), 400, WMS_URL)
    avec = replace(base, image_site=site)
    reglages = reglages.model_copy(update={"duckdb_path": medallion_db})

    texte = formater_diagnostic(avec)
    assert "prise de vue du 22 au 27 août 2024 (BD ORTHO de l'IGN)" in texte
    assert diagnostic_en_dict(avec)["image_site"]["fin"] == "2024-08-27"

    httpx_mock.add_response(content=jpeg())
    pdf = generer_rapport_implantation(
        avec, format="pdf", settings=reglages, output=tmp_path / "avec.pdf"
    )
    # Sans réponse du WMS, le rapport se fait quand même, sans image
    httpx_mock.add_response(status_code=500, is_reusable=True)
    sans = generer_rapport_implantation(
        avec, format="pdf", settings=reglages, output=tmp_path / "sans.pdf"
    )
    assert pdf.stat().st_size > sans.stat().st_size
