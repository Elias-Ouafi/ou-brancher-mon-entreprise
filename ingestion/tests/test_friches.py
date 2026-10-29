from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.diagnostic import (
    essentiel_en_dict,
    formater_diagnostic,
    lire_diagnostic,
    verdict,
)
from ingestion.extract_friches import FrichesError, lire_friches, telecharger_friches
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

URL = "https://friches.test/friches.csv"
EN_TETE = (
    '"site_id";"site_nom";"site_type";"site_statut";"site_reconv_type";"site_actu_date";'
    '"comm_insee";"comm_nom";"bati_surface";"unite_fonciere_surface";"proprio_type";'
    '"sol_pollution_existe";"urba_zone_type";"geompoint"\n'
)
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def _csv(*lignes: str) -> bytes:
    return (EN_TETE + "".join(lignes)).encode("cp1252")


def test_friche_lue_avec_sa_position_et_ses_valeurs_absentes() -> None:
    [friche] = lire_friches(
        _csv(
            '"76163_1";"Bourg";"friche agro-industrielle";"friche sans projet";NA;"2024-08-12";'
            '"76163";"Catenay";NA;38601;"personne physique";NA;"A";"POINT (1.330421 49.51214)"\n'
        )
    )

    assert (friche["longitude"], friche["latitude"]) == (1.330421, 49.51214)
    assert friche["unite_fonciere_surface_m2"] == 38601.0
    assert friche["bati_surface_m2"] is None and friche["sol_pollution_existe"] is None
    assert friche["site_statut"] == "friche sans projet"


def test_accents_windows_1252_lus_et_site_sans_point_ecarte() -> None:
    friches = lire_friches(
        _csv(
            '"A";"Dépôt";"inconnu";NA;NA;NA;"01001";"Étrez";NA;NA;NA;NA;NA;"POINT (5 46)"\n',
            '"B";"Sans point";"inconnu";NA;NA;NA;"01001";"Étrez";NA;NA;NA;NA;NA;NA\n',
        )
    )

    assert [(f["site_id"], f["site_nom"], f["comm_nom"]) for f in friches] == [
        ("A", "Dépôt", "Étrez")
    ]


# Constaté en réel : des lignes en UTF-8 et d'autres en Windows-1252 dans le même fichier
def test_encodages_melanges_lus_ligne_a_ligne() -> None:
    ligne = '"{}";"{}";"inconnu";NA;NA;NA;"01001";"Étrez";NA;NA;NA;NA;NA;"POINT (5 46)"\n'
    contenu = EN_TETE.encode() + ligne.format("A", "Découflé").encode("utf-8")
    contenu += ligne.format("B", "Dépôt").encode("cp1252")

    assert [f["site_nom"] for f in lire_friches(contenu)] == ["Découflé", "Dépôt"]


def test_colonne_absente_refusee_et_fichier_vide_refuse(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    reglages = settings.model_copy(update={"friches_url": URL})
    with pytest.raises(FrichesError, match="colonnes absentes"):
        lire_friches(b'"a";"b"\n1;2\n')
    httpx_mock.add_response(url=URL, content=_csv())
    with pytest.raises(FrichesError, match="aucune friche"):
        telecharger_friches(settings=reglages)


@avec_dbt
def test_gold_ne_garde_que_les_friches_d_un_hectare_non_reconverties(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        ids = connection.execute("select site_id from gold.fct_friches order by 1").fetchall()

    assert ids == [("F1",), ("F2",), ("F3",)]


@avec_dbt
def test_diagnostic_donne_les_friches_du_rayon_les_plus_proches_d_abord(
    medallion_db: Path,
) -> None:
    lyon = territoire("69123", "Lyon", latitude=45.75, longitude=4.85)
    diagnostic = lire_diagnostic(lyon, database=medallion_db)

    assert diagnostic.nb_friches == 2
    assert [(f.site_id, round(f.distance_km)) for f in diagnostic.friches] == [("F1", 2), ("F2", 5)]
    texte = formater_diagnostic(diagnostic)
    assert "2 friches de 1 ha ou plus à moins de 10 km" in texte
    assert (
        "Site F1 à Lyon (friche industrielle, 5,0 ha, zone U, friche sans projet) à 2 km" in texte
    )
    foncier = next(c for c in verdict(diagnostic) if c.nom == "Foncier")
    assert "2 friche(s) de 1 ha ou plus à moins de 10 km" in foncier.detail
    reponse = essentiel_en_dict(diagnostic)["friches"]
    assert (
        reponse["nb_dans_le_rayon"] == 2 and "friches" in essentiel_en_dict(diagnostic)["limites"]
    )


@avec_dbt
def test_aucune_friche_proche_est_dit(medallion_db: Path) -> None:
    loin = territoire("01053", "Bourg", latitude=46.2, longitude=5.2)
    diagnostic = lire_diagnostic(loin, database=medallion_db)

    assert diagnostic.nb_friches == 0 and diagnostic.friches == []
    assert "aucune friche de 1 ha ou plus référencée à moins de 10 km" in formater_diagnostic(
        diagnostic
    )
    assert replace(diagnostic, nb_friches=None).nb_friches is None
