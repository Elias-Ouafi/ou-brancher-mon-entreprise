from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ingestion.diagnostic import (
    essentiel_en_dict,
    formater_diagnostic,
    formater_essentiel,
    lire_diagnostic,
    verdict,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from ingestion.turpe import (
    PARIS_TZ,
    PROFIL_COURBE,
    PROFIL_PLAT,
    Acheminement,
    classe_temporelle,
    parts_energie,
    parts_profil_plat,
)

avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


@pytest.mark.parametrize(
    ("instant", "classe"),
    [
        (datetime(2027, 1, 12, 9), "pointe"),
        (datetime(2027, 1, 12, 19), "pointe"),
        # Dimanche : pas de pointe
        (datetime(2027, 1, 10, 9), "hph"),
        # Novembre et mars : saison haute sans pointe
        (datetime(2026, 11, 3, 9), "hph"),
        (datetime(2027, 3, 2, 23), "hch"),
        (datetime(2027, 1, 12, 5), "hch"),
        (datetime(2026, 8, 3, 12), "hpb"),
        (datetime(2026, 10, 30, 22), "hcb"),
    ],
)
def test_time_class_follows_the_turpe_calendar(instant: datetime, classe: str) -> None:
    assert classe_temporelle(instant.replace(tzinfo=PARIS_TZ)) == classe


# Une année de grille, du 1er août 2026 au 31 juillet 2027 : 8 760 heures
def test_flat_profile_counts_every_hour_of_the_tariff_year() -> None:
    parts = parts_profil_plat(date(2026, 8, 1))
    heures = {classe: round(part * 8760) for classe, part in parts.items()}
    assert heures == {"pointe": 308, "hph": 2108, "hch": 1207, "hpb": 3424, "hcb": 1713}


# Les instants de la courbe sont en UTC : 8 h UTC en janvier, c'est 9 h à Paris, en pointe
def test_load_curve_energy_is_split_in_paris_time() -> None:
    debut = datetime(2027, 1, 12, 8, tzinfo=UTC)
    parts = parts_energie([(debut, 3.0), (debut + timedelta(hours=14), 1.0)])
    assert parts["pointe"] == pytest.approx(0.75)
    assert parts["hch"] == pytest.approx(0.25)


# Nouvelle grille chaque 1er août : celle du 1er août 2026 vaut jusqu'au 31 juillet 2027
@pytest.mark.parametrize(
    ("aujourd_hui", "a_reverifier"),
    [(date(2026, 10, 26), False), (date(2027, 7, 31), False), (date(2027, 8, 1), True)],
)
def test_turpe_grid_is_flagged_after_each_august_update(
    aujourd_hui: date, a_reverifier: bool
) -> None:
    acheminement = Acheminement(
        "hta", "longue", date(2026, 8, 1), 1000, 4000, 0, 0, 0, 0, 0, PROFIL_PLAT
    )
    assert acheminement.a_reverifier(aujourd_hui) is a_reverifier


# 1 MW en HTA, 4 000 MWh : longue utilisation (36 400 € de puissance, 53 281 € d'énergie,
# 832,93 € de gestion et comptage) moins chère que la courte (99 074 €)
@avec_dbt
def test_hta_routing_cost_picks_the_cheapest_version(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon"), puissance_mw=1, conso_mwh=4000, database=medallion_db
    )
    a = diagnostic.acheminement
    assert a is not None
    assert (a.domaine_turpe, a.utilisation, a.profil) == ("hta", "longue", PROFIL_PLAT)
    assert a.gestion_comptage_eur == pytest.approx(832.93)
    assert a.puissance_eur == pytest.approx(36_400)
    assert a.energie_eur == pytest.approx(53_281.2, abs=0.5)
    assert a.prix_eur_mwh == pytest.approx(22.63, abs=0.01)
    texte = formater_essentiel(diagnostic)
    assert "Acheminement : environ 90 500 € HTVA par an, soit 22,6 €/MWh" in texte
    assert "TURPE 7 HTA, longue utilisation" in texte
    complet = formater_diagnostic(diagnostic)
    assert complet.count("Acheminement : environ") == 1
    assert "Acheminement, détail : 36 400 € pour la puissance souscrite, 53 300 €" in complet
    facture = next(c for c in verdict(diagnostic) if c.nom == "Facture")
    assert "Dont acheminement 22,6 €/MWh" in facture.detail
    reponse = essentiel_en_dict(diagnostic)
    assert reponse["acheminement"]["total_eur"] == pytest.approx(a.total_eur)
    assert "acheminement_turpe" in reponse["limites"]


# En BT > 36 kVA, peu d'heures d'utilisation : la courte utilisation l'emporte
@avec_dbt
@pytest.mark.parametrize(
    ("conso", "utilisation", "total"), [(50, "courte", 4191.8), (300, "longue", 13281.5)]
)
def test_low_voltage_version_follows_hours_of_use(
    medallion_db: Path, conso: float, utilisation: str, total: float
) -> None:
    a = lire_diagnostic(
        territoire("69123"), puissance_mw=0.1, conso_mwh=conso, database=medallion_db
    ).acheminement
    assert a is not None
    assert (a.domaine_turpe, a.utilisation) == ("bt_sup36", utilisation)
    assert a.total_eur == pytest.approx(total, abs=0.5)


# Tarif bleu (acheminement inclus), réseau de transport ou consommation inconnue : rien à chiffrer
@avec_dbt
@pytest.mark.parametrize(
    ("puissance", "conso", "message"),
    [
        (0.03, 100, None),
        (100, 100_000, "grille TURPE HTB de RTE, non chiffrée ici"),
        (1, None, "préciser --conso (MWh par an) pour le chiffrer"),
    ],
)
def test_routing_cost_is_only_computed_on_the_distribution_grid(
    medallion_db: Path, puissance: float, conso: float | None, message: str | None
) -> None:
    diagnostic = lire_diagnostic(
        territoire("69123"), puissance_mw=puissance, conso_mwh=conso, database=medallion_db
    )
    assert diagnostic.acheminement is None
    texte = formater_diagnostic(diagnostic)
    if message is None:
        assert "Acheminement" not in texte
    else:
        assert f"Acheminement : {message}" in texte


# Une courbe de charge répartit l'énergie réelle entre les classes, à la place du profil plat
@avec_dbt
def test_load_curve_replaces_the_flat_profile(medallion_db: Path) -> None:
    from ingestion.courbe import Courbe

    debut = datetime(2027, 1, 11, tzinfo=UTC)
    # Une semaine d'hiver à 200 kW, la nuit seulement (22 h – 6 h à Paris)
    points = tuple(
        (instant, 200.0 if not 6 <= instant.astimezone(PARIS_TZ).hour < 22 else 0.0)
        for instant in (debut + timedelta(hours=h) for h in range(7 * 24))
    )
    diagnostic = lire_diagnostic(
        territoire("69123"),
        conso_mwh=500,
        courbe=Courbe(pas_minutes=60, points=points),
        database=medallion_db,
    )
    a = diagnostic.acheminement
    assert a is not None
    assert a.profil == PROFIL_COURBE
    assert a.domaine_turpe == "bt_sup36"
    assert "votre courbe de charge" in formater_essentiel(diagnostic)
