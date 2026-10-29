from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_voisinage,
    diagnostic_en_dict,
    formater_diagnostic,
    lire_diagnostic,
    verdict,
)
from ingestion.georisques import (
    Installation,
    Voisinage,
    lire_installations,
    lire_voisinage,
    lire_voisinage_site,
)
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

GEORISQUES_URL = "https://georisques.test/api/v1/"
CHILLY = {"latitude": 48.7008, "longitude": 2.3127}
VIDE = {"results": 0, "page": 1, "total_pages": 0, "data": []}
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def fixture(nom: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"georisques_{nom}_chilly.json").read_text(encoding="utf-8"))


def sis(*noms: str) -> dict[str, Any]:
    return {"results": len(noms), "data": [{"nom": n} for n in noms]}


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"georisques_base_url": GEORISQUES_URL})


def repondre(httpx_mock: HTTPXMock, chemin: str, payload: dict[str, Any]) -> None:
    httpx_mock.add_response(url=re.compile(rf"{GEORISQUES_URL}{chemin}\?.*"), json=payload)


def test_only_operating_classified_sites_are_kept_closest_first() -> None:
    installations = lire_installations(fixture("icpe")["data"], **CHILLY)

    assert all(i.regime in {"Autorisation", "Enregistrement"} for i in installations)
    distances = [i.distance_km for i in installations]
    assert distances == sorted(distances)
    seveso = [i for i in installations if i.seveso]
    assert [(s.nom, s.seveso) for s in seveso] == [("SMCA", "seuil bas")]
    assert seveso[0].distance_km == pytest.approx(4.83, abs=0.01)


def test_site_whose_postcode_and_commune_disagree_is_dropped() -> None:
    commun = {
        "regime": "Autorisation",
        "etatActivite": "En exploitation avec titre",
        "statutSeveso": "Seveso seuil bas",
        "latitude": 48.85297,
        "longitude": 2.349996,
    }
    entrees = [
        {
            **commun,
            "raisonSociale": "TRIADIS SERVICES",
            "codePostal": "76000",
            "codeInsee": "75056",
        },
        {**commun, "raisonSociale": "AJACCIO", "codePostal": "20000", "codeInsee": "2A004"},
        {**commun, "raisonSociale": "SAINT-DENIS", "codePostal": "97400", "codeInsee": "97411"},
        {**commun, "raisonSociale": "SANS CODE POSTAL", "codeInsee": "75056"},
    ]

    installations = lire_installations(entrees, latitude=48.8471, longitude=2.2862)

    assert {i.nom for i in installations} == {"AJACCIO", "SAINT-DENIS", "SANS CODE POSTAL"}


def test_neighbourhood_counts_each_family() -> None:
    v = lire_voisinage(
        rga=fixture("rga"),
        cavites=fixture("cavites"),
        installations=[
            Installation("A", "X", "Autorisation", None, 0.4),
            Installation("B", "X", "Enregistrement", None, 0.9),
            Installation("C", "Y", "Autorisation", "seuil haut", 3.0),
            Installation("D", "Z", "Autorisation", "seuil bas", 6.0),
        ],
        sis=sis("SITE REI"),
        sup=VIDE,
        instructions={"data": [{"statut": "En cours"}, {"statut": "Clôturée"}]},
        casias=fixture("casias"),
    )

    assert v.argiles == "Exposition forte" and v.nb_cavites == 0
    assert (v.nb_icpe, v.nb_icpe_autorisation) == (2, 1)
    assert [s.nom for s in v.seveso] == ["C"]
    assert v.sis == ["SITE REI"] and v.nb_instructions_en_cours == 1 and v.nb_casias == 9


def test_neighbourhood_is_asked_around_the_point(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    repondre(httpx_mock, "installations_classees", fixture("icpe"))
    repondre(httpx_mock, "rga", fixture("rga"))
    repondre(httpx_mock, "cavites", fixture("cavites"))
    repondre(httpx_mock, "ssp/conclusions_sis", fixture("sis"))
    repondre(httpx_mock, "ssp/conclusions_sup", VIDE)
    repondre(httpx_mock, "ssp/instructions", fixture("instructions"))
    repondre(httpx_mock, "ssp/casias", fixture("casias"))

    v = lire_voisinage_site(**CHILLY, settings=reglages)

    assert v.seveso[0].nom == "SMCA" and v.nb_casias == 9
    icpe = httpx_mock.get_requests(url=re.compile(r".*installations_classees.*"))[0]
    assert dict(icpe.url.params) == {
        "latlon": "2.3127,48.7008",
        "rayon": "5000",
        "page_size": "1000",
        "page": "1",
    }
    casias = httpx_mock.get_requests(url=re.compile(r".*ssp/casias.*"))[0]
    assert casias.url.params["rayon"] == "500"


def test_every_page_of_classified_sites_is_read(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    premiere, seconde = fixture("icpe"), fixture("icpe")
    premiere["total_pages"] = seconde["total_pages"] = 2
    seconde["data"] = [{**e, "raisonSociale": f"{e['raisonSociale']} bis"} for e in seconde["data"]]
    httpx_mock.add_response(
        url=re.compile(r".*installations_classees\?.*page=1(&|$).*"), json=premiere
    )
    httpx_mock.add_response(
        url=re.compile(r".*installations_classees\?.*page=2(&|$).*"), json=seconde
    )
    for chemin in ("rga", "cavites", "ssp/conclusions_sis", "ssp/conclusions_sup"):
        repondre(httpx_mock, chemin, VIDE)
    repondre(httpx_mock, "ssp/instructions", VIDE)
    repondre(httpx_mock, "ssp/casias", VIDE)

    v = lire_voisinage_site(**CHILLY, settings=reglages)

    assert [s.nom for s in v.seveso] == ["SMCA", "SMCA bis"]


def test_clay_map_absent_outside_mainland(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    repondre(httpx_mock, "installations_classees", VIDE)
    httpx_mock.add_response(url=re.compile(r".*/rga\?.*"), content=b"")
    for chemin in ("cavites", "ssp/conclusions_sis", "ssp/conclusions_sup"):
        repondre(httpx_mock, chemin, VIDE)
    repondre(httpx_mock, "ssp/instructions", VIDE)
    repondre(httpx_mock, "ssp/casias", VIDE)

    assert lire_voisinage_site(latitude=-20.88, longitude=55.45, settings=reglages).argiles is None


def voisinage(**champs: Any) -> Voisinage:
    base = Voisinage(
        argiles="Exposition forte",
        nb_cavites=0,
        seveso=[Installation("SMCA", "Paray-Vieille-Poste", "Autorisation", "seuil bas", 4.83)],
        nb_icpe=2,
        nb_icpe_autorisation=0,
        sis=[],
        nb_sup=0,
        nb_instructions_en_cours=0,
        nb_casias=9,
    )
    return replace(base, **champs)


@avec_dbt
def test_neighbourhood_is_written_without_changing_the_verdict(medallion_db: Path) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    d = replace(base, voisinage=voisinage(sis=["SITE REI"]))

    texte = formater_diagnostic(d)
    assert "Argiles (retrait-gonflement) à l'adresse : exposition forte" in texte
    assert "SMCA (seuil bas, Paray-Vieille-Poste) à 5 km" in texte
    assert "secteurs d'information sur les sols 1 (SITE REI)" in texte
    assert "anciens sites industriels ou de service (CASIAS) 9" in texte
    risques = next(c for c in verdict(d) if c.nom == "Risques")
    assert risques.etat == next(c for c in verdict(base) if c.nom == "Risques").etat
    assert "Seveso le plus proche : SMCA (seuil bas) à 5 km" in risques.detail
    assert diagnostic_en_dict(d)["voisinage"]["seveso"][0]["nom"] == "SMCA"

    sans = replace(base, voisinage=voisinage(seveso=[], argiles=None))
    texte = formater_diagnostic(sans)
    assert "Seveso en exploitation à moins de 5 km : aucun" in texte
    assert "territoire non couvert par la carte d'exposition" in texte


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"), is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_voisinage(diagnostic, settings=reglages)

    assert diagnostic.voisinage is None and diagnostic.voisinage_erreur is not None
    assert "Voisinage (argiles, Seveso, sols pollués) non consulté" in formater_diagnostic(
        diagnostic
    )
