from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import completer_risques, formater_diagnostic, lire_diagnostic
from ingestion.georisques import (
    Exposition,
    GeorisquesError,
    exposition,
    lire_gaspar,
    lire_rapport,
    lire_risques,
    lire_zonage,
    niveau,
)
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

GEORISQUES_URL = "https://georisques.test/api/v1/"


def fixture(nom: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / nom).read_text(encoding="utf-8"))


def libelles(risques: list[Any]) -> list[str]:
    return [r.libelle for r in risques]


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"georisques_base_url": GEORISQUES_URL})


@pytest.mark.parametrize(
    ("statut", "attendu"),
    [
        ("Risque Existant", Exposition.EXPOSE),
        ("Risque Existant - important", Exposition.EXPOSE),
        ("Risque Concerne", Exposition.EXPOSE),
        ("Risque non Concerne", Exposition.NON_EXPOSE),
        ("Risque non Connu", Exposition.INCONNU),
        ("Risque Inconnu", Exposition.INCONNU),
        ("Risque recensé dans la commune - faible", Exposition.INCONNU),
        ("Statut jamais vu", Exposition.INCONNU),
        (None, Exposition.ABSENT),
    ],
)
def test_statuses_are_classified(statut: str | None, attendu: Exposition) -> None:
    assert exposition(statut) is attendu


def test_level_is_read_after_the_dash() -> None:
    assert niveau("Risque Existant - important") == "important"
    assert niveau("Risque Existant") is None


def test_lyon_report_separates_address_from_commune() -> None:
    rapport = lire_rapport(fixture("georisques_rapport_lyon.json"))

    assert rapport.maille == "adresse"
    assert rapport.url is not None and "codeInsee=69123" in rapport.url
    assert libelles(rapport.selon(Exposition.EXPOSE, "naturel")) == [
        "Inondation",
        "Remontée de nappe",
        "Séisme",
        "Retrait-gonflement des argiles",
        "Radon",
    ]
    assert libelles(rapport.selon(Exposition.EXPOSE, "technologique")) == ["Pollution des sols"]
    assert libelles(rapport.selon(Exposition.INCONNU)) == ["Rupture de barrage"]
    assert libelles(rapport.selon(Exposition.NON_EXPOSE)) == [
        "Installations industrielles classées (ICPE)",
        "Canalisations de matières dangereuses",
    ]
    seisme = next(r for r in rapport.risques if r.code == "seisme")
    assert seisme.niveau == "faible"


# Ajaccio : risque côtier existant dans la commune, « non connu » à l'adresse
def test_risk_unknown_at_the_address_is_not_reported_absent() -> None:
    rapport = lire_rapport(fixture("georisques_rapport_ajaccio.json"))
    cotier = next(r for r in rapport.risques if r.code == "risqueCotier")
    assert cotier.exposition is Exposition.INCONNU
    absents = {r.code for r in rapport.selon(Exposition.ABSENT)}
    assert absents == {"reculTraitCote", "avalanche", "eruptionVolcanique", "cyclone",
                       "nucleaire", "risqueMinier"}  # fmt: skip


def test_report_without_its_blocks_is_rejected() -> None:
    with pytest.raises(GeorisquesError, match="risquesTechnologiques"):
        lire_rapport({"risquesNaturels": {}})


def test_commune_fallback_keeps_the_most_specific_subtypes() -> None:
    risques = lire_gaspar(fixture("georisques_gaspar_ajaccio.json"), {"2A004"})
    # 17 « Phénomène lié à l'atmosphère » s'efface devant son sous-type 172
    assert [(r.code, r.famille) for r in risques] == [
        ("gaspar_16", "naturel"),
        ("gaspar_172", "naturel"),
        ("gaspar_21", "technologique"),
        ("gaspar_23", "technologique"),
        ("gaspar_24", "technologique"),
    ]
    assert all(r.exposition is Exposition.INCONNU for r in risques)


def test_commune_fallback_picks_the_commune_by_code() -> None:
    payload = {
        "data": [
            {"code_insee": "69381", "risques_detail": [{"num_risque": "13"}]},
            {"code_insee": "69382", "risques_detail": [{"num_risque": "11"}]},
        ]
    }
    assert [r.code for r in lire_gaspar(payload, {"69382", "69123"})] == ["gaspar_11"]
    with pytest.raises(GeorisquesError):
        lire_gaspar({"data": []}, {"69382"})


@pytest.mark.parametrize(
    ("zone", "libelle", "intensite"),
    [
        ("2 - FAIBLE", "Séisme, zone 2", "faible"),
        ("1 - TRES FAIBLE", "Séisme, zone 1", "très faible"),
    ],
)
def test_seismic_zone_is_read(zone: str, libelle: str, intensite: str) -> None:
    risque = lire_zonage({"data": [{"zone_sismicite": zone}]})
    assert risque is not None
    assert (risque.libelle, risque.niveau) == (libelle, intensite)
    assert lire_zonage({"data": []}) is None


def test_report_is_asked_at_the_exact_point(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_response(json=fixture("georisques_rapport_lyon.json"))

    rapport = lire_risques(
        latitude=45.763721, longitude=4.835685, codes_commune={"69382"}, settings=reglages
    )

    assert rapport.maille == "adresse"
    requete = httpx_mock.get_requests()[0]
    assert requete.url.path == "/api/v1/resultats_rapport_risque"
    assert requete.url.params["latlon"] == "4.835685,45.763721"
    assert requete.headers["User-Agent"].startswith("ou-brancher-mon-entreprise")


# Constaté en réel : plusieurs connexions coupées avant une réponse
def test_dropped_connections_are_retried(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    for _ in range(2):
        httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))
    httpx_mock.add_response(json=fixture("georisques_rapport_lyon.json"))

    rapport = lire_risques(latitude=45.76, longitude=4.83, codes_commune=set(), settings=reglages)
    assert rapport.maille == "adresse"


def test_unreachable_report_falls_back_on_the_commune(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_exception(
        httpx.RemoteProtocolError("Server disconnected"),
        url=f"{GEORISQUES_URL}resultats_rapport_risque?latlon=8.7386%2C41.9192",
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=f"{GEORISQUES_URL}gaspar/risques?latlon=8.7386%2C41.9192",
        json=fixture("georisques_gaspar_ajaccio.json"),
    )
    httpx_mock.add_response(
        url=f"{GEORISQUES_URL}zonage_sismique?latlon=8.7386%2C41.9192",
        json={"data": [{"zone_sismicite": "1 - TRES FAIBLE"}]},
    )

    rapport = lire_risques(
        latitude=41.9192, longitude=8.7386, codes_commune={"2A004"}, settings=reglages
    )

    assert rapport.maille == "commune"
    assert rapport.url is None
    assert rapport.risques[0].libelle == "Séisme, zone 1"
    assert len(rapport.risques) == 6


def test_nothing_reachable_raises(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_exception(httpx.ConnectError("injoignable"), is_reusable=True)
    with pytest.raises(GeorisquesError, match="gaspar/risques"):
        lire_risques(latitude=41.9, longitude=8.7, codes_commune=set(), settings=reglages)


dbt_requis = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def _bloc_6(texte: str) -> str:
    return texte[texte.index("6. Risques") :]


@dbt_requis
def test_address_report_is_written_with_its_limit(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_response(json=fixture("georisques_rapport_lyon.json"))
    diagnostic = completer_risques(
        lire_diagnostic(territoire("69382", "Lyon"), database=medallion_db), settings=reglages
    )

    bloc = _bloc_6(formater_diagnostic(diagnostic))
    assert (
        "Naturels, à l'adresse : Inondation, Remontée de nappe, Séisme (faible),"
        " Retrait-gonflement des argiles (modéré), Radon (faible)" in bloc
    )
    assert "Technologiques, à l'adresse : Pollution des sols" in bloc
    assert "non évalués à l'adresse : Rupture de barrage" in bloc
    assert "l'adresse n'est pas concernée : Installations industrielles classées (ICPE)" in bloc
    assert "Rapport officiel : https://www.georisques.gouv.fr/" in bloc
    assert diagnostic.limites["risques_site"] in bloc


@dbt_requis
def test_commune_fallback_is_announced_as_such(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    diagnostic = replace(
        lire_diagnostic(territoire("2A004", "Ajaccio"), database=medallion_db),
        risques=replace(
            lire_rapport(fixture("georisques_rapport_ajaccio.json")),
            risques=lire_gaspar(fixture("georisques_gaspar_ajaccio.json"), {"2A004"}),
            url=None,
            maille="commune",
        ),
    )
    bloc = _bloc_6(formater_diagnostic(diagnostic))
    assert "Rapport à l'adresse indisponible" in bloc
    assert "Naturels, recensés dans la commune : Feu de forêt, Tempête et grains (vent)" in bloc
    assert "à l'adresse :" not in bloc


@dbt_requis
def test_unreachable_georisques_never_breaks_the_diagnostic(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("injoignable"), is_reusable=True)
    diagnostic = completer_risques(
        lire_diagnostic(territoire("69123"), database=medallion_db), settings=reglages
    )
    assert diagnostic.risques is None
    assert diagnostic.risques_erreur is not None
    bloc = _bloc_6(formater_diagnostic(diagnostic))
    assert "Géorisques n'a pas répondu, même après plusieurs tentatives" in bloc


@dbt_requis
def test_imprecise_address_is_flagged(medallion_db: Path) -> None:
    diagnostic = replace(
        lire_diagnostic(territoire("69123", precision="municipality"), database=medallion_db),
        risques=lire_rapport(fixture("georisques_rapport_lyon.json")),
    )
    assert "Adresse résolue à la commune seulement" in _bloc_6(formater_diagnostic(diagnostic))
