"""Réseau de transport autour du site : postes RTE et contraintes prospectives.

Données synthétiques décrites dans `medallion.py` : postes repérés par le nom de
leur département, contraintes localisées par leurs postes. Aucun appel réseau.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from ingestion.datasets import CONTRAINTES_OUVRAGES
from ingestion.diagnostic import _duree, formater_diagnostic, lire_diagnostic
from ingestion.tests.medallion import DBT_BIN, contraintes_ouvrages
from ingestion.tests.test_diagnostic import territoire
from ingestion.transform import records_to_table, write_parquet
from ingestion.warehouse import DuckDBLoader

dbt_requis = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

MAINTENANT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _lignes(medallion_db: Path, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        return connection.execute(sql).fetchall()


# --- Bronze : clé à postes optionnels ---------------------------------------------------


def test_missing_optional_key_columns_become_empty_strings() -> None:
    table = records_to_table(
        contraintes_ouvrages(), spec=CONTRAINTES_OUVRAGES, ingested_at=MAINTENANT
    )
    lignes = table.select(["ouvrage", "poste_2", "poste_3"]).to_pylist()
    assert len(lignes) == 4  # aucune contrainte écartée faute de deuxième poste
    assert {"ouvrage": "ORPHELINE", "poste_2": "", "poste_3": ""} in lignes


def test_record_without_its_required_key_is_still_dropped() -> None:
    sans_ouvrage = {**contraintes_ouvrages()[0], "ouvrage": None}
    table = records_to_table([sans_ouvrage], spec=CONTRAINTES_OUVRAGES, ingested_at=MAINTENANT)
    assert table.num_rows == 0


def test_reloading_constraints_is_idempotent(tmp_path: Path) -> None:
    """`'' = ''` est vrai, `NULL = NULL` ne l'est pas : la chaîne vide rend le MERGE stable."""
    table = records_to_table(
        contraintes_ouvrages(), spec=CONTRAINTES_OUVRAGES, ingested_at=MAINTENANT
    )
    chemin = write_parquet(
        table, dataset_dir=tmp_path / "bronze", run_id="t", ingest_date=MAINTENANT.date()
    )
    base = tmp_path / "base.duckdb"
    with DuckDBLoader(base, spec=CONTRAINTES_OUVRAGES) as loader:
        premier = loader.merge_parquet(chemin)
        second = loader.merge_parquet(chemin)
    assert (premier.as_dict()["rows_inserted"], second.as_dict()["rows_inserted"]) == (4, 0)
    with duckdb.connect(str(base), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM bronze.contraintes_region").fetchone() == (
            4,
        )


# --- Silver et gold -----------------------------------------------------------------------


@dbt_requis
def test_substations_get_their_department_code_from_its_name(medallion_db: Path) -> None:
    postes = dict(
        (code, (departement, domaine))
        for code, departement, domaine in _lignes(
            medallion_db,
            "SELECT code_poste, code_departement, domaine_tension FROM silver.postes_rte",
        )
    )
    assert postes["LYON1"] == ("69", "HTB1")
    assert postes["LYON2"] == ("69", "HTB2")
    assert postes["LYON3"] == ("69", "HTB3")  # « RHONE » sans accent retrouvé aussi
    assert postes["BOURG"] == ("01", "HTB1")
    assert postes["PIQUA"] == (None, None)  # pas de département publié, tension < 45 kV


@dbt_requis
def test_constraints_classes_are_ranked(medallion_db: Path) -> None:
    lignes = _lignes(
        medallion_db,
        "SELECT ouvrage, poste_2, poste_3, rang_occurrence, rang_perennite, est_en_journee,"
        " code_insee_region FROM silver.contraintes_ouvrages ORDER BY ouvrage, poste_3 NULLS FIRST",
    )
    assert lignes == [
        ("BOURGLYON1", "LYON1", None, 4, 3, False, "84"),
        ("LYON2LYON3", "LYON3", None, 2, 1, False, "84"),
        ("LYON2LYON3", "LYON3", "LYON1", 5, 2, True, "84"),
        ("ORPHELINE", None, None, 2, 1, False, "84"),
    ]


@dbt_requis
def test_constraints_are_attached_to_every_department_of_their_substations(
    medallion_db: Path,
) -> None:
    rattachements = _lignes(
        medallion_db,
        "SELECT code_departement, ouvrage, poste_3 FROM gold.fct_contraintes_departement"
        " ORDER BY code_departement, ouvrage, poste_3 NULLS FIRST",
    )
    assert rattachements == [
        ("01", "BOURGLYON1", None),  # liaison Ain-Rhône, comptée dans les deux
        ("69", "BOURGLYON1", None),
        ("69", "LYON2LYON3", None),
        ("69", "LYON2LYON3", "LYON1"),
    ]  # ORPHELINE : postes inconnus, rattachée à aucun département


@dbt_requis
def test_substations_are_counted_by_voltage_domain(medallion_db: Path) -> None:
    lignes = _lignes(
        medallion_db,
        "SELECT code_departement, nb_postes, nb_postes_htb1, nb_postes_htb2, nb_postes_htb3,"
        " nb_postes_en_projet FROM gold.fct_postes_rte_departement"
        " WHERE code_departement IN ('01', '69', '2A') ORDER BY code_departement",
    )
    assert lignes == [("01", 1, 1, 0, 0, 0), ("2A", 0, 0, 0, 0, 0), ("69", 3, 1, 1, 1, 1)]


# --- Diagnostic ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duree", "texte"),
    [("]4h-6h]", "épisodes de 4 à 6 h"), ("]0h-2h]", "épisodes de moins de 2 h"), ("", "")],
)
def test_episode_length_is_written_in_plain_french(duree: str, texte: str) -> None:
    assert _duree(duree) == texte


@dbt_requis
def test_diagnostic_lists_the_most_frequent_constraints_first(medallion_db: Path) -> None:
    texte = formater_diagnostic(lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db))

    assert (
        "Contraintes prospectives (RTE, d'ici 3 à 5 ans) : 3 sur des ouvrages reliés aux postes"
        " du département, dont 2 plus de 75 fois par an." in texte
    )
    plus_frequentes = texte[texte.index("Les plus fréquentes : ") :].splitlines()[0]
    assert plus_frequentes.index("très forte") < plus_frequentes.index("forte : entre 75")
    assert plus_frequentes.index("forte : entre 75") < plus_frequentes.index("faible")
    assert "épisodes de 4 à 6 h, pérennité moyenne, en journée" in plus_frequentes
    assert (
        "Postes RTE en exploitation dans le département : 3, dont 1 en HTB1 (63 à 150 kV),"
        " 1 en HTB2 (225 kV) et 1 en HTB3 (400 kV) ; 1 en projet" in texte
    )


@dbt_requis
def test_department_without_constraint_says_so(medallion_db: Path) -> None:
    texte = formater_diagnostic(
        lire_diagnostic(territoire("02003", "Aisne"), database=medallion_db)
    )
    assert "aucune sur les ouvrages reliés aux postes du département" in texte
    assert "Postes RTE dans le département : aucun." in texte


@dbt_requis
def test_non_interconnected_territory_is_outside_rte(medallion_db: Path) -> None:
    texte = formater_diagnostic(lire_diagnostic(territoire("2A004"), database=medallion_db))
    assert "le réseau de ce territoire n'est pas exploité par RTE" in texte
    assert "Postes RTE dans le département : aucun (réseau de ce territoire non exploité" in texte
    assert "Les plus fréquentes" not in texte
