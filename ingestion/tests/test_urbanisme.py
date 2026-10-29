from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    Critere,
    Diagnostic,
    completer_urbanisme,
    essentiel_en_dict,
    formater_diagnostic,
    lire_diagnostic,
    verdict,
)
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from ingestion.urbanisme import UrbanismeError, ZoneUrbanisme, lire_zone, lire_zone_urbanisme

APICARTO_URL = "https://apicarto.test/api/"
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def fixture() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "gpu_zone_urba_chilly.json").read_text(encoding="utf-8"))


def zone(type_zone: str) -> ZoneUrbanisme:
    return ZoneUrbanisme(type_zone, "UX", None, "200058014_PLUI_20251216", "16/12/2025", None)


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"apicarto_base_url": APICARTO_URL})


def test_real_response_gives_the_zone_and_its_document() -> None:
    z = lire_zone(fixture())
    assert z is not None
    assert (z.type_zone, z.libelle, z.nom_type) == ("U", "UP21", "zone urbaine")
    assert z.document == "200058014_PLUI_20251216"
    assert z.date_validite == "16/12/2025"
    assert z.url_reglement is None
    assert not z.est_inconstructible_activite


def test_no_feature_means_no_digitised_document() -> None:
    assert lire_zone({"type": "FeatureCollection", "features": []}) is None


# Zones superposées (PLU et prescription) : la plus contraignante pour l'activité passe devant
def test_agricultural_or_natural_zone_comes_first() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [{"properties": {"typezone": t}} for t in ("U", "N", "AU")],
    }
    z = lire_zone(payload)
    assert z is not None and z.type_zone == "N" and z.est_inconstructible_activite


@pytest.mark.parametrize("payload", [{}, {"type": "FeatureCollection", "features": [{}]}])
def test_unreadable_payload_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(UrbanismeError):
        lire_zone(payload)


def test_zone_is_asked_at_the_exact_point(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_response(json=fixture())

    z = lire_zone_urbanisme(latitude=48.6735, longitude=2.3415, settings=reglages)

    assert z is not None and z.type_zone == "U"
    requete = httpx_mock.get_requests()[0]
    assert requete.url.path == "/api/gpu/zone-urba"
    assert json.loads(requete.url.params["geom"]) == {
        "type": "Point",
        "coordinates": [2.3415, 48.6735],
    }
    assert requete.headers["User-Agent"].startswith("ou-brancher-mon-entreprise")


def test_dropped_connections_are_retried(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))
    httpx_mock.add_response(json=fixture())
    assert lire_zone_urbanisme(latitude=48.6, longitude=2.3, settings=reglages) is not None


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Any
) -> None:
    httpx_mock.add_response(status_code=400, is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_urbanisme(diagnostic, settings=reglages)

    assert diagnostic.urbanisme is None and diagnostic.urbanisme_erreur is not None
    assert "Zonage d'urbanisme non consulté" in formater_diagnostic(diagnostic)


def foncier(diagnostic: Diagnostic) -> Critere:
    return next(c for c in verdict(diagnostic) if c.nom == "Foncier")


@avec_dbt
def test_foncier_criterion_follows_the_zone(medallion_db: Any) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    urbaine = replace(base, urbanisme=zone("U"), urbanisme_consulte=True)
    assert foncier(urbaine).etat == "info"
    assert "Zonage : zone urbaine UX" in foncier(urbaine).detail

    agricole = replace(base, urbanisme=zone("A"), urbanisme_consulte=True)
    assert foncier(agricole).etat == "attention"
    assert "n'y est pas permise" in foncier(agricole).detail

    vide = replace(base, urbanisme_consulte=True)
    assert foncier(vide).etat == "info"
    assert "aucune zone numérisée" in formater_diagnostic(vide)
    assert essentiel_en_dict(agricole)["urbanisme"]["type_zone"] == "A"
    assert "zonage_urbanisme" in essentiel_en_dict(agricole)["limites"]


# Constaté en réel : l'API Carto a répondu 500 à chaque essai et fait planter le diagnostic
def test_persistent_server_error_becomes_a_clean_failure(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(status_code=500, is_reusable=True)

    with pytest.raises(UrbanismeError):
        lire_zone_urbanisme(latitude=48.6, longitude=2.3, settings=reglages)
