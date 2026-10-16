from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import pytest

from ingestion.tests.medallion import DBT_BIN, JOUR_25H, JOUR_BASCULE, PIC_CO2

pytestmark = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


@pytest.fixture(scope="module")
def carbone(medallion_db: Path) -> Iterator[dict[str, tuple]]:
    connection = duckdb.connect(str(medallion_db), read_only=True)
    lignes = connection.execute(
        "SELECT code_insee_region, zone_carbone, intensite_acv_g_co2e_kwh, est_decarbone,"
        " seuil_decarbone_g_co2e_kwh, intensite_directe_rte_g_co2_kwh, rte_jour_debut,"
        " rte_jour_fin FROM gold.fct_carbone_territoire"
    ).fetchall()
    connection.close()
    yield {ligne[0]: ligne[1:] for ligne in lignes}


def test_une_ligne_par_region(carbone: dict[str, tuple]) -> None:
    assert len(carbone) == 18


def test_continent_decarbone_et_partage_par_ses_douze_regions(carbone: dict[str, tuple]) -> None:
    continent = {code: ligne for code, ligne in carbone.items() if ligne[0] == "continent"}
    assert len(continent) == 12
    assert {ligne[1:4] for ligne in continent.values()} == {(51.9, True, 100.0)}


@pytest.mark.parametrize(
    ("region", "intensite"),
    [("94", 594), ("01", 702), ("02", 840), ("03", 957), ("04", 780), ("06", 780)],
)
def test_zni_non_decarbonees_avec_leur_propre_facteur(
    carbone: dict[str, tuple], region: str, intensite: float
) -> None:
    zone, acv, est_decarbone, _, directe, _, _ = carbone[region]
    assert (zone, acv, est_decarbone) == (region, intensite, False)
    # RTE ne publie pas de taux_co2 pour les ZNI : pas d'intensité observée
    assert directe is None


def test_intensite_rte_sur_les_jours_complets_des_douze_derniers_mois(
    carbone: dict[str, tuple],
) -> None:
    *_, directe, debut, fin = carbone["84"]
    assert (debut, fin) == (JOUR_25H, JOUR_BASCULE)
    assert 20 < directe < PIC_CO2
    assert isinstance(debut, date)


def test_diagnostic_affiche_le_verdict_du_territoire(medallion_db: Path) -> None:
    from ingestion.diagnostic import formater_diagnostic, lire_diagnostic
    from ingestion.tests.test_diagnostic import territoire

    lyon = formater_diagnostic(lire_diagnostic(territoire("69123"), database=medallion_db))
    assert "Électricité du réseau décarbonée (France continentale) : 51,9 gCO₂e/kWh" in lyon
    assert "sous le seuil de 100 gCO₂e/kWh" in lyon
    assert "émissions directes observées par RTE sur 12 mois, jusqu'au 30/06/2026" in lyon

    ajaccio = lire_diagnostic(territoire("2A004"), database=medallion_db)
    assert ajaccio.carbone is not None
    assert (ajaccio.carbone.systeme_electrique, ajaccio.carbone.est_decarbone) == ("Corse", False)
    texte = formater_diagnostic(ajaccio)
    assert "Électricité du réseau non décarbonée (Corse) : 594 gCO₂e/kWh" in texte
    assert "au-dessus du seuil de 100 gCO₂e/kWh" in texte
    assert "observées par RTE" not in texte
