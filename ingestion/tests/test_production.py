from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_production import (
    REGISTRE_DATASET,
    ProductionError,
    telecharger_centres,
    telecharger_sites,
)
from ingestion.tests.conftest import TEST_BASE_URL
from ingestion.tests.medallion import DBT_BIN

GEO_URL = "https://geo.api.test/communes"


@pytest.fixture
def reglages(settings: Settings) -> Settings:
    return settings.model_copy(update={"geo_communes_url": GEO_URL})


def test_registre_agrege_par_commune_en_mw(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_response(
        json=[
            {
                "code_commune": "59273",
                "commune": "Gravelines",
                "code_departement": "59",
                "filiere": "Nucléaire",
                "puissance_kw": 5460000.0,
                "energie_kwh": 31000000000,
                "nb_installations": 6,
            }
        ]
    )

    [site] = telecharger_sites(settings=reglages)

    assert site["puissance_mw"] == 5460 and site["energie_gwh"] == 31000
    assert site["nb_installations"] == 6
    requete = httpx_mock.get_requests()[0]
    assert str(requete.url).startswith(
        f"{TEST_BASE_URL}/catalog/datasets/{REGISTRE_DATASET}/exports/json"
    )
    # Filtre et agrégation faits par ODRÉ : 2 500 lignes au lieu de 141 000
    assert "regime = 'En service'" in requete.url.params["where"]
    assert requete.url.params["group_by"].startswith("codeinseecommuneimplantation")


def test_centres_des_communes(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_response(
        json=[
            {
                "code": "97411",
                "nom": "Saint-Denis",
                "codeDepartement": "974",
                "centre": {"type": "Point", "coordinates": [55.4444, -20.9434]},
            },
            # Commune sans centre publié : ignorée plutôt que posée en (0, 0)
            {"code": "99999", "nom": "Sans centre", "codeDepartement": "99"},
        ]
    )

    assert telecharger_centres(settings=reglages) == [
        {
            "code_commune": "97411",
            "nom": "Saint-Denis",
            "code_departement": "974",
            "longitude": 55.4444,
            "latitude": -20.9434,
        }
    ]


def test_reponse_inattendue_refusee(httpx_mock: HTTPXMock, reglages: Settings) -> None:
    httpx_mock.add_response(json={"error": "quota"})
    with pytest.raises(ProductionError, match="liste attendue"):
        telecharger_sites(settings=reglages)


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")
def test_gold_garde_les_poles_au_dessus_du_seuil_de_leur_reseau(medallion_db: Path) -> None:
    import duckdb

    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        poles = connection.execute(
            "select code_insee_commune, filiere, puissance_mw, latitude"
            " from gold.fct_sites_production order by code_insee_commune, filiere"
        ).fetchall()

    # Arrondissement rattaché à Lyon, hydraulique additionnée, Corse dès 20 MW ; écartés :
    # éolien sous 200 MW, stockage, commune inconnue, collectivité hors carte
    assert [(c, f, mw) for c, f, mw, _ in poles] == [
        ("2A004", "Thermique non renouvelable", 120.0),
        ("69123", "Hydraulique", 270.0),
        ("69123", "Nucléaire", 3600.0),
    ]
    assert all(latitude for *_, latitude in poles)
