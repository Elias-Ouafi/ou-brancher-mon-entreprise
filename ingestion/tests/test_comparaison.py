"""Comparaison d'adresses côte à côte : lignes, éléments communs, limites, rendus et CLI.

Base synthétique de `medallion.py` ; la BAN est simulée. Aucun appel réseau.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli
from ingestion.comparaison import (
    communs,
    comparaison_en_csv,
    comparaison_en_json,
    comparaison_en_markdown,
    formater_comparaison,
    lignes,
    limites,
)
from ingestion.config import Settings
from ingestion.diagnostic import Diagnostic, lire_diagnostic
from ingestion.georisques import lire_gaspar, lire_rapport
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

dbt_requis = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def _rapport_lyon():
    return lire_rapport(
        json.loads((FIXTURES_DIR / "georisques_rapport_lyon.json").read_text(encoding="utf-8"))
    )


def _diagnostics(medallion_db: Path, **options: Any) -> list[Diagnostic]:
    """Lyon (sites, contraintes, postes) et la Corse-du-Sud (hors réseau RTE)."""
    return [
        lire_diagnostic(
            territoire("69123", "Lyon", latitude=45.7637, longitude=4.8357),
            database=medallion_db,
            **options,
        ),
        lire_diagnostic(
            territoire("2A004", "Ajaccio", latitude=41.9192, longitude=8.7386),
            database=medallion_db,
            **options,
        ),
    ]


def _valeurs(tableau: list[Any], indicateur: str) -> list[str]:
    return next(ligne.valeurs for ligne in tableau if ligne.indicateur == indicateur)


# --- Contenu du tableau --------------------------------------------------------------------


@dbt_requis
def test_rows_depend_on_the_address_only(medallion_db: Path) -> None:
    tableau = lignes(_diagnostics(medallion_db))

    assert [ligne.indicateur for ligne in tableau] == [
        "Territoire",
        "Géocodage",
        "Industrie : commune",
        "Industrie : département",
        "Région : couverture",
        "Contraintes RTE (3-5 ans)",
        "Postes RTE du département",
        "Zone prioritaire RTE",
        "Site clé en main (France 2030)",
        "Électricité décarbonée",
    ]  # pas de risques : Géorisques n'a pas été consulté
    assert _valeurs(tableau, "Contraintes RTE (3-5 ans)") == [
        "3, dont 2 plus de 75 fois par an",
        "réseau hors RTE",
    ]
    assert _valeurs(tableau, "Électricité décarbonée") == [
        "oui, 51,9 gCO₂e/kWh (France continentale)",
        "non, 594 gCO₂e/kWh (Corse)",
    ]
    assert _valeurs(tableau, "Postes RTE du département") == [
        "3 (HTB1 1, HTB2 1, HTB3 1)",
        "aucun (réseau hors RTE)",
    ]
    lyon, corse = _valeurs(tableau, "Zone prioritaire RTE")
    assert lyon.startswith("Vallée de la chimie (P2), à 7 km ; capacité 2029")
    assert corse == "aucune (réseau hors RTE)"


@dbt_requis
def test_risk_rows_appear_once_georisques_was_asked(medallion_db: Path) -> None:
    lyon, corse = _diagnostics(medallion_db)
    gaspar = json.loads((FIXTURES_DIR / "georisques_gaspar_ajaccio.json").read_text("utf-8"))
    diagnostics = [
        replace(lyon, risques=_rapport_lyon()),
        replace(
            corse,
            risques=replace(
                _rapport_lyon(), risques=lire_gaspar(gaspar, {"2A004"}), maille="commune"
            ),
        ),
    ]
    tableau = lignes(diagnostics)

    naturels = _valeurs(tableau, "Risques naturels")
    assert naturels[0].startswith("Inondation, Remontée de nappe, Séisme (faible)")
    assert naturels[1].startswith("commune seulement : Feu de forêt")
    assert _valeurs(tableau, "Non évalués à l'adresse") == ["Rupture de barrage", "—"]


@dbt_requis
def test_unreachable_georisques_is_said_in_the_table(medallion_db: Path) -> None:
    diagnostics = [replace(d, risques_erreur="coupure") for d in _diagnostics(medallion_db)]
    assert (
        _valeurs(lignes(diagnostics), "Risques naturels")
        == ["non évalués (Géorisques injoignable)"] * 2
    )


@dbt_requis
def test_address_independent_figures_are_given_once(medallion_db: Path) -> None:
    diagnostics = _diagnostics(medallion_db, puissance_mw=12, conso_mwh=350)
    texte = "\n".join(communs(diagnostics))

    assert texte.count("Puissance 12 MW → HTA") == 1
    assert "Prix de l'électricité pour l'entreprise" in texte
    assert "Carbone du réseau et coût horaire d'un profil type" in texte
    # Le verdict carbone dépend du territoire : il n'est pas répété sous le tableau.
    assert "Électricité du réseau" not in texte
    # Une adresse en Corse : les prix communs valent pour la métropole continentale.
    assert "valent pour la métropole continentale" in texte


@dbt_requis
def test_every_limit_is_listed_once(medallion_db: Path) -> None:
    diagnostics = _diagnostics(medallion_db, puissance_mw=12)
    diagnostics = [replace(d, risques=_rapport_lyon()) for d in diagnostics]
    tableau = lignes(diagnostics)
    codes = [code for code, _ in limites(diagnostics, tableau)]

    assert len(codes) == len(set(codes))
    assert {"pression_industrielle", "zones_raccordement_rte", "risques_site",
            "capacite_raccordement", "prix_entreprise"} <= set(codes)  # fmt: skip


# --- Rendus ---------------------------------------------------------------------------------


@dbt_requis
def test_text_table_wraps_into_columns(medallion_db: Path) -> None:
    texte = formater_comparaison(_diagnostics(medallion_db), largeur=100)

    assert texte.startswith("COMPARAISON DE 2 ADRESSES (sans classement)")
    lignes_tableau = texte[: texte.index("IDENTIQUE POUR TOUTES")].splitlines()
    assert max(len(ligne) for ligne in lignes_tableau) <= 100
    assert "LIMITES" in texte


@dbt_requis
def test_markdown_csv_and_json_carry_the_same_rows(medallion_db: Path) -> None:
    diagnostics = _diagnostics(medallion_db)
    nb_lignes = len(lignes(diagnostics))

    markdown = comparaison_en_markdown(diagnostics)
    assert markdown.count("\n| ") == nb_lignes + 1  # en-tête et une ligne par indicateur

    lecteur = list(csv.reader(io.StringIO(comparaison_en_csv(diagnostics)), delimiter=";"))
    assert lecteur[0] == ["indicateur", "1 rue de test, Lyon", "1 rue de test, Ajaccio", "limite"]
    assert len(lecteur) == nb_lignes + 1
    assert lecteur[1][3] == ""  # le territoire n'a pas de limite
    assert lecteur[3][3].startswith("Ne compte que les sites raccordés")

    contenu = json.loads(comparaison_en_json(diagnostics))
    assert len(contenu["tableau"]) == nb_lignes
    assert len(contenu["diagnostics"]) == 2


def test_gaspar_subtypes_read_alone_get_their_type() -> None:
    payload = {
        "data": [
            {
                "code_insee": "69123",
                "risques_detail": [
                    {"num_risque": "11", "libelle_risque_long": "Inondation"},
                    {"num_risque": "112", "libelle_risque_long": "Par une crue lente"},
                    {"num_risque": "17", "libelle_risque_long": "Phénomène atmosphérique"},
                    {"num_risque": "172", "libelle_risque_long": "Tempête et grains (vent)"},
                ],
            }
        ]
    }
    assert [r.libelle for r in lire_gaspar(payload, {"69123"})] == [
        "Inondation par une crue lente",
        "Tempête et grains (vent)",
    ]


# --- Ligne de commande ----------------------------------------------------------------------


def _ban(httpx_mock: HTTPXMock, label: str, citycode: str, city: str, context: str) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [4.85, 45.75]},
                    "properties": {
                        "label": label,
                        "score": 0.95,
                        "type": "housenumber",
                        "citycode": citycode,
                        "city": city,
                        "postcode": "00000",
                        "context": context,
                    },
                }
            ],
        }
    )


@pytest.fixture
def reglages(settings: Settings, medallion_db: Path) -> Settings:
    return settings.model_copy(
        update={"ban_base_url": "https://ban.test/geocodage", "duckdb_path": medallion_db}
    )


@dbt_requis
def test_compare_command_writes_a_spreadsheet(
    httpx_mock: HTTPXMock, reglages: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _ban(httpx_mock, "10 Rue A 69003 Lyon", "69383", "Lyon", "69, Rhône, Auvergne-Rhône-Alpes")
    _ban(httpx_mock, "1 Cours B 20000 Ajaccio", "2A004", "Ajaccio", "2A, Corse-du-Sud, Corse")
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)
    sortie = tmp_path / "comparaison.csv"

    code = cli.main(
        ["compare", "--adresse", "A", "--adresse", "B", "--sans-risques",
         "--format", "csv", "--output", str(sortie)]
    )  # fmt: skip

    assert code == 0
    assert sortie.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM : Excel lit l'UTF-8
    entete = sortie.read_text(encoding="utf-8-sig").splitlines()[0]
    assert entete == "indicateur;10 Rue A 69003 Lyon;1 Cours B 20000 Ajaccio;limite"


@pytest.mark.parametrize("nb", [1, 7])
def test_compare_needs_two_to_six_addresses(nb: int) -> None:
    arguments = ["compare", "--sans-risques"]
    for i in range(nb):
        arguments += ["--adresse", f"adresse {i}"]
    assert cli.main(arguments) == 1


@dbt_requis
def test_compare_names_the_address_it_cannot_geocode(
    httpx_mock: HTTPXMock,
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ban(httpx_mock, "10 Rue A 69003 Lyon", "69383", "Lyon", "69, Rhône, Auvergne-Rhône-Alpes")
    httpx_mock.add_response(json={"type": "FeatureCollection", "features": []})
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    assert cli.main(["compare", "--adresse", "A", "--adresse", "nulle part", "--sans-risques"]) == 1
    assert "'nulle part'" in caplog.text
