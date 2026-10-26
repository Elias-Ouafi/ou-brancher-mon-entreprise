from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli
from ingestion.config import Settings
from ingestion.diagnostic import (
    Critere,
    DiagnosticIndisponibleError,
    PlanPrevention,
    code_commune_parent,
    code_departement,
    formater_diagnostic,
    formater_essentiel,
    lire_diagnostic,
    liste_plans,
    phrase_verdict,
    verdict,
)
from ingestion.geocode import Territoire
from ingestion.georisques import lire_rapport
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import ANNEE_REFERENCE, DBT_BIN

pytestmark = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


@pytest.fixture(scope="module")
def warehouse(medallion_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(str(medallion_db), read_only=True)
    yield connection
    connection.close()


def _rows(connection: duckdb.DuckDBPyConnection, query: str) -> list[tuple[Any, ...]]:
    return connection.execute(query).fetchall()


def territoire(code_commune: str, commune: str = "Commune", **overrides: Any) -> Territoire:
    valeurs: dict[str, Any] = {
        "adresse": f"1 rue de test, {commune}",
        "code_insee_commune": code_commune,
        "commune": commune,
        "code_postal": None,
        "code_departement": code_departement(code_commune),
        "departement": "",
        "code_insee_region": None,
        "region": "",
        "latitude": 0.0,
        "longitude": 0.0,
        "score": 0.9,
        "precision": "housenumber",
    }
    return Territoire(**(valeurs | overrides))


@pytest.mark.parametrize(
    ("code", "parent"),
    [("69383", "69123"), ("75101", "75056"), ("13216", "13055"), ("69266", "69266")],
)
def test_arrondissements_are_brought_back_to_their_commune(code: str, parent: str) -> None:
    assert code_commune_parent(code) == parent


@pytest.mark.parametrize(
    ("commune", "departement"), [("69123", "69"), ("2A004", "2A"), ("97411", "974")]
)
def test_department_is_derived_from_commune_code(commune: str, departement: str) -> None:
    assert code_departement(commune) == departement


def test_iris_codes_are_padded_and_empty_duplicates_dropped(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    rows = _rows(
        warehouse,
        "SELECT code_iris, consommation_electricite_mwh FROM silver.conso_industrielle_iris"
        " WHERE annee = 2022 ORDER BY code_iris",
    )
    # « 13760000 » vide doublonnait « 013760000 » : la ligne publiée sur 9 caractères reste
    assert rows == [("013760000", 7_000.0), ("692660000", 99_000.0), ("693830101", None)]


def test_missing_commune_is_rebuilt_from_iris_code(warehouse: duckdb.DuckDBPyConnection) -> None:
    rows = _rows(
        warehouse,
        "SELECT code_iris, code_insee_commune, code_departement, code_insee_region"
        " FROM silver.conso_industrielle_iris"
        " WHERE est_commune_reconstituee AND annee = 2021 ORDER BY code_iris",
    )
    assert rows == [
        ("020030000", "02003", "02", "32"),
        ("751010101", "75056", "75", "11"),
    ]


def test_corsica_resolves_its_hierarchy(warehouse: duckdb.DuckDBPyConnection) -> None:
    assert _rows(
        warehouse,
        "SELECT code_departement, code_insee_region FROM silver.conso_industrielle_iris"
        " WHERE code_iris = '2A0040000'",
    ) == [("2A", "94")]


def test_constraints_region_label_is_matched_to_its_code(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    assert _rows(
        warehouse,
        "SELECT code_insee_region, libelle_publie FROM silver.contraintes_reseau_region"
        " ORDER BY code_insee_region",
    ) == [("11", "ÎLE-DE-FRANCE"), ("84", "AUVERGNE-RHÔNE-ALPES")]


def test_reference_year_is_last_one_without_statistical_secrecy(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    assert _rows(
        warehouse, "SELECT DISTINCT annee FROM gold.fct_pression_industrielle_departement"
    ) == [(ANNEE_REFERENCE,)]
    assert _rows(
        warehouse, "SELECT DISTINCT annee FROM gold.fct_pression_industrielle_commune"
    ) == [(ANNEE_REFERENCE,)]


def test_department_pressure_aggregates_communes_and_ranks(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    rows = _rows(
        warehouse,
        "SELECT code_departement, consommation_electricite_mwh, nb_sites_electricite,"
        " nb_communes_avec_site, rang_national, nb_departements_classes"
        " FROM gold.fct_pression_industrielle_departement"
        " WHERE rang_national IS NOT NULL ORDER BY rang_national",
    )
    assert rows == [
        ("69", 100_000.0, 3, 2, 1, 4),
        ("02", 10_000.0, 1, 1, 2, 4),
        ("75", 5_000.0, 1, 1, 3, 4),
        ("2A", 3_000.0, 1, 1, 4, 4),
    ]


def test_every_department_has_a_row_even_without_site(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    assert _rows(
        warehouse,
        "SELECT count(*), count(*) FILTER (WHERE conso_industrielle_departement_gwh = 0)"
        " FROM gold.fct_diagnostic_territoire",
    ) == [(101, 97)]


def test_regional_balance_uses_last_twelve_months(warehouse: duckdb.DuckDBPyConnection) -> None:
    rows = _rows(
        warehouse,
        "SELECT code_insee_region, mois_debut, mois_fin, nb_mois, production_gwh,"
        " taux_couverture, statut_equilibre, rang_dependance"
        " FROM gold.fct_tension_reseau_region WHERE nb_mois > 0 ORDER BY rang_dependance",
    )
    debut, fin = date(2025, 1, 1), date(2025, 12, 1)
    assert rows == [
        ("11", debut, fin, 12, pytest.approx(0.12), pytest.approx(0.1), "deficitaire", 1),
        ("94", debut, fin, 12, pytest.approx(0.84), pytest.approx(0.7), "deficitaire", 2),
        ("84", debut, fin, 12, pytest.approx(2.4), pytest.approx(2.0), "excedentaire", 3),
    ]


def test_regions_without_data_are_kept_with_nulls(warehouse: duckdb.DuckDBPyConnection) -> None:
    assert _rows(
        warehouse,
        "SELECT nb_mois, taux_couverture, rang_dependance, nb_regions_classees"
        " FROM gold.fct_tension_reseau_region WHERE code_insee_region = '04'",
    ) == [(0, None, None, 3)]


def test_curtailed_energy_sums_the_four_seasons(warehouse: duckdb.DuckDBPyConnection) -> None:
    assert _rows(
        warehouse,
        "SELECT puissance_a_compenser_mw, energie_non_evacuee_mwh"
        " FROM gold.fct_tension_reseau_region WHERE code_insee_region = '84'",
    ) == [(187.0, 2_777.0)]


def test_diagnostic_for_a_lyon_arrondissement(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69383", "Lyon"), puissance_mw=5, database=medallion_db)
    assert diagnostic.code_insee_commune == "69123"
    assert (diagnostic.code_departement, diagnostic.code_insee_region) == ("69", "84")
    assert diagnostic.commune_a_des_sites
    assert diagnostic.conso_industrielle_commune_gwh == pytest.approx(40.0)
    assert diagnostic.conso_industrielle_departement_gwh == pytest.approx(100.0)
    assert diagnostic.rang_pression_departement == 1
    assert diagnostic.statut_equilibre == "excedentaire"
    assert diagnostic.seuil is not None
    assert diagnostic.seuil.domaine_tension == "HTA"


def test_commune_without_site_falls_back_to_department(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69029", "Bron"), database=medallion_db)
    assert not diagnostic.commune_a_des_sites
    assert diagnostic.nb_sites_departement == 3
    assert diagnostic.seuil is None
    assert "repli sur le département" in formater_diagnostic(diagnostic)


@pytest.mark.parametrize(
    ("puissance", "domaine"),
    [
        (0.009, "BT ≤ 36 kVA"),
        (0.036, "BT > 36 kVA"),
        (0.25, "HTA"),
        (19.9, "HTA"),
        (20, "HTA – zone grise"),
        (39.9, "HTA – zone grise"),
        (40, "HTB"),
        (1_000, "HTB"),
    ],
)
def test_connection_threshold_follows_project_power(
    medallion_db: Path, puissance: float, domaine: str
) -> None:
    seuil = lire_diagnostic(
        territoire("69123"), puissance_mw=puissance, database=medallion_db
    ).seuil
    assert seuil is not None
    assert seuil.domaine_tension == domaine


# Sans puissance, on ne sait pas qui raccorde : le verdict le dit au lieu de deviner
def test_verdict_without_power_leaves_connection_unknown(medallion_db: Path) -> None:
    criteres = verdict(lire_diagnostic(territoire("69123"), database=medallion_db))
    assert [c.nom for c in criteres] == ["Raccordement", "Risques", "Carbone", "Facture", "Foncier"]
    assert criteres[0].etat == "inconnu"
    assert "non évalué : raccordement" in phrase_verdict(criteres)


# Petit projet chez Enedis : rien à signaler ; gros projet chez RTE : étude indispensable
@pytest.mark.parametrize(("puissance", "etat"), [(0.25, "ok"), (100, "attention")])
def test_verdict_connection_follows_who_connects(
    medallion_db: Path, puissance: float, etat: str
) -> None:
    raccordement = verdict(
        lire_diagnostic(territoire("69123"), puissance_mw=puissance, database=medallion_db)
    )[0]
    assert raccordement.etat == etat
    assert ("RTE" in raccordement.detail) is (etat == "attention")


# Réseau insulaire au-dessus du seuil européen : le carbone passe à vérifier
def test_verdict_flags_carbon_heavy_grid(medallion_db: Path) -> None:
    def carbone(commune: str) -> str:
        criteres = verdict(lire_diagnostic(territoire(commune), database=medallion_db))
        return next(c.etat for c in criteres if c.nom == "Carbone")

    assert carbone("2A004") == "attention"
    assert carbone("69123") == "ok"


# Le profil horaire redonne le facteur annuel en moyenne et place la pointe du soir
def test_hourly_carbon_profile_for_islands(medallion_db: Path, warehouse: Any) -> None:
    corse = lire_diagnostic(territoire("2A004"), database=medallion_db).carbone
    assert corse is not None and corse.annee_profil == 2025
    assert [h for h, _ in corse.profil_horaire] == list(range(24))
    moyenne = warehouse.execute(
        "select sum(intensite_estimee_g_co2e_kwh * production_mwh) / sum(production_mwh)"
        " from gold.fct_carbone_horaire_zni"
    ).fetchone()[0]
    assert moyenne == pytest.approx(corse.intensite_acv_g_co2e_kwh)
    pointe = max(corse.profil_horaire, key=lambda heure: heure[1])[0]
    assert 18 <= pointe <= 22
    texte = formater_essentiel(lire_diagnostic(territoire("2A004"), database=medallion_db))
    assert "Heure par heure : le moins carboné vers" in texte

    lyon = lire_diagnostic(territoire("69123"), database=medallion_db).carbone
    assert lyon is not None and lyon.profil_horaire == ()


def _risques(diagnostic: Any) -> Critere:
    return next(c for c in verdict(diagnostic) if c.nom == "Risques")


# Un plan de prévention en vigueur (approuvé ou prescrit) rend les risques à vérifier ; un plan
# caduc ou d'un export dépassé ne compte pas, et un arrondissement hérite des plans de la commune
def test_risks_need_checking_only_under_a_prevention_plan(medallion_db: Path) -> None:
    lyon = lire_diagnostic(territoire("69382", "Lyon"), database=medallion_db)
    assert [p.code_procedure for p in lyon.plans_prevention or []] == ["PPRT1", "PPRI1"]
    critere = _risques(lyon)
    assert (critere.etat, critere.valeur) == ("attention", "2 plans de prévention dans la commune")
    assert critere.detail.startswith(
        "Vallée de la chimie (risque industriel, approuvé en 2016),"
        " PPRI du Grand Lyon (inondation, approuvé en 2009). Le règlement du plan"
    )

    ajaccio = _risques(lire_diagnostic(territoire("2A004", "Ajaccio"), database=medallion_db))
    assert ajaccio.etat == "attention"
    assert ajaccio.detail.startswith("PPRIF d'Ajaccio (feu de forêt, prescrit en 2007).")

    paris = lire_diagnostic(territoire("75056", "Paris"), database=medallion_db)
    assert paris.plans_prevention == []
    assert (_risques(paris).etat, _risques(paris).valeur) == (
        "info",
        "aucun plan de prévention dans la commune",
    )
    assert "plans de prévention" not in phrase_verdict(verdict(paris))

    non_charges = _risques(replace(paris, plans_prevention=None))
    assert (non_charges.etat, non_charges.valeur) == ("inconnu", "plans de prévention non chargés")


def test_full_diagnosis_lists_every_plan_with_its_limit(medallion_db: Path) -> None:
    lyon = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)
    texte = formater_diagnostic(lyon)
    assert "Plans de prévention en vigueur dans la commune : Vallée de la chimie" in texte
    assert lyon.limites["plans_prevention"] in texte
    paris = formater_diagnostic(lire_diagnostic(territoire("75056"), database=medallion_db))
    assert "Plans de prévention en vigueur dans la commune : aucun" in paris


def test_plan_list_is_cut_after_the_first_ones() -> None:
    plans = [
        PlanPrevention(f"P{i}", "PPRN", f"Plan {i}", None, "approuvé", None, None) for i in range(5)
    ]
    assert (
        liste_plans(plans, 3)
        == "Plan 0 (approuvé), Plan 1 (approuvé), Plan 2 (approuvé) et 2 autres"
    )
    assert liste_plans(plans[:4], 3).endswith("et 1 autre")


def test_verdict_sentence_lists_what_to_check() -> None:
    criteres = [
        Critere("Raccordement", "attention", "HTB, RTE", ""),
        Critere("Risques", "inconnu", "non consultés", ""),
        Critere("Carbone", "ok", "40 gCO₂e/kWh", ""),
    ]
    assert phrase_verdict(criteres) == (
        "À vérifier avant de s'engager : raccordement (non évalué : risques)."
    )
    assert phrase_verdict(criteres[2:]) == "Rien de bloquant dans les données publiques."


def test_every_figure_comes_with_its_limit(medallion_db: Path, warehouse: Any) -> None:
    rapport = json.loads(
        (FIXTURES_DIR / "georisques_rapport_lyon.json").read_text(encoding="utf-8")
    )
    diagnostic = replace(
        lire_diagnostic(
            territoire("69123"),
            puissance_mw=50,
            conso_mwh=350,
            ca_eur=5_000_000,
            va_eur=1_000_000,
            database=medallion_db,
        ),
        risques=lire_rapport(rapport),
    )
    texte = formater_diagnostic(diagnostic)
    limites = [
        limite for (limite,) in _rows(warehouse, "SELECT limite FROM gold.limites_methodologiques")
    ]
    assert len(limites) == 19
    for limite in limites:
        assert limite in texte


def test_non_interconnected_zones_are_flagged(medallion_db: Path) -> None:
    corse = formater_diagnostic(lire_diagnostic(territoire("2A004"), database=medallion_db))
    assert "Zone non interconnectée" in corse
    reunion = formater_diagnostic(lire_diagnostic(territoire("97411"), database=medallion_db))
    assert "Aucun équilibre régional publié par RTE" in reunion
    assert "zone non interconnectée" in reunion


def test_missing_warehouse_is_reported(tmp_path: Path) -> None:
    with pytest.raises(DiagnosticIndisponibleError, match="introuvable"):
        lire_diagnostic(territoire("69123"), database=tmp_path / "absent.duckdb")


def test_first_launch_tells_which_command_loads_the_data(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "geocode", lambda adresse, **_: territoire("69123", "Lyon"))

    for commande in ("essentiel", "diagnose"):
        assert cli.main([commande, "--adresse", "1 rue X 69001 Lyon", "--sans-carte"]) == 1
    assert caplog.text.count("python -m ingestion.cli refresh --months 180") == 2
    assert capsys.readouterr().out == ""


def test_verbose_is_accepted_before_or_after_the_command() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["essentiel", "--adresse", "x"]).verbose is False
    assert parser.parse_args(["essentiel", "--adresse", "x", "--verbose"]).verbose is True
    assert parser.parse_args(["--verbose", "essentiel", "--adresse", "x"]).verbose is True


def test_warehouse_without_gold_layer_is_reported(tmp_path: Path) -> None:
    database = tmp_path / "vide.duckdb"
    duckdb.connect(str(database)).close()
    with pytest.raises(DiagnosticIndisponibleError, match="gold incomplete : Catalog Error"):
        lire_diagnostic(territoire("69123"), database=database)


def test_diagnose_command_end_to_end(
    httpx_mock: HTTPXMock,
    settings: Settings,
    medallion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [4.85, 45.75]},
                    "properties": {
                        "label": "10 Rue Garibaldi 69003 Lyon",
                        "score": 0.95,
                        "type": "housenumber",
                        "citycode": "69383",
                        "city": "Lyon",
                        "postcode": "69003",
                        "context": "69, Rhône, Auvergne-Rhône-Alpes",
                    },
                }
            ],
        }
    )
    reglages = settings.model_copy(
        update={"ban_base_url": "https://ban.test/geocodage", "duckdb_path": medallion_db}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    code = cli.main(
        [
            "diagnose",
            "--adresse",
            "10 rue Garibaldi, Lyon",
            "--puissance",
            "0,5",
            "--sans-risques",
            "--sans-carte",
        ]
    )

    sortie = capsys.readouterr().out
    assert code == 0
    assert "Lyon (69123) · Rhône (69) · Auvergne-Rhône-Alpes (84)" in sortie
    assert "rang 1 sur 4 départements" in sortie
    assert "Puissance 0,5 MW → HTA" in sortie


def test_diagnose_rejects_negative_power() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["diagnose", "--adresse", "x", "--puissance", "-1"])


def test_essentiel_command_answers_price_and_carbon(
    httpx_mock: HTTPXMock,
    settings: Settings,
    medallion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [8.74, 41.92]},
                    "properties": {
                        "label": "1 Cours Napoléon 20000 Ajaccio",
                        "score": 0.95,
                        "type": "housenumber",
                        "citycode": "2A004",
                        "city": "Ajaccio",
                        "postcode": "20000",
                        "context": "2A, Corse-du-Sud, Corse",
                    },
                }
            ],
        }
    )
    reglages = settings.model_copy(
        update={"ban_base_url": "https://ban.test/geocodage", "duckdb_path": medallion_db}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    code = cli.main(
        ["essentiel", "--adresse", "1 cours Napoléon, Ajaccio", "--format", "json", "--sans-carte"]
    )
    assert code == 0
    reponse = json.loads(capsys.readouterr().out)
    assert reponse["prix"]["code_tranche"] == "IA"
    assert reponse["prix"]["est_tranche_par_defaut"] is True
    assert reponse["prix"]["facture_htva_eur_an"] is None
    assert reponse["carbone"]["est_decarbone"] is False
    assert reponse["carbone"]["systeme_electrique"] == "Corse"
    assert set(reponse["limites"]) == {"prix_entreprise", "carbone_territoire"}
    assert "carbone" in reponse["verdict"]["phrase"]
    assert len(reponse["verdict"]["criteres"]) == 5


def test_essentiel_text_fits_in_a_few_lines(medallion_db: Path) -> None:
    from ingestion.diagnostic import formater_essentiel

    texte = formater_essentiel(
        # Paris : aucun plan de prévention dans la base synthétique, rien de bloquant
        lire_diagnostic(territoire("75056", "Paris"), conso_mwh=350, database=medallion_db)
    )
    assert len(texte.splitlines()) <= 8
    assert texte.splitlines()[-1].startswith("Brancher ici ? Rien de bloquant")
    assert "Prix moyen : 0,1925 €/kWh HTVA, soit environ 67 400 € par an pour 350 MWh" in texte
    assert "Électricité décarbonée : oui (France continentale), 51,9 gCO₂e/kWh" in texte


def test_essentiel_puts_the_bill_against_turnover(medallion_db: Path) -> None:
    from ingestion.diagnostic import essentiel_en_dict, formater_essentiel

    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon"), conso_mwh=350, ca_eur=5_000_000, database=medallion_db
    )
    part = diagnostic.part_chiffre_affaires
    assert part is not None
    assert part.part == pytest.approx(350_000 * 0.1925 / 5_000_000, rel=1e-3)
    texte = formater_essentiel(diagnostic)
    assert "Part du chiffre d'affaires : 1,3 % (67 400 € sur 5 000 000 €)" in texte
    assert "scénarios" not in texte
    reponse = essentiel_en_dict(diagnostic)
    assert reponse["part_chiffre_affaires"]["part"] == part.part
    assert "part_chiffre_affaires" in reponse["limites"]


# 350 MWh à 26,58 € = 9 303 € d'accise : 0,93 % d'une VA de 1 M€ (premier palier), 0,47 % de
# 2 M€ (sous le seuil), 7 % de 133 000 € (dernier palier)
@pytest.mark.parametrize(
    ("va", "palier"), [(1_000_000, 0.005), (2_000_000, None), (133_000, 0.0675)]
)
def test_value_added_share_places_the_fiscal_threshold(
    medallion_db: Path, va: float, palier: float | None
) -> None:
    from ingestion.diagnostic import essentiel_en_dict, formater_essentiel

    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon"), conso_mwh=350, va_eur=va, database=medallion_db
    )
    part = diagnostic.part_valeur_ajoutee
    assert part is not None
    assert part.electro_intensite == pytest.approx(350 * 26.58 / va)
    assert part.palier == palier
    texte = formater_essentiel(diagnostic)
    assert "Part de la valeur ajoutée : facture" in texte
    assert ("taux réduit d'accise possible" in texte) is (palier is not None)
    reponse = essentiel_en_dict(diagnostic)
    assert reponse["part_valeur_ajoutee"]["palier"] == palier
    assert "part_valeur_ajoutee" in reponse["limites"]


def test_excise_rate_comes_from_the_dated_seed(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon"), conso_mwh=350, va_eur=1_000_000, database=medallion_db
    )
    assert diagnostic.accise is not None
    assert diagnostic.accise.tarif_eur_mwh == 26.58
    assert diagnostic.accise.date_debut == date(2026, 2, 1)
    sans_va = lire_diagnostic(territoire("69123", "Lyon"), conso_mwh=350, database=medallion_db)
    assert sans_va.accise is None
    assert sans_va.part_valeur_ajoutee is None


# Indexation chaque 1er février : un barème du 1er février 2026 vaut jusqu'au 31 janvier 2027
@pytest.mark.parametrize(
    ("debut", "aujourd_hui", "a_reverifier"),
    [
        (date(2026, 2, 1), date(2026, 10, 25), False),
        (date(2026, 2, 1), date(2027, 1, 31), False),
        (date(2026, 2, 1), date(2027, 2, 1), True),
        (date(2025, 8, 1), date(2026, 1, 31), False),
        (date(2025, 8, 1), date(2026, 2, 1), True),
        (date(2026, 1, 1), date(2026, 2, 1), True),
    ],
)
def test_excise_rate_is_flagged_after_each_february_indexation(
    debut: date, aujourd_hui: date, a_reverifier: bool
) -> None:
    from ingestion.diagnostic import Accise

    assert Accise(26.58, debut).a_reverifier(aujourd_hui) is a_reverifier


def test_turnover_share_follows_the_three_year_scenarios(medallion_db: Path) -> None:
    from ingestion.diagnostic import formater_essentiel

    diagnostic = lire_diagnostic(
        territoire("69123"), conso_mwh=350, ca_eur=5_000_000, database=medallion_db
    )
    assert diagnostic.prix is not None
    diagnostic = replace(
        diagnostic,
        prix=replace(
            diagnostic.prix,
            prix_3_ans_bas_eur_kwh=0.10,
            prix_3_ans_central_eur_kwh=0.15,
            prix_3_ans_haut_eur_kwh=0.30,
        ),
    )
    part = diagnostic.part_chiffre_affaires
    assert part is not None
    assert part.part_bas == pytest.approx(0.007)
    assert part.part_haut == pytest.approx(0.021)
    assert "de 0,70 % à 2,1 % avec les scénarios à 3 ans" in formater_essentiel(diagnostic)


def test_turnover_is_ignored_without_consumption(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69123"), ca_eur=5_000_000, database=medallion_db)
    assert diagnostic.part_chiffre_affaires is None
    assert "Part du chiffre d'affaires" not in formater_diagnostic(diagnostic)


def test_bill_above_turnover_hints_at_a_unit_error(medallion_db: Path) -> None:
    from ingestion.diagnostic import formater_essentiel

    texte = formater_essentiel(
        lire_diagnostic(territoire("69123"), conso_mwh=350, ca_eur=5_000, database=medallion_db)
    )
    assert "Facture supérieure au chiffre d'affaires" in texte


def test_turnover_requires_consumption() -> None:
    with pytest.raises(SystemExit):
        cli.main(["essentiel", "--adresse", "x", "--ca", "5000000"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["diagnose", "--adresse", "x", "--ca", "-1"])


def test_essentiel_explains_an_unknown_address(
    httpx_mock: HTTPXMock,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(json={"type": "FeatureCollection", "features": []})
    reglages = settings.model_copy(update={"ban_base_url": "https://ban.test/geocodage"})
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    assert cli.main(["essentiel", "--adresse", "nulle part"]) == 1
    assert "adresse introuvable" in caplog.text


def test_data_dates_come_from_the_last_load_of_each_source(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69123", "Lyon"), database=medallion_db)

    assert set(diagnostic.chargements) >= {"eurostat_nrg_pc_205", "gaspar_risques"}
    assert set(diagnostic.chargements.values()) == {date(2026, 7, 1)}
    assert "Données chargées le 01/07/2026" in formater_diagnostic(diagnostic)


@pytest.mark.parametrize(
    ("chargements", "attendu"),
    [
        ({}, "Date des données inconnue"),
        ({"eurostat_nrg_pc_205": date(2026, 10, 23)}, "Données chargées le 23/10/2026."),
        (
            {"eurostat_nrg_pc_205": date(2026, 10, 23), "gaspar_risques": date(2026, 10, 24)},
            "Données chargées du 23/10/2026 au 24/10/2026.",
        ),
        (
            {"eurostat_nrg_pc_205": date(2026, 9, 1), "gaspar_risques": date(2026, 10, 24)},
            "Données chargées le 24/10/2026, sauf prix Eurostat le 01/09/2026 : plus de 45 jours,"
            " relancer refresh.",
        ),
    ],
)
def test_data_date_line_flags_late_sources(chargements: dict, attendu: str) -> None:
    from ingestion.diagnostic import ligne_chargements

    assert ligne_chargements(chargements, aujourd_hui=date(2026, 10, 24)).startswith(attendu)


def test_essentiel_lists_the_candidates_of_an_ambiguous_address(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from ingestion.geocode import AdresseAmbigueError

    def ambigue(adresse: str, **_: object) -> None:
        raise AdresseAmbigueError(adresse, ["3 A. 33000 Bordeaux", "3 A. 02100 Saint-Quentin"])

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "geocode", ambigue)

    assert cli.main(["essentiel", "--adresse", "3 allée des peupliers"]) == 1
    assert "adresse ambigue" in caplog.text
    assert "33000 Bordeaux ; 3 A. 02100 Saint-Quentin" in caplog.text
    assert "Preciser le code postal ou la commune" in caplog.text


def test_enedis_addresses_are_grouped_on_the_latest_year(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    assert _rows(
        warehouse,
        "select annee, adresse_normalisee, nb_sites, consommation_mwh, grand_secteur, code_naf2,"
        " nb_lignes from gold.fct_conso_entreprises_adresse where code_insee_commune = '69123'"
        " order by adresse_normalisee",
    ) == [
        (2025, "13bruejouffroydabbans", 1, 20.0, "TERTIAIRE", "62", 1),
        (2025, "9rueampere", 3, 350.0, "INDUSTRIE", "24", 2),
    ]


def test_enedis_communes_rank_their_industry_within_the_department(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    assert _rows(
        warehouse,
        "select code_insee_commune, nb_sites, consommation_mwh, consommation_industrie_mwh,"
        " code_naf2_principal, rang_industrie_departement, nb_communes_departement"
        " from gold.fct_conso_entreprises_commune order by code_insee_commune",
    ) == [("69123", 5, 375.0, 300.0, "24", 2, 2), ("69266", 4, 1000.0, 1000.0, "20", 1, 2)]


@pytest.mark.parametrize("voie", ["9 Rue Ampère", "9 rue Ampere"])
def test_address_already_connected_is_found_in_enedis_data(medallion_db: Path, voie: str) -> None:
    from ingestion.diagnostic import essentiel_en_dict, formater_diagnostic, formater_essentiel

    diagnostic = lire_diagnostic(territoire("69383", "Lyon", voie=voie), database=medallion_db)

    deja = diagnostic.entreprises_adresse
    assert deja is not None
    assert (deja.annee, deja.nb_sites, deja.consommation_mwh) == (2025, 3, 350.0)
    assert deja.libelle_naf2 == "Métallurgie"
    assert (
        "Déjà raccordé ici : 3 site(s) d'entreprise consommant 350,0 MWh en 2025"
        " (industrie, Métallurgie ; Enedis)"
    ) in formater_essentiel(diagnostic)
    complet = formater_diagnostic(diagnostic)
    assert "Entreprises raccordées à Enedis : 5 site(s)" in complet
    assert "rang 2 sur 2 communes du département" in complet
    reponse = essentiel_en_dict(diagnostic)
    assert reponse["entreprises_adresse"]["nb_sites"] == 3
    assert "entreprises_enedis" in reponse["limites"]


# Une adresse à la rue seulement, ou absente des données Enedis, ne prouve rien
@pytest.mark.parametrize(
    ("voie", "precision"), [("9 Rue Ampère", "street"), ("11 Rue Ampère", "housenumber")]
)
def test_unmatched_address_shows_only_the_commune(
    medallion_db: Path, voie: str, precision: str
) -> None:
    from ingestion.diagnostic import essentiel_en_dict, formater_essentiel

    diagnostic = lire_diagnostic(
        territoire("69123", "Lyon", voie=voie, precision=precision), database=medallion_db
    )

    assert diagnostic.entreprises_adresse is None
    assert diagnostic.entreprises_commune is not None
    assert "Déjà raccordé" not in formater_essentiel(diagnostic)
    assert "entreprises_enedis" not in essentiel_en_dict(diagnostic)["limites"]
