from __future__ import annotations

import json
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli, diagnostic
from ingestion.config import Settings
from ingestion.diagnostic import Diagnostic, formater_diagnostic, lire_diagnostic, verdict
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from reporting import app

pytestmark = [
    pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)"),
    # Selon la panne, une source s'arrête au premier appel ou n'en fait aucun
    pytest.mark.httpx_mock(
        assert_all_responses_were_requested=False, can_send_already_matched_responses=True
    ),
]

LYON = territoire("69123", "Lyon", latitude=45.76, longitude=4.83)

# (source à la demande, champ qui porte son échec dans le diagnostic)
SOURCES = [
    ("completer_risques", "risques_erreur"),
    ("completer_voisinage", "voisinage_erreur"),
    ("completer_urbanisme", "urbanisme_erreur"),
    ("completer_eau", "eau_erreur"),
    ("completer_solaire", "solaire_erreur"),
    ("completer_acces_routier", "acces_routier_erreur"),
    ("completer_zones_activite", "zones_activite_erreur"),
]


def _delai(mock: HTTPXMock) -> None:
    mock.add_exception(httpx.ReadTimeout("délai dépassé"))


def _coupure(mock: HTTPXMock) -> None:
    mock.add_exception(httpx.RemoteProtocolError("Server disconnected"))


def _erreur_500(mock: HTTPXMock) -> None:
    mock.add_response(status_code=500)


def _introuvable(mock: HTTPXMock) -> None:
    mock.add_response(status_code=404, json={"message": "not found"})


def _sans_corps(mock: HTTPXMock) -> None:
    mock.add_response(status_code=200, content=b"")


def _page_de_maintenance(mock: HTTPXMock) -> None:
    mock.add_response(status_code=200, content=b"<html>Maintenance</html>")


def _forme_inattendue(mock: HTTPXMock) -> None:
    mock.add_response(json={"inattendu": True})


def _liste_vide(mock: HTTPXMock) -> None:
    mock.add_response(json=[])


# Le service ne répond pas, ou pas en JSON : l'échec doit être dit dans le diagnostic
PANNES_FRANCHES = [_delai, _coupure, _erreur_500, _sans_corps, _page_de_maintenance]
# Le service répond un JSON inattendu : un échec ou « rien trouvé », jamais une donnée inventée
REPONSES_DOUTEUSES = [_introuvable, _forme_inattendue, _liste_vide]


@pytest.fixture
def reglages(medallion_db: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=medallion_db,
        bronze_dir=tmp_path / "bronze",
        reports_dir=tmp_path / "reports",
        retry_wait_seconds=0.0,
    )


@pytest.fixture
def projet(reglages: Settings) -> Diagnostic:
    return lire_diagnostic(LYON, puissance_mw=12, conso_mwh=70_000, settings=reglages)


def _etats(d: Diagnostic) -> list[tuple[str, str]]:
    return [(critere.nom, critere.etat) for critere in verdict(d)]


@pytest.mark.parametrize(("source", "champ"), SOURCES)
@pytest.mark.parametrize("panne", PANNES_FRANCHES + REPONSES_DOUTEUSES)
def test_une_source_en_panne_ne_casse_ni_le_diagnostic_ni_le_verdict(
    httpx_mock: HTTPXMock,
    reglages: Settings,
    projet: Diagnostic,
    source: str,
    champ: str,
    panne: Callable[[HTTPXMock], None],
) -> None:
    panne(httpx_mock)
    complete = getattr(diagnostic, source)(projet, settings=reglages)

    # Sans donnée nouvelle, aucun critère ne change d'état
    assert _etats(complete) == _etats(diagnostic.avec_risques_locaux(projet, settings=reglages))
    assert "Brancher ici ?" in formater_diagnostic(complete)
    if panne not in PANNES_FRANCHES:
        return
    # Risques : le repli sur l'export GASPAR local dit l'échec par sa maille
    replie = source == "completer_risques" and complete.risques.maille == "commune"
    assert replie or getattr(complete, champ), f"{source} : échec passé sous silence"


@pytest.mark.parametrize("panne", PANNES_FRANCHES + REPONSES_DOUTEUSES)
@pytest.mark.parametrize("commande", ["diagnose", "compare"])
def test_toutes_les_sources_en_panne_la_commande_aboutit(
    httpx_mock: HTTPXMock,
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    commande: str,
    panne: Callable[[HTTPXMock], None],
) -> None:
    panne(httpx_mock)
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)
    monkeypatch.setattr(cli, "geocode", lambda *args, **kwargs: LYON)
    adresses = ["--adresse", "Lyon"] * (2 if commande == "compare" else 1)
    suite = ["--sans-carte"] if commande == "diagnose" else []

    assert cli.main([commande, *adresses, "--puissance", "12", "--conso", "70000", *suite]) == 0
    assert "Brancher ici ?" in capsys.readouterr().out


@pytest.mark.parametrize("panne", PANNES_FRANCHES + REPONSES_DOUTEUSES)
def test_toutes_les_sources_en_panne_l_application_repond(
    httpx_mock: HTTPXMock,
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    panne: Callable[[HTTPXMock], None],
) -> None:
    panne(httpx_mock)
    monkeypatch.setattr(app, "geocode", lambda *args, **kwargs: LYON)
    monkeypatch.setattr(app.carte, "mettre_a_jour_carte", lambda **kwargs: Path("carte.html"))
    corps = json.dumps({"adresse": "Lyon", "puissance_mw": 12, "complet": True}).encode()

    reponse = app.traiter("POST", "/api/analyse", corps, reglages)
    assert reponse.statut == HTTPStatus.OK
    assert "Brancher ici ?" in json.loads(reponse.corps)["adresses"][0]["texte"]
