from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_dvf import DvfError, lire_mutations, telecharger_ventes_locaux
from ingestion.tests.medallion import DBT_BIN

EN_TETE = (
    "id_mutation,date_mutation,numero_disposition,nature_mutation,valeur_fonciere,"
    "code_commune,code_departement,type_local,surface_reelle_bati,longitude,latitude\n"
)
ACTIVITE = "Local industriel. commercial ou assimilé"
URL = "https://dvf.test/{annee}/full.csv.gz"


def _csv(*lignes: str) -> bytes:
    return gzip.compress((EN_TETE + "".join(lignes)).encode("utf-8"))


def _lignes(texte: str) -> list[dict[str, str]]:
    import csv
    import io

    return list(csv.DictReader(io.StringIO(EN_TETE + texte)))


def test_une_vente_de_local_d_activite_gardee_avec_son_prix_et_sa_surface() -> None:
    [vente] = lire_mutations(
        _lignes(
            f"2025-1,2025-01-07,000001,Vente,250000,69123,69,{ACTIVITE},60,4.85,45.75\n"
            f"2025-1,2025-01-07,000001,Vente,250000,69123,69,{ACTIVITE},40,4.85,45.75\n"
            "2025-1,2025-01-07,000001,Vente,250000,69123,69,Dépendance,,4.85,45.75\n"
        )
    )

    assert vente["valeur_fonciere"] == 250_000.0 and vente["surface_bati_m2"] == 100.0
    assert vente["nb_locaux_activite"] == 2.0 and vente["nb_locaux_habitation"] == 0.0


def test_logement_compte_et_autres_natures_ou_biens_ecartes() -> None:
    ventes = lire_mutations(
        _lignes(
            f"A,2025-01-07,000001,Vente,100,69123,69,{ACTIVITE},50,4.8,45.7\n"
            "A,2025-01-07,000001,Vente,100,69123,69,Maison,90,4.8,45.7\n"
            f"B,2025-01-08,000001,Echange,100,69123,69,{ACTIVITE},50,4.8,45.7\n"
            "C,2025-01-09,000001,Vente,100,69123,69,Appartement,50,4.8,45.7\n"
        )
    )

    # B (échange) et C (aucun local d'activité) disparaissent ; A garde le compte du logement
    assert [(v["id_mutation"], v["nb_locaux_habitation"]) for v in ventes] == [("A", 1.0)]


def test_colonne_absente_refusee(httpx_mock: HTTPXMock, settings: Settings) -> None:
    reglages = settings.model_copy(update={"dvf_url": URL, "dvf_nb_annees": 1})
    httpx_mock.add_response(url=URL.format(annee=2025), content=gzip.compress(b"a,b\n1,2\n"))

    with pytest.raises(DvfError, match="colonnes absentes"):
        telecharger_ventes_locaux(settings=reglages, aujourd_hui=datetime(2025, 6, 1, tzinfo=UTC))


def test_annee_non_publiee_sautee_puis_nombre_d_annees_respecte(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    reglages = settings.model_copy(update={"dvf_url": URL, "dvf_nb_annees": 2})
    ligne = f"{{a}}-1,{{a}}-02-01,000001,Vente,100000,01053,01,{ACTIVITE},50,5.2,46.2\n"
    httpx_mock.add_response(url=URL.format(annee=2026), status_code=404)
    for annee in (2025, 2024):
        httpx_mock.add_response(url=URL.format(annee=annee), content=_csv(ligne.format(a=annee)))

    ventes = telecharger_ventes_locaux(
        settings=reglages, aujourd_hui=datetime(2026, 10, 28, tzinfo=UTC)
    )

    assert sorted(v["id_mutation"] for v in ventes) == ["2024-1", "2025-1"]


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")
def test_gold_median_par_commune_avec_repli_departement(medallion_db: Path) -> None:
    import duckdb

    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        lignes = connection.execute(
            "select niveau, code_zone, nb_ventes, prix_m2_median, fiable"
            " from gold.fct_prix_locaux order by niveau, code_zone"
        ).fetchall()

    # Lyon : arrondissement ramené à la commune ; écartés : logement, deux dispositions, prix
    # aberrant et vente de plus de cinq ans ; Bourg n'a que deux ventes, donc non fiable
    assert lignes == [
        ("commune", "01053", 2, 1750.0, False),
        ("commune", "69123", 6, 3250.0, True),
        ("departement", "01", 2, 1750.0, False),
        ("departement", "69", 6, 3250.0, True),
    ]
