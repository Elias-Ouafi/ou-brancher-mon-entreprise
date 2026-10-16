from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from ingestion.diagnostic import (
    ZoneRte,
    _calendrier_zone,
    _distance,
    formater_diagnostic,
    lire_diagnostic,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

dbt_requis = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

# Positions (latitude, longitude) d'adresses réelles
LYON = (45.7637, 4.8357)
MELUN = (48.5421, 2.6552)
POITIERS = (46.5802, 0.3404)
MONTPELLIER = (43.6085, 3.8800)


def _diagnostic(medallion_db: Path, code: str, position: tuple[float, float]):
    latitude, longitude = position
    return lire_diagnostic(
        territoire(code, latitude=latitude, longitude=longitude), database=medallion_db
    )


@dbt_requis
def test_seed_lists_the_ten_sddr_zones_and_three_p3_examples(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        lignes = connection.execute(
            "SELECT priorite, count(DISTINCT zone), count(*) FROM gold.zones_raccordement_rte"
            " GROUP BY priorite ORDER BY priorite"
        ).fetchall()
    # P2 : 7 zones, dont Sud Île-de-France sur deux points (Essonne, Seine-et-Marne)
    assert lignes == [("P1", 3, 3), ("P2", 7, 8), ("P3", 3, 3)]


@dbt_requis
def test_nearest_priority_zone_and_its_calendar(medallion_db: Path) -> None:
    diagnostic = _diagnostic(medallion_db, "69123", LYON)
    zone = diagnostic.zone_rte

    assert zone is not None
    assert (zone.zone, zone.priorite, zone.annee_capacite) == ("Vallée de la chimie", "P2", 2029)
    assert zone.distance_km == pytest.approx(7.2, abs=0.5)
    assert diagnostic.zone_rte_p3 is None


@dbt_requis
def test_zone_with_several_points_uses_the_nearest(medallion_db: Path) -> None:
    zone = _diagnostic(medallion_db, "77288", MELUN).zone_rte
    assert zone is not None
    assert (zone.zone, zone.point_reference) == ("Sud Île-de-France", "Melun")
    assert zone.distance_km < 1


@dbt_requis
def test_p3_example_is_shown_only_when_nearer(medallion_db: Path) -> None:
    poitiers = _diagnostic(medallion_db, "86194", POITIERS)
    assert poitiers.zone_rte is not None and poitiers.zone_rte.zone == "Loire-Estuaire"
    assert poitiers.zone_rte_p3 is not None and poitiers.zone_rte_p3.zone == "Châteauroux"

    # Montpellier : Fos-sur-Mer (P1, ~84 km) est plus proche que Port-la-Nouvelle (P3, ~95 km)
    montpellier = _diagnostic(medallion_db, "34172", MONTPELLIER)
    assert montpellier.zone_rte is not None and montpellier.zone_rte.zone == "Fos-sur-Mer"
    assert montpellier.zone_rte_p3 is None


@dbt_requis
def test_block_3_states_zone_calendar_and_limit(medallion_db: Path) -> None:
    diagnostic = _diagnostic(medallion_db, "86194", POITIERS)
    texte = formater_diagnostic(diagnostic)
    bloc = texte[texte.index("3. Raccordement") : texte.index("4. Prix")]

    assert (
        "Zone industrielle prioritaire de RTE la plus proche : Loire-Estuaire (P2), à 202 km"
        " (Donges) : capacité supplémentaire possible à partir de 2030, si les projets"
        " industriels de la zone se concrétisent" in bloc
    )
    p3 = diagnostic.zone_rte_p3
    assert p3 is not None
    assert f"Plus proche encore, Châteauroux ({_distance(p3.distance_km)})" in bloc
    assert diagnostic.limites["zones_raccordement_rte"] in bloc


@dbt_requis
def test_non_interconnected_territory_has_no_rte_zone(medallion_db: Path) -> None:
    texte = formater_diagnostic(_diagnostic(medallion_db, "2A004", (41.9192, 8.7386)))
    assert "Zones industrielles prioritaires de RTE : aucune" in texte
    assert "Zone industrielle prioritaire de RTE la plus proche" not in texte


def _zone(priorite: str, annee: int | None) -> ZoneRte:
    return ZoneRte("Z", priorite, annee, "poste", "Commune", 12.0)


def test_p1_and_p2_calendars_differ() -> None:
    assert "annoncée à partir de 2028, travaux lancés" in _calendrier_zone(_zone("P1", 2028))
    assert "possible à partir de 2030, si les projets" in _calendrier_zone(_zone("P2", 2030))


@pytest.mark.parametrize(("km", "texte"), [(0.4, "à moins d'un km"), (7.2, "à 7 km")])
def test_distance_is_rounded_to_the_kilometre(km: float, texte: str) -> None:
    assert _distance(km) == texte
