from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import duckdb
import pytest
from pytest_httpx import HTTPXMock

from ingestion.comparaison import lignes as lignes_comparees
from ingestion.config import Settings
from ingestion.diagnostic import essentiel_en_dict, formater_diagnostic, lire_diagnostic
from ingestion.extract_reseaux_chaleur import (
    ReseauxChaleurError,
    lambert93_vers_wgs84,
    lire_reseaux,
    telecharger_reseaux_chaleur,
)
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

JEU = "https://jeu.test/api/1/datasets/fcu/"
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")


def _entite(geometrie: dict | None, **proprietes: object) -> dict:
    return {"type": "Feature", "properties": proprietes, "geometry": geometrie}


def _archive(traces: list[dict], sans_traces: list[dict] | None = None) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as zf:
        zf.writestr("reseaux_de_chaleur.geojson", json.dumps({"features": traces}))
        if sans_traces is not None:
            zf.writestr(
                "reseaux_de_chaleur_sans_traces.geojson", json.dumps({"features": sans_traces})
            )
    return tampon.getvalue()


def test_lambert93_vers_wgs84_sur_l_origine_et_paris() -> None:
    assert lambert93_vers_wgs84(700_000, 6_600_000) == pytest.approx((46.5, 3.0), abs=1e-7)
    assert lambert93_vers_wgs84(652_469.02, 6_862_035.26) == pytest.approx(
        (48.8566, 2.3522), abs=1e-4
    )


# Pièges constatés : tracés en Lambert-93, près de 200 réseaux sans identifiant, deux entités
# du même nom, des sommets à quelques mètres et des coordonnées hors de France
def test_points_espaces_de_100_m_et_pieges_de_la_source() -> None:
    sommets = [[700_000 + 30 * k, 6_600_000] for k in range(11)]
    lignes = lire_reseaux(
        _archive(
            [
                _entite(
                    {"type": "MultiLineString", "coordinates": [sommets]},
                    **{"Identifiant reseau": "0301C", "nom_reseau": "Centre", "Taux EnR&R": 61.2},
                    **{"reseaux classes": True, "departement": "Allier"},
                ),
                _entite(
                    {"type": "MultiLineString", "coordinates": [[[700_000, 6_610_000]]]},
                    nom_reseau="Avignon",
                ),
                _entite(
                    {"type": "MultiLineString", "coordinates": [[[700_000, 6_620_000]]]},
                    nom_reseau="Avignon",
                ),
                _entite(
                    {"type": "MultiLineString", "coordinates": [[[-1_022_531, 120]]]},
                    nom_reseau="Hors de France",
                ),
                _entite(None, nom_reseau=None),
            ],
            [_entite({"type": "Point", "coordinates": [700_000, 6_630_000]}, nom_reseau="Bourg")],
        )
    )

    centre = [ligne for ligne in lignes if ligne["reseau_id"] == "0301C"]
    assert len(centre) == 4  # sommets à 0, 120, 240 et 300 m
    assert centre[0]["classe"] == "oui" and centre[0]["taux_enr_pct"] == 61.2
    assert (centre[0]["latitude"], centre[0]["longitude"]) == pytest.approx((46.5, 3.0))
    avignon = [
        (ligne["reseau_id"], ligne["rang"]) for ligne in lignes if "Avignon" in ligne["reseau_id"]
    ]
    assert avignon == [("sans-id:Avignon", "1"), ("sans-id:Avignon", "2")]
    assert not any("Hors de France" in ligne["reseau_id"] for ligne in lignes)
    [bourg] = [ligne for ligne in lignes if ligne["a_trace"] == "non"]
    assert bourg["reseau_id"] == "sans-id:Bourg"


def test_derniere_archive_du_jeu_telechargee(httpx_mock: HTTPXMock, settings: Settings) -> None:
    reglages = settings.model_copy(update={"reseaux_chaleur_jeu_url": JEU})
    ressources = [
        {"url": "https://fichiers.test/ancienne.zip", "last_modified": "2026-09-14T10:31:22"},
        {"url": "https://fichiers.test/nomenclature.xlsx", "last_modified": "2026-11-01T00:00:00"},
        {"url": "https://fichiers.test/recente.zip", "last_modified": "2026-10-16T13:01:06"},
    ]
    httpx_mock.add_response(url=JEU, json={"resources": ressources})
    point = {"type": "Point", "coordinates": [700_000, 6_600_000]}
    httpx_mock.add_response(
        url="https://fichiers.test/recente.zip",
        content=_archive([_entite(point, **{"Identifiant reseau": "1"})], []),
    )

    [ligne] = telecharger_reseaux_chaleur(settings=reglages)

    assert ligne["reseau_id"] == "1" and ligne["a_trace"] == "oui"


def test_couche_absente_et_jeu_sans_archive_refuses(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    with pytest.raises(ReseauxChaleurError, match="couches absentes"):
        lire_reseaux(_archive([]))
    reglages = settings.model_copy(update={"reseaux_chaleur_jeu_url": JEU})
    httpx_mock.add_response(url=JEU, json={"resources": []})
    with pytest.raises(ReseauxChaleurError, match="aucune archive"):
        telecharger_reseaux_chaleur(settings=reglages)


@avec_dbt
def test_seul_le_dernier_chargement_et_un_fait_par_departement(medallion_db: Path) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        rangs = connection.execute(
            "select rang from gold.fct_reseaux_chaleur where reseau_id = '6901C' order by 1"
        ).fetchall()
        departements = connection.execute(
            "select code_departement from etoile.fait_reseau_chaleur"
            " where reseau_id = '6902C' order by 1"
        ).fetchall()

    assert rangs == [(1,), (2,)]
    assert departements == [("01",), ("69",)]


@avec_dbt
def test_reseaux_du_rayon_les_plus_proches_d_abord(medallion_db: Path) -> None:
    lyon = territoire("69123", "Lyon", latitude=45.75, longitude=4.85)
    diagnostic = lire_diagnostic(lyon, database=medallion_db)

    assert diagnostic.reseaux_chaleur is not None
    assert [(r.nom, round(r.distance_km)) for r in diagnostic.reseaux_chaleur] == [
        ("Lyon Centre", 1),
        ("Chaufferie Est", 3),
        ("Réseau 6902C", 6),
    ]
    texte = formater_diagnostic(diagnostic)
    assert (
        "Réseaux de chaleur : le plus proche, Lyon Centre (Lyon) à 1 km, 65 % d'énergies"
        " renouvelables et de récupération, classé ; puis Chaufferie Est (Lyon) à 3 km,"
        " tracé non publié, centre de la commune" in texte
    )
    reponse = essentiel_en_dict(diagnostic)
    assert reponse["reseaux_chaleur"]["plus_proches"][0]["nom"] == "Lyon Centre"
    assert "reseaux_chaleur" in reponse["limites"]
    loin = lire_diagnostic(
        territoire("01053", "Bourg", latitude=46.2, longitude=5.2), database=medallion_db
    )
    assert loin.reseaux_chaleur == []
    assert "Réseaux de chaleur : aucun réseau recensé à moins de 10 km" in formater_diagnostic(loin)
    comparees = {c.indicateur: c.valeurs for c in lignes_comparees([diagnostic, loin])}
    assert comparees["Réseau de chaleur (France Chaleur Urbaine)"] == [
        "Lyon Centre, à 1 km",
        "aucun à moins de 10 km",
    ]
