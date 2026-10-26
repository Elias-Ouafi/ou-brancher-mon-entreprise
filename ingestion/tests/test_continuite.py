from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import diagnostic_en_dict, formater_diagnostic, lire_diagnostic
from ingestion.extract_enedis import (
    EnedisError,
    colonnes_annees,
    lire_continuite,
    telecharger_continuite,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

ENEDIS_URL = "https://opendata.enedis.test/data-fair/api/v1/datasets/"
JEU = f"{ENEDIS_URL}indicateur-continuite-dalimentation"

avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def test_year_columns_come_from_the_schema() -> None:
    schema = [
        {"key": "intitule_departement"},
        {"key": "aaa2024"},
        {"key": "ndeg_departement"},
        {"key": "aaa2009"},
    ]
    assert colonnes_annees(schema) == ["aaa2009", "aaa2024"]
    with pytest.raises(EnedisError, match="aucune colonne"):
        colonnes_annees([{"key": "ndeg_departement"}])


# Une ligne par département et par année ; une année non publiée reste absente
def test_departments_are_unpivoted_by_year() -> None:
    contenu = '"N° Département","aaa2023","aaa2024"\n"01",0.59,\n"69",1.5,2.25\n'.encode(
        "utf-8-sig"
    )
    lignes = lire_continuite(contenu, ["aaa2023", "aaa2024"])
    assert lignes == [
        {"code_departement": "01", "annee": "2023", "part_mal_alimentes_pct": 0.59},
        {"code_departement": "69", "annee": "2023", "part_mal_alimentes_pct": 1.5},
        {"code_departement": "69", "annee": "2024", "part_mal_alimentes_pct": 2.25},
    ]
    with pytest.raises(EnedisError, match="en-tete"):
        lire_continuite(b"a,b\n", ["aaa2023", "aaa2024"])


def test_download_selects_every_published_year(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(
        url=JEU, json={"schema": [{"key": "ndeg_departement"}, {"key": "aaa2024"}]}
    )
    httpx_mock.add_response(
        url=re.compile(re.escape(f"{JEU}/lines?") + ".*select=ndeg_departement%2Caaa2024"),
        content=b'"N\xc2\xb0 D\xc3\xa9partement","aaa2024"\n"69",2.5\n',
    )
    reglages = settings.model_copy(update={"enedis_base_url": ENEDIS_URL})
    assert telecharger_continuite(settings=reglages) == [
        {"code_departement": "69", "annee": "2024", "part_mal_alimentes_pct": 2.5}
    ]


# Rhône : dernière année 2,5 %, moyenne 2020-2024 de 2,2 %, une année au-delà de 3 % ;
# l'Isère, sans 2024, n'entre pas dans la médiane de 2024 (0,5 ; 2,5 ; 4,0)
@avec_dbt
def test_gold_compares_each_department_to_the_threshold(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        lignes = connection.execute(
            "select code_departement, derniere_annee, part_mal_alimentes_pct,"
            " moyenne_5_ans_pct, annees_au_dela_seuil_5_ans, mediane_nationale_pct,"
            " depasse_seuil from gold.fct_continuite_departement order by code_departement"
        ).fetchall()
    par_code = {ligne[0]: ligne[1:] for ligne in lignes}
    assert par_code["69"] == (2024, 2.5, pytest.approx(2.2), 1, 2.5, False)
    assert par_code["01"][-1] is True
    assert par_code["38"][0] == 2023


@avec_dbt
def test_diagnose_shows_supply_quality(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    texte = formater_diagnostic(diagnostic)
    assert (
        "Qualité de fourniture (Enedis, 2024) : 2,5 % des clients du département au-delà des"
        " seuils de coupure (médiane des départements : 2,5 %), sous le seuil réglementaire de"
        " 3 % ; 2,2 % en moyenne sur cinq ans, seuil dépassé 1 année(s) sur cinq." in texte
    )
    assert diagnostic.limites["continuite_alimentation"] in texte
    assert diagnostic_en_dict(diagnostic)["continuite"]["depasse_seuil"] is False

    ain = formater_diagnostic(lire_diagnostic(territoire("01053"), database=medallion_db))
    assert "au-dessus du seuil réglementaire de 3 %" in ain


# Corse et outre-mer : Enedis ne publie rien, le diagnostic le dit au lieu de se taire
@avec_dbt
@pytest.mark.parametrize("commune", ["2A004", "97411"])
def test_outside_enedis_territory_is_not_published(medallion_db: Path, commune: str) -> None:
    diagnostic = lire_diagnostic(territoire(commune), database=medallion_db)
    assert diagnostic.continuite is None
    assert "Qualité de fourniture : non publiée pour ce département" in formater_diagnostic(
        diagnostic
    )
