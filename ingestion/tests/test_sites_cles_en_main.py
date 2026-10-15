"""Sites industriels clés en main (France 2030) : seed, site le plus proche, bloc 3.

Le seed est la vraie liste de l'ANCT (`dbt/seeds/sites_cles_en_main.csv`),
construite dans la base synthétique comme tous les seeds.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from ingestion.comparaison import lignes
from ingestion.diagnostic import formater_diagnostic, lire_diagnostic
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

dbt_requis = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

LYON = (45.7637, 4.8357)
SAINT_DENIS_REUNION = (-20.8823, 55.4504)


def _diagnostic(medallion_db: Path, code: str, position: tuple[float, float]):
    latitude, longitude = position
    return lire_diagnostic(
        territoire(code, latitude=latitude, longitude=longitude), database=medallion_db
    )


@dbt_requis
def test_seed_holds_the_55_sites_of_the_anct_list(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        total, hors_metropole = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE code_departement LIKE '97%')"
            " FROM gold.sites_cles_en_main"
        ).fetchone()
    assert (total, hors_metropole) == (55, 1)  # un seul site outre-mer : Saint-Paul (974)


@dbt_requis
def test_nearest_site_and_its_maturity(medallion_db: Path) -> None:
    site = _diagnostic(medallion_db, "69123", LYON).site_cle_en_main

    assert site is not None
    assert (site.commune, site.maturite_site) == ("Salaise-sur-Sanne", "2026 - 2027")
    assert site.distance_km == pytest.approx(47, abs=2)


@dbt_requis
def test_overseas_address_only_looks_at_its_own_territory(medallion_db: Path) -> None:
    reunion = _diagnostic(medallion_db, "97411", SAINT_DENIS_REUNION).site_cle_en_main
    assert reunion is not None and reunion.commune == "Saint-Paul"
    assert reunion.distance_km < 30

    # La Corse n'a aucun site : le plus proche du continent ne compte pas.
    assert _diagnostic(medallion_db, "2A004", (41.9192, 8.7386)).site_cle_en_main is None


@dbt_requis
def test_mainland_address_never_gets_an_overseas_site(medallion_db: Path) -> None:
    # Un point au large de l'océan Indien, rattaché au Rhône : le site retenu reste en métropole.
    site = _diagnostic(medallion_db, "69123", SAINT_DENIS_REUNION).site_cle_en_main
    assert site is not None and site.code_insee_commune != "97415"


@dbt_requis
def test_block_3_and_comparison_show_the_site_with_its_limit(medallion_db: Path) -> None:
    diagnostic = _diagnostic(medallion_db, "69123", LYON)
    texte = formater_diagnostic(diagnostic)

    assert (
        "Site industriel clé en main (France 2030) le plus proche : Salaise-sur-Sanne, à 47 km ;"
        " annoncé prêt en 2026 - 2027" in texte
    )
    assert diagnostic.limites["sites_cles_en_main"] in texte

    corse = _diagnostic(medallion_db, "2A004", (41.9192, 8.7386))
    tableau = lignes([diagnostic, corse])
    valeurs = next(ligne.valeurs for ligne in tableau if ligne.indicateur.startswith("Site clé"))
    assert valeurs == [
        "Salaise-sur-Sanne, à 47 km ; prêt en 2026 - 2027",
        "aucun dans ce territoire",
    ]
