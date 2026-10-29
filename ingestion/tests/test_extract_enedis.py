from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_enedis import (
    EnedisError,
    lire_page,
    telecharger_conso_entreprises,
)

ENEDIS_URL = "https://opendata.enedis.test/data-fair/api/v1/datasets/"
JEU = f"{ENEDIS_URL}consommation-annuelle-entreprise-par-adresse"
ENTETE = "Année,Code commune,Tri,IRIS,Adresse,Sites,Conso,Grand secteur,NAF"


def _page(*lignes: str) -> bytes:
    return "\n".join([ENTETE, *lignes]).encode("utf-8-sig")


def test_columns_are_read_by_position_and_empty_cells_become_null() -> None:
    (ligne,) = lire_page(_page("2025,69123,4,691230102,,1,5.0,INCONNU,"))

    assert ligne["annee"] == "2025" and ligne["code_commune"] == "69123"
    assert ligne["adresse"] is None and ligne["code_secteur_naf2"] is None
    assert ligne["consommation_annuelle_totale_de_ladresse_mwh"] == "5.0"


def test_unexpected_columns_are_rejected() -> None:
    with pytest.raises(EnedisError, match="3 colonnes"):
        lire_page(b"annee,code_commune,adresse\n2025,69123,9 Rue Ampere\n")


def test_latest_year_is_downloaded_page_by_page(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(
        url=f"{JEU}/values_agg?field=annee",
        json={"aggs": [{"value": "2024"}, {"value": "2025"}]},
    )
    suivante = f"{JEU}/lines?format=csv&after=2"
    httpx_mock.add_response(
        url=re.compile(re.escape(f"{JEU}/lines?format=csv&size=") + ".*annee%3A2025"),
        content=_page(
            "2025,69123,1,691230101,9 Rue Ampère,2,300,INDUSTRIE,24",
            '2025,69123,2,691230101,"9 RUE AMPERE",1,50,TERTIAIRE,47',
        ),
        headers={"Link": f'<{suivante}>; rel="next"'},
    )
    httpx_mock.add_response(
        url=suivante, content=_page("2025,69266,1,692660101,1 Rue du Test,4,1000,INDUSTRIE,20")
    )
    reglages = settings.model_copy(update={"enedis_base_url": ENEDIS_URL})

    lignes = telecharger_conso_entreprises(settings=reglages)

    assert [(ligne["code_commune"], ligne["tri_des_adresses"]) for ligne in lignes] == [
        ("69123", "1"),
        ("69123", "2"),
        ("69266", "1"),
    ]


PROFILS = f"{ENEDIS_URL}conso-sup36-region"
ENTETE_PROFILS = "Horodate,Région,Profil,Secteur,Points,Courbe,Indice"


def _page_profils(*lignes: str) -> bytes:
    return "\n".join([ENTETE_PROFILS, *lignes]).encode("utf-8-sig")


def test_profile_rows_are_normalised_to_utc_with_short_codes() -> None:
    from ingestion.extract_enedis import lire_page_profils

    (ligne, masquee) = lire_page_profils(
        _page_profils(
            "2026-06-30T23:30:00+02:00,11,ENT3 (+ ENT4 + ENT5 + ENT7),S2: Industrie,1799,99878,98",
            "2026-06-30T23:00:00+02:00,11,ENT1 (+ ENT2),S3: Tertiaire,12,,S",
        )
    )

    assert ligne["date_heure"] == "2026-06-30T21:30:00+00:00"
    assert (ligne["profil"], ligne["secteur"], ligne["courbe_moyenne_wh"]) == ("ENT3", "S2", 99878)
    assert masquee["courbe_moyenne_wh"] is None and masquee["indice_representativite"] == "S"


def test_load_profiles_cover_the_twelve_months_before_the_latest_curve(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    from ingestion.extract_enedis import telecharger_profils_charge

    httpx_mock.add_response(
        url=re.compile(re.escape(f"{PROFILS}/lines?size=1")),
        json={"results": [{"horodate": "2026-06-30T23:30:00+02:00"}]},
    )
    httpx_mock.add_response(
        url=re.compile(re.escape(f"{PROFILS}/lines?format=csv") + r".*2025-06-30\+TO\+2026-06-30"),
        content=_page_profils("2026-06-30T23:30:00+02:00,11,ENT3 (+ ENT4),S2: Industrie,3,100,98"),
    )
    reglages = settings.model_copy(update={"enedis_base_url": ENEDIS_URL})

    lignes = telecharger_profils_charge(settings=reglages)

    assert [ligne["code_region"] for ligne in lignes] == ["11"]
