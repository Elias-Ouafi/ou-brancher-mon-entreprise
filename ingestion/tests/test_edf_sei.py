from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_edf_sei import EdfSeiError, lire_csv, telecharger_production

EDF_SEI_URL = "https://opendata.edf.test/production.csv"
ENTETE = "territoire;statut;date_heure;production_totale_mw;thermique_mw;bagasse_charbon_mw"


def test_hours_without_seconds_and_missing_sectors_are_normalised() -> None:
    contenu = (
        f"{ENTETE}\r\n"
        "Corse;Validé;2019-01-01T00:00:00+00:00;339.88;139.89;\r\n"
        "Réunion;Estimé;2024-01-01T00:00+00:00;500.0;200.0;150.5\r\n"
    ).encode("utf-8-sig")

    corse, reunion = lire_csv(contenu)

    assert corse["date_heure"] == "2019-01-01T00:00:00+00:00"
    assert corse["bagasse_charbon_mw"] is None and corse["thermique_mw"] == 139.89
    assert corse["eolien_mw"] is None
    assert reunion["date_heure"] == "2024-01-01T00:00:00+00:00"
    assert reunion["bagasse_charbon_mw"] == 150.5


def test_unexpected_file_is_rejected() -> None:
    with pytest.raises(EdfSeiError, match="colonnes absentes"):
        lire_csv(b"territoire;date\nCorse;2024\n")


def test_file_is_downloaded_over_https(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(
        url=EDF_SEI_URL,
        content=f"{ENTETE}\nGuyane;Estimé;2024-06-01T12:00:00+00:00;120;80;\n".encode(),
    )
    reglages = settings.model_copy(update={"edf_sei_url": EDF_SEI_URL})

    assert [ligne["territoire"] for ligne in telecharger_production(settings=reglages)] == [
        "Guyane"
    ]
