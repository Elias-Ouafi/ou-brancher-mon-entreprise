"""Prix de l'électricité des entreprises : client Eurostat, couches dbt et bloc du diagnostic.

Les prix synthétiques sont décrits dans `medallion.py` : la tranche IB monte de
0,005 €/kWh par semestre à partir de 0,10 (2016-S1 à 2025-S2), la tranche IG n'a
que deux ans d'historique. Aucun appel réseau réel (Eurostat est simulé).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli
from ingestion.config import Settings
from ingestion.diagnostic import formater_diagnostic, lire_diagnostic
from ingestion.extract_eurostat import (
    FISCALITES,
    TRANCHES,
    EurostatClient,
    EurostatResponseError,
    aplatir_json_stat,
)
from ingestion.pipeline import run_prix_entreprises_ingestion
from ingestion.tests.medallion import (
    DBT_BIN,
    PRIX_IB_DEPART,
    PRIX_IB_PAS,
    PRIX_ID,
    TAXES_IB,
    semestres,
)
from ingestion.tests.test_diagnostic import territoire
from reporting.implantation import generer_rapport_implantation

EUROSTAT_URL = "https://eurostat.test/api"

#: Prix HTVA synthétiques de la tranche IB, du plus ancien au plus récent.
PRIX_IB = [PRIX_IB_DEPART + PRIX_IB_PAS * i for i in range(20)]


def cube(valeurs: dict[str, float], statuts: dict[str, str] | None = None) -> dict[str, Any]:
    """Cube JSON-stat minimal : 2 tranches, 2 fiscalités, 2 semestres (8 cellules)."""
    dimension = {
        "freq": {"category": {"index": {"S": 0}, "label": {"S": "Semestriel"}}},
        "nrg_cons": {
            "category": {
                "index": {"MWH20-499": 0, "MWH_LT20": 1},
                "label": {"MWH20-499": "tranche IB", "MWH_LT20": "tranche IA"},
            }
        },
        "tax": {"category": {"index": {"X_TAX": 0, "X_VAT": 1}, "label": {}}},
        "time": {"category": {"index": {"2025-S2": 0, "2026-S1": 1}, "label": {}}},
    }
    return {
        "id": ["freq", "nrg_cons", "tax", "time"],
        "size": [1, 2, 2, 2],
        "dimension": dimension,
        "value": valeurs,
        "status": statuts or {},
    }


# --- Client Eurostat --------------------------------------------------------------


def test_json_stat_cube_is_flattened_by_position() -> None:
    # Position = ((tranche * 2) + fiscalite) * 2 + semestre ; la cellule 6 n'est pas publiée.
    records = aplatir_json_stat(
        cube({"0": 0.18, "1": 0.17, "3": 0.20, "7": 0.25}, statuts={"7": "p"})
    )
    assert records == [
        {"semestre": "2025-S2", "tranche": "MWH20-499", "fiscalite": "X_TAX",
         "libelle_tranche": "tranche IB", "statut": None, "prix_eur_kwh": 0.18},
        {"semestre": "2026-S1", "tranche": "MWH20-499", "fiscalite": "X_TAX",
         "libelle_tranche": "tranche IB", "statut": None, "prix_eur_kwh": 0.17},
        {"semestre": "2026-S1", "tranche": "MWH20-499", "fiscalite": "X_VAT",
         "libelle_tranche": "tranche IB", "statut": None, "prix_eur_kwh": 0.20},
        {"semestre": "2026-S1", "tranche": "MWH_LT20", "fiscalite": "X_VAT",
         "libelle_tranche": "tranche IA", "statut": "p", "prix_eur_kwh": 0.25},
    ]  # fmt: skip


@pytest.mark.parametrize(
    "payload",
    [
        {"erreur": "inattendue"},
        {**cube({}), "id": ["freq", "nrg_cons", "time"], "size": [1, 2, 2]},
        {**cube({}), "size": [1, 2, 2, 3]},
    ],
)
def test_malformed_json_stat_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(EurostatResponseError):
        aplatir_json_stat(payload)


def test_client_asks_every_band_and_tax_level_in_one_call(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    httpx_mock.add_response(json=cube({"0": 0.18}))
    reglages = settings.model_copy(update={"eurostat_base_url": EUROSTAT_URL})

    with EurostatClient(reglages) as client:
        records = client.fetch_prix_entreprises()
        assert client.calls == 1

    assert len(records) == 1
    requete = httpx_mock.get_requests()[0]
    assert requete.url.path == "/api/statistics/1.0/data/nrg_pc_205"
    assert requete.url.params.get_list("nrg_cons") == list(TRANCHES)
    assert requete.url.params.get_list("tax") == list(FISCALITES)
    assert requete.url.params["geo"] == "FR"


def test_ingestion_merges_into_bronze_idempotently(
    httpx_mock: HTTPXMock, settings: Settings, tmp_path: Path
) -> None:
    reglages = settings.model_copy(
        update={"eurostat_base_url": EUROSTAT_URL, "bronze_dir": tmp_path / "bronze"}
    )
    httpx_mock.add_response(json=cube({"0": 0.18, "3": 0.20}), is_reusable=True)

    outcome, premier = run_prix_entreprises_ingestion(settings=reglages)
    _, second = run_prix_entreprises_ingestion(settings=reglages, run_id="rejeu")

    assert (outcome.rows, outcome.api_calls) == (2, 1)
    assert premier.as_dict()["rows_inserted"] == 2
    assert second.as_dict()["rows_inserted"] == 0
    with duckdb.connect(str(reglages.duckdb_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM bronze.eurostat_nrg_pc_205").fetchone() == (
            2,
        )


# --- Couches dbt ----------------------------------------------------------------------

dbt_requis = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def quantile_cont(valeurs: list[float], q: float) -> float:
    """Interpolation linéaire entre rangs, comme `quantile_cont` de DuckDB."""
    tries = sorted(valeurs)
    position = q * (len(tries) - 1)
    bas = int(position)
    haut = min(bas + 1, len(tries) - 1)
    return tries[bas] + (tries[haut] - tries[bas]) * (position - bas)


def _une_ligne(medallion_db: Path, sql: str) -> tuple[Any, ...]:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        ligne = connection.execute(sql).fetchone()
    assert ligne is not None
    return ligne


@dbt_requis
def test_silver_pairs_tax_levels_and_numbers_semesters(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        lignes = connection.execute(
            "SELECT semestre, debut_semestre, rang_semestre, taxes_non_recuperables_eur_kwh"
            " FROM silver.prix_entreprises WHERE tranche = 'MWH20-499' ORDER BY rang_semestre"
        ).fetchall()
    assert len(lignes) == 20
    rangs = [rang for _, _, rang, _ in lignes]
    assert rangs == list(range(rangs[0], rangs[0] + 20))  # aucun trou
    assert (lignes[1][0], str(lignes[1][1])) == ("2016-S2", "2016-07-01")
    assert all(taxes == pytest.approx(TAXES_IB) for *_, taxes in lignes)


@dbt_requis
def test_tranche_summary_reads_last_twelve_months_and_history(medallion_db: Path) -> None:
    ligne = _une_ligne(
        medallion_db,
        "SELECT code_tranche, dernier_semestre, prix_reference_eur_kwh, taxes_reference_eur_kwh,"
        " prix_moyen_5_ans_eur_kwh, prix_min_eur_kwh, semestre_min, prix_max_eur_kwh,"
        " semestre_max FROM gold.fct_prix_entreprise_tranche WHERE tranche = 'MWH20-499'",
    )
    code, dernier, reference, taxes, cinq_ans, minimum, sem_min, maximum, sem_max = ligne
    assert (code, dernier, sem_min, sem_max) == ("IB", "2025-S2", "2016-S1", "2025-S2")
    assert reference == pytest.approx((PRIX_IB[18] + PRIX_IB[19]) / 2)
    assert taxes == pytest.approx(TAXES_IB)
    assert cinq_ans == pytest.approx(sum(PRIX_IB[10:]) / 10)
    assert (minimum, maximum) == (pytest.approx(PRIX_IB[0]), pytest.approx(PRIX_IB[19]))


@dbt_requis
def test_scenarios_apply_past_variations_to_reference_price(medallion_db: Path) -> None:
    reference = (PRIX_IB[18] + PRIX_IB[19]) / 2
    attendus = {}
    for horizon in (1, 2, 3):
        decalage = 2 * horizon
        variations = [PRIX_IB[i + decalage] / PRIX_IB[i] for i in range(20 - decalage)]
        attendus[horizon] = (
            len(variations),
            *(reference * quantile_cont(variations, q) for q in (0.1, 0.5, 0.9)),
        )

    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        lignes = connection.execute(
            "SELECT horizon_annees, nb_variations_observees, prix_bas_eur_kwh,"
            " prix_central_eur_kwh, prix_haut_eur_kwh FROM gold.fct_prix_entreprise_scenarios"
            " WHERE tranche = 'MWH20-499' ORDER BY horizon_annees"
        ).fetchall()
        trois_ans = connection.execute(
            "SELECT prix_3_ans_bas_eur_kwh, prix_3_ans_central_eur_kwh, prix_3_ans_haut_eur_kwh"
            " FROM gold.fct_prix_entreprise_tranche WHERE tranche = 'MWH20-499'"
        ).fetchone()

    for horizon, nb, bas, centre, haut in lignes:
        assert (nb, bas, centre, haut) == pytest.approx(attendus[horizon])
    assert trois_ans == pytest.approx(
        tuple(sum(attendus[h][k] for h in (1, 2, 3)) / 3 for k in (1, 2, 3))
    )


@dbt_requis
def test_short_history_gives_no_scenario(medallion_db: Path) -> None:
    ligne = _une_ligne(
        medallion_db,
        "SELECT prix_reference_eur_kwh, prix_3_ans_central_eur_kwh, nb_variations_observees"
        " FROM gold.fct_prix_entreprise_tranche WHERE tranche = 'MWH_GE150000'",
    )
    assert ligne == (pytest.approx(0.07), None, None)


# --- Contrôle de la méthode sur le passé ----------------------------------------------

SEMESTRES_ID = semestres(2011, 2025)


def calibration_attendue(prix: list[float], minimum: int = 10) -> dict[str, Any]:
    """Recalcul indépendant du backtest dbt, sur une série semestrielle sans trou."""
    n = len(prix)
    periodes = []
    for t in range(n):
        bornes = []
        for h in (1, 2, 3):
            pas = 2 * h
            variations = [prix[s + pas] / prix[s] for s in range(n) if s + pas <= t]
            if len(variations) < minimum or t + pas >= n:
                break
            reference = sum(prix[max(0, t - 1) : t + 1]) / len(prix[max(0, t - 1) : t + 1])
            realise = (prix[t + pas - 1] + prix[t + pas]) / 2
            bas, haut = (reference * quantile_cont(variations, q) for q in (0.1, 0.9))
            bornes.append((bas, haut, realise))
        if len(bornes) == 3:
            bas, haut, realise = (sum(b[k] for b in bornes) / 3 for k in range(3))
            periodes.append((t, bas, haut, realise))
    return {
        "nb_periodes_controlees": len(periodes),
        "nb_dans_eventail": sum(bas <= r <= haut for _, bas, haut, r in periodes),
        "nb_au_dessus": sum(r > haut for _, _, haut, r in periodes),
        "nb_en_dessous": sum(r < bas for _, bas, _, r in periodes),
        "premiere_origine_controlee": SEMESTRES_ID[periodes[0][0]],
        "derniere_origine_controlee": SEMESTRES_ID[periodes[-1][0]],
    }


@dbt_requis
def test_past_fans_only_use_variations_known_at_their_origin(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        lignes = connection.execute(
            "SELECT semestre_origine, horizon_annees, nb_variations_observees"
            " FROM gold.fct_prix_entreprise_eventails WHERE tranche = 'MWH2000-19999'"
        ).fetchall()
    for semestre, horizon, nb in lignes:
        t = SEMESTRES_ID.index(semestre)
        # Départs s = 0 … t - 2h : l'arrivée s + 2h ne dépasse jamais l'origine.
        assert nb == t - 2 * horizon + 1


@dbt_requis
def test_calibration_matches_an_independent_backtest(medallion_db: Path) -> None:
    attendu = calibration_attendue(PRIX_ID)
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        resultat = connection.execute(
            "SELECT nb_periodes_controlees, nb_dans_eventail, nb_au_dessus, nb_en_dessous,"
            " premiere_origine_controlee, derniere_origine_controlee"
            " FROM gold.fct_prix_entreprise_calibration WHERE tranche = 'MWH2000-19999'"
        ).fetchone()
    assert resultat == tuple(attendu.values())
    # Les trois positions sont représentées : aucune branche du contrôle n'est muette.
    assert min(attendu["nb_dans_eventail"], attendu["nb_au_dessus"], attendu["nb_en_dessous"]) > 0


@dbt_requis
def test_short_histories_are_not_calibrated(medallion_db: Path) -> None:
    ligne = _une_ligne(
        medallion_db,
        "SELECT nb_periodes_controlees, nb_dans_eventail FROM gold.fct_prix_entreprise_tranche"
        " WHERE tranche = 'MWH20-499'",
    )
    assert ligne == (0, None)


@dbt_requis
def test_price_section_reports_the_past_check(medallion_db: Path) -> None:
    attendu = calibration_attendue(PRIX_ID)
    texte = formater_diagnostic(
        lire_diagnostic(territoire("69123"), conso_mwh=5_000, database=medallion_db)
    )
    total, dans = attendu["nb_periodes_controlees"], attendu["nb_dans_eventail"]
    assert (
        f"la méthode a encadré le prix moyen réel des 3 années suivantes {dans} fois sur {total}"
        in texte
    )
    assert f"il a dépassé le scénario haut {attendu['nb_au_dessus']} fois" in texte
    assert "ce ne sont pas des essais indépendants" in texte

    # IB : 10 ans d'historique, assez pour des scénarios, pas pour les contrôler.
    texte_ib = formater_diagnostic(
        lire_diagnostic(territoire("69123"), conso_mwh=350, database=medallion_db)
    )
    assert "Historique trop court pour contrôler la méthode sur le passé." in texte_ib


# --- Diagnostic -------------------------------------------------------------------------


@dbt_requis
@pytest.mark.parametrize(
    ("conso", "code"),
    [(19.9, None), (20, "IB"), (499.9, "IB"), (500, None), (150_000, "IG"), (1e7, "IG")],
)
def test_annual_consumption_selects_its_band(
    medallion_db: Path, conso: float, code: str | None
) -> None:
    """IA et IC n'ont pas de prix dans la base synthétique : pas de tranche renvoyée."""
    diagnostic = lire_diagnostic(territoire("69123"), conso_mwh=conso, database=medallion_db)
    assert diagnostic.conso_mwh == conso
    assert (diagnostic.prix.code_tranche if diagnostic.prix else None) == code


@dbt_requis
def test_price_section_gives_bill_range_and_its_limit(medallion_db: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69123"), conso_mwh=350, database=medallion_db)
    texte = formater_diagnostic(diagnostic)
    assert diagnostic.prix is not None

    bloc = texte[texte.index("4. Prix") : texte.index("5. Coût")]
    assert "Consommation 350 MWh/an → tranche IB" in bloc
    assert "12 derniers mois (S1 2025 – S2 2025) : 0,1925 €/kWh HTVA" in bloc
    assert "dont 0,0200 €/kWh de taxes non récupérables" in bloc
    # 350 000 kWh à 0,1925 €/kWh = 67 375 €, arrondi à la centaine.
    assert "facture d'environ 67 400 € par an" in bloc
    assert "plus bas 0,1000 (S1 2016), plus haut 0,1950 (S2 2025)" in bloc
    assert "Prix moyen des 3 prochaines années" in bloc
    assert diagnostic.limites["prix_entreprise"] in bloc


@dbt_requis
@pytest.mark.parametrize(
    ("conso", "message"),
    [
        (None, "Préciser --conso"),
        (1_000, "Aucun prix publié par Eurostat pour la tranche de 1 000 MWh/an."),
        (200_000, "Historique trop court pour cette tranche"),
    ],
)
def test_price_section_explains_missing_figures(
    medallion_db: Path, conso: float | None, message: str
) -> None:
    texte = formater_diagnostic(
        lire_diagnostic(territoire("69123"), conso_mwh=conso, database=medallion_db)
    )
    assert message in texte[texte.index("4. Prix") : texte.index("5. Coût")]


@dbt_requis
def test_non_positive_consumption_is_refused(medallion_db: Path) -> None:
    with pytest.raises(ValueError, match="strictement positive"):
        lire_diagnostic(territoire("69123"), conso_mwh=0, database=medallion_db)


@dbt_requis
def test_report_adds_the_band_price_chart(medallion_db: Path, tmp_path: Path) -> None:
    diagnostic = lire_diagnostic(territoire("69123"), conso_mwh=350, database=medallion_db)
    reglages = Settings(_env_file=None, duckdb_path=medallion_db)

    chemin = generer_rapport_implantation(
        diagnostic, format="md", settings=reglages, output=tmp_path / "prix.md"
    )
    assert "](prix_graphiques/prix_tranche.png)" in chemin.read_text(encoding="utf-8")
    assert (tmp_path / "prix_graphiques" / "prix_tranche.png").read_bytes()[:4] == b"\x89PNG"

    pdf = generer_rapport_implantation(
        diagnostic, format="pdf", settings=reglages, output=tmp_path / "prix.pdf"
    )
    assert pdf.read_bytes().startswith(b"%PDF")

    # Sans scénario (historique trop court), le graphique garde l'historique seul.
    sans_scenario = replace(
        diagnostic,
        prix=lire_diagnostic(territoire("69123"), conso_mwh=200_000, database=medallion_db).prix,
    )
    generer_rapport_implantation(
        sans_scenario, format="md", settings=reglages, output=tmp_path / "ig.md"
    )
    assert (tmp_path / "ig_graphiques" / "prix_tranche.png").exists()


@dbt_requis
def test_json_output_carries_the_price_block(medallion_db: Path) -> None:
    from reporting.implantation import en_json

    contenu = json.loads(
        en_json(lire_diagnostic(territoire("69123"), conso_mwh=350, database=medallion_db))
    )
    assert contenu["conso_mwh"] == 350
    assert contenu["prix"]["code_tranche"] == "IB"
    assert contenu["prix"]["dernier_semestre"] == "2025-S2"


# --- Ligne de commande -------------------------------------------------------------------


@pytest.mark.parametrize(("saisie", "attendu"), [("350", 350.0), ("1 200,5", 1200.5)])
def test_consumption_option_accepts_french_numbers(saisie: str, attendu: float) -> None:
    args = cli.build_parser().parse_args(["diagnose", "--adresse", "x", "--conso", saisie])
    assert args.conso == attendu


@pytest.mark.parametrize("saisie", ["0", "-5", "beaucoup"])
def test_consumption_option_rejects_invalid_values(saisie: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["diagnose", "--adresse", "x", "--conso", saisie])


def test_prix_command_runs_the_ingestion(
    httpx_mock: HTTPXMock, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reglages = settings.model_copy(
        update={"eurostat_base_url": EUROSTAT_URL, "bronze_dir": tmp_path / "bronze"}
    )
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)
    httpx_mock.add_response(json=cube({"3": 0.20}))

    assert cli.main(["prix"]) == 0


@pytest.mark.parametrize(
    ("valeur", "texte"),
    [(67_375, "67 400 €"), (5_496_000, "5 500 000 €"), (950.4, "950 €"), (12.3, "12 €")],
)
def test_bills_keep_three_significant_digits(valeur: float, texte: str) -> None:
    from ingestion.diagnostic import _euros_arrondis

    assert _euros_arrondis(valeur) == texte
