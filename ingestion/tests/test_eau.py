from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_eau,
    essentiel_en_dict,
    formater_diagnostic,
    lire_diagnostic,
)
from ingestion.eau import EauError, SituationEau, ZoneRestriction, lire_situation_eau, lire_zones
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

VIGIEAU_URL = "https://vigieau.test/api/"
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def fixture() -> list[dict[str, Any]]:
    return json.loads((FIXTURES_DIR / "vigieau_zones_chilly.json").read_text(encoding="utf-8"))


def zone(niveau: str, type_zone: str = "SUP") -> ZoneRestriction:
    return ZoneRestriction(type_zone, "Zone", niveau, date(2026, 12, 31), 3, None)


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"vigieau_base_url": VIGIEAU_URL})


def test_real_response_gives_levels_end_date_and_company_usages() -> None:
    eau = lire_zones(fixture())

    assert sorted(z.type_zone for z in eau.zones) == ["AEP", "SOU", "SUP"]
    assert eau.niveau_max == "vigilance" and not eau.est_contraignante
    assert eau.zones[0].nb_usages_entreprise == 3
    assert max(z.date_fin for z in eau.zones if z.date_fin) == date(2026, 12, 31)


def test_no_zone_means_no_restriction() -> None:
    eau = lire_zones([])
    assert eau.niveau_max is None and not eau.est_contraignante


@pytest.mark.parametrize(
    ("niveaux", "maximum", "contraignante"),
    [
        (["vigilance", "alerte"], "alerte", False),
        (["alerte", "alerte_renforcee"], "alerte_renforcee", True),
        (["vigilance", "crise"], "crise", True),
    ],
)
def test_highest_level_decides(niveaux: list[str], maximum: str, contraignante: bool) -> None:
    eau = SituationEau([zone(n) for n in niveaux])
    assert (eau.niveau_max, eau.est_contraignante) == (maximum, contraignante)


@pytest.mark.parametrize("payload", [{}, [{"niveauGravite": "inconnu"}]])
def test_unreadable_payload_is_refused(payload: Any) -> None:
    with pytest.raises(EauError):
        lire_zones(payload)


def test_zones_are_asked_at_the_exact_point_for_companies(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(json=fixture())

    eau = lire_situation_eau(latitude=48.6735, longitude=2.3415, settings=reglages)

    assert len(eau.zones) == 3
    requete = httpx_mock.get_requests()[0]
    assert requete.url.path == "/api/zones"
    assert dict(requete.url.params) == {"lon": "2.3415", "lat": "48.6735", "profil": "entreprise"}


def test_dropped_connections_are_retried(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))
    httpx_mock.add_response(json=[])
    assert lire_situation_eau(latitude=1, longitude=1, settings=reglages).zones == []


@avec_dbt
def test_failure_never_breaks_the_diagnosis(
    httpx_mock: HTTPXMock, reglages: Settings, medallion_db: Path
) -> None:
    httpx_mock.add_response(status_code=400, is_reusable=True)
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    diagnostic = completer_eau(diagnostic, settings=reglages)

    assert diagnostic.eau is None and diagnostic.eau_erreur is not None
    assert "Restrictions d'eau non consultées" in formater_diagnostic(diagnostic)


@avec_dbt
def test_water_line_follows_the_level(medallion_db: Path) -> None:
    base = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    vigilance = replace(base, eau=SituationEau([zone("vigilance")]))
    texte = formater_diagnostic(vigilance)
    assert "Eau : niveau vigilance en vigueur : eau superficielle (vigilance)" in texte
    assert "jusqu'au 31/12/2026" in texte and "appelle surtout à la sobriété" in texte

    crise = replace(base, eau=SituationEau([zone("vigilance", "SOU"), zone("crise")]))
    assert "niveau crise en vigueur" in formater_diagnostic(crise)
    assert "très réduits ou suspendus" in formater_diagnostic(crise)
    assert essentiel_en_dict(crise)["eau"]["est_contraignante"] is True
    assert "restrictions_eau" in essentiel_en_dict(crise)["limites"]

    aucune = replace(base, eau=SituationEau([]))
    assert "aucun arrêté de restriction des usages de l'eau" in formater_diagnostic(aucune)
