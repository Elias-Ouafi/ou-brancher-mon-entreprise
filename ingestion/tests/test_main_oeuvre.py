from __future__ import annotations

import io
import re
import zipfile
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest
from pytest_httpx import HTTPXMock

from ingestion.comparaison import lignes
from ingestion.config import Settings
from ingestion.diagnostic import (
    completer_main_oeuvre,
    diagnostic_en_dict,
    formater_diagnostic,
    lire_diagnostic,
)
from ingestion.extract_main_oeuvre import (
    lire_diplomes,
    lire_recrutements,
    telecharger_diplomes,
)
from ingestion.main_oeuvre import MainOeuvreError, lire_isochrone, lire_zone_trajet
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire

ISOCHRONE_URL = "https://isochrone.test/isochrone"
DIPLOMES_URL = "https://insee.test/diplomes.zip"
avec_dbt = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")
LYON = territoire("69123", "Lyon", latitude=45.76, longitude=4.83)
# Contient les centres de Lyon et de Colombier-Saugnieu, pas L'Abergement-Clémenciat ni Ajaccio
AUTOUR_DE_LYON = [[4.7, 45.6], [5.2, 45.6], [5.2, 45.9], [4.7, 45.9], [4.7, 45.6]]


def _archive(lignes_csv: list[str]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        entete = '"GEO";"GEO_OBJECT";"AGE";"SEX";"EDUC";"RP_MEASURE";"FREQ";"OBS_STATUS";'
        entete += '"TIME_PERIOD";"OBS_VALUE"\n'
        archive.writestr("DS_data.csv", "﻿" + entete + "".join(lignes_csv))
        archive.writestr("DS_metadata.csv", "")
    return tampon.getvalue()


def _ligne(geo: str, objet: str, sexe: str, educ: str, annee: int, valeur: str) -> str:
    return f'"{geo}";"{objet}";"Y_GE15";"{sexe}";"{educ}";"POP";"A";"A";{annee};{valeur}\n'


def test_diplomes_communes_entieres_tous_sexes_au_dernier_millesime() -> None:
    contenu = _archive(
        [
            _ligne("91161", "COM", "_T", "500T702_RP", 2023, "5225.9"),
            _ligne("91161", "COM", "_T", "300_RP", 2023, "2816.6"),
            _ligne("91161", "COM", "_T", "350T351_RP", 2023, ""),
            _ligne("91161", "COM", "_T", "_T", 2023, "14306.8"),
            # Écartés : autre sexe, autre maille, diplôme non retenu, millésime ancien
            _ligne("91161", "COM", "F", "500T702_RP", 2023, "3000"),
            _ligne("75101", "ARM", "_T", "500T702_RP", 2023, "9000"),
            _ligne("91161", "COM", "_T", "001T100_RP", 2023, "2545"),
            _ligne("91161", "COM", "_T", "500T702_RP", 2017, "4000"),
            _ligne("01001", "COM", "_T", "500T702_RP", 2017, "70"),
        ]
    )

    assert lire_diplomes(contenu) == [
        {
            "code_commune": "91161",
            "millesime": "2023",
            "nb_cap_bep": 2816.6,
            # Valeur vide : comptée dans une autre catégorie
            "nb_bac": 0.0,
            "nb_superieur": 5225.9,
            "nb_15_ans_plus": 14306.8,
        }
    ]


def test_archive_illisible_ou_incomplete_refusee(httpx_mock: HTTPXMock, settings: Settings) -> None:
    with pytest.raises(MainOeuvreError, match="archive illisible"):
        lire_diplomes(b"pas un zip")
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("autre.csv", "")
    with pytest.raises(MainOeuvreError, match="absent de l'archive"):
        lire_diplomes(tampon.getvalue())
    with zipfile.ZipFile(tampon := io.BytesIO(), "w") as archive:
        archive.writestr("DS_data.csv", '"GEO";"EDUC"\n')
    with pytest.raises(MainOeuvreError, match="colonnes absentes"):
        lire_diplomes(tampon.getvalue())
    httpx_mock.add_response(url=DIPLOMES_URL, content=_archive([]))
    with pytest.raises(MainOeuvreError, match="aucune commune"):
        telecharger_diplomes(settings=settings.model_copy(update={"diplomes_url": DIPLOMES_URL}))


def _classeur(lignes_bmo: list[tuple]) -> bytes:
    classeur = openpyxl.Workbook()
    classeur.active.title = "Description_des_variables"
    feuille = classeur.create_sheet("BMO_2026_open_data")
    feuille.append(["annee", "Code métier BMO", "Nom métier BMO", "Dept", "met", "xmet", "smet"])
    for ligne in lignes_bmo:
        feuille.append(list(ligne))
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


def test_recrutements_sommes_par_departement_sans_les_bassins_secrets() -> None:
    contenu = _classeur(
        [
            (2026, "G1X74", "Froid", "91", "100", "90", "0"),
            (2026, "G1X74", "Froid", "91", "53", "58", "*"),
            (2026, "G1X74", "Froid", "91", "*", "*", "*"),
            (2026, "G1X74", "Froid", 1, "4", "1", "0"),
        ]
    )

    lignes_lues = sorted(lire_recrutements(contenu), key=lambda r: r["code_departement"])
    assert [
        (r["code_departement"], r["projets"], r["nb_bassins_secrets"]) for r in lignes_lues
    ] == [
        ("01", 4.0, 0.0),
        ("91", 153.0, 1.0),
    ]
    assert lignes_lues[1] | {"libelle_metier": None} == {
        "code_departement": "91",
        "code_metier": "G1X74",
        "libelle_metier": None,
        "annee": "2026",
        "projets": 153.0,
        "projets_difficiles": 148.0,
        "nb_bassins": 3.0,
        "nb_bassins_secrets": 1.0,
    }


def test_classeur_illisible_ou_sans_donnees_refuse() -> None:
    with pytest.raises(MainOeuvreError, match="illisible"):
        lire_recrutements(b"pas un classeur")
    classeur = openpyxl.Workbook()
    tampon = io.BytesIO()
    classeur.save(tampon)
    with pytest.raises(MainOeuvreError, match="feuille"):
        lire_recrutements(tampon.getvalue())
    with pytest.raises(MainOeuvreError, match="aucun projet"):
        lire_recrutements(_classeur([]))


def test_isochrone_lue_en_polygones_a_deux_coordonnees() -> None:
    polygone = {"geometry": {"type": "Polygon", "coordinates": [[[1, 2, 30], [3, 4, 30]]]}}
    multi = {"geometry": {"type": "MultiPolygon", "coordinates": [[[[1, 2], [3, 4]]]]}}

    assert lire_isochrone(polygone) == [[[[1, 2], [3, 4]]]]
    assert lire_isochrone(multi) == [[[[1, 2], [3, 4]]]]
    for invalide in (
        [],
        {"geometry": {"type": "Point"}},
        {"geometry": {"type": "Polygon", "coordinates": []}},
    ):
        with pytest.raises(MainOeuvreError):
            lire_isochrone(invalide)


def test_isochrone_demandee_en_voiture_a_l_arrivee_sur_45_minutes(
    httpx_mock: HTTPXMock, settings: Settings
) -> None:
    reglages = settings.model_copy(update={"isochrone_url": ISOCHRONE_URL})
    httpx_mock.add_response(
        url=re.compile("^" + re.escape(ISOCHRONE_URL)),
        json={"geometry": {"type": "Polygon", "coordinates": [AUTOUR_DE_LYON]}},
    )

    assert lire_zone_trajet(latitude=45.76, longitude=4.83, settings=reglages) == [[AUTOUR_DE_LYON]]
    parametres = httpx_mock.get_request().url.params
    assert (parametres["point"], parametres["costValue"]) == ("4.83,45.76", "2700")
    assert (parametres["profile"], parametres["direction"]) == ("car", "arrival")


@pytest.fixture
def reglages(medallion_db: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=medallion_db,
        bronze_dir=tmp_path / "bronze",
        isochrone_url=ISOCHRONE_URL,
        retry_wait_seconds=0.0,
    )


@avec_dbt
def test_diplomes_sommes_dans_l_isochrone_et_recrutements_du_departement(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(
        url=re.compile("^" + re.escape(ISOCHRONE_URL)),
        json={"geometry": {"type": "Polygon", "coordinates": [AUTOUR_DE_LYON]}},
    )
    diagnostic = completer_main_oeuvre(lire_diagnostic(LYON, settings=reglages), settings=reglages)

    m = diagnostic.main_oeuvre
    assert (m.nb_communes, m.nb_superieur, m.nb_cap_bep_bac) == (2, 250_700.0, 101_100.0)
    texte = formater_diagnostic(diagnostic)
    assert (
        "Main-d'œuvre : 250 700 diplômés du supérieur et 101 100 titulaires d'un CAP-BEP ou d'un"
        " bac, parmi 452 500 habitants de 15 ans ou plus (2 communes à moins de 45 min en"
        " voiture, recensement 2023)" in texte
    )
    # Le métier hors seed (A0X40) n'est pas compté
    assert "400 projets de recrutement en 2026" in texte
    assert "dont 75,0 % jugés difficiles" in texte
    assert "1 des 4 croisements bassin-métier sont sous secret statistique" in texte
    assert diagnostic_en_dict(diagnostic)["main_oeuvre"]["nb_communes"] == 2
    tableau = {ligne.indicateur: ligne.valeurs for ligne in lignes([diagnostic, diagnostic])}
    assert tableau["Main-d'œuvre à 45 min"][0] == "250 700 du supérieur, 101 100 CAP-BEP ou bac"
    assert tableau["Recrutements techniques (BMO)"][0] == "400 projets, 75,0 % difficiles"


@avec_dbt
def test_aucune_commune_recensee_dans_l_isochrone_est_dite_non_disponible(
    httpx_mock: HTTPXMock, reglages: Settings
) -> None:
    httpx_mock.add_response(
        url=re.compile("^" + re.escape(ISOCHRONE_URL)),
        json={"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
    )
    diagnostic = completer_main_oeuvre(lire_diagnostic(LYON, settings=reglages), settings=reglages)

    assert "Main-d'œuvre : non disponible : aucune commune recensée" in formater_diagnostic(
        diagnostic
    )
    assert lignes([diagnostic, diagnostic])[-1].indicateur == "Brancher ici ?"


@avec_dbt
def test_sans_diplomes_charges_l_isochrone_n_est_pas_demandee(reglages: Settings) -> None:
    diagnostic = lire_diagnostic(LYON, settings=reglages)
    sans = replace(diagnostic, chargements={})

    assert completer_main_oeuvre(sans, settings=reglages) is sans
