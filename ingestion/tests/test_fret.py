from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_fret import (
    FretError,
    lire_aeroports,
    lire_chantiers,
    lire_ports,
    telecharger_aeroports,
)
from ingestion.tests.medallion import DBT_BIN

CATALOGUE = "https://data.gouv.test/api/1/datasets/dgac/"
ARCHIVE = "https://static.data.gouv.test/asp-apt-1990-2025.zip"

PORTS = (
    '"LOCODE_PORT";"PORT";"FACADE";"STATUT";"MOUV";"ANNEE";"TONNAGE_TOT";"MAR_TOT";"EVP_TOT"\n'
    '"FR001";"HAROPA";"MAN";"GPM";"Entree";"2025";50000000;46000000;1600000\n'
    '"FR001";"HAROPA";"MAN";"GPM";"Sortie";"2025";34000000;32000000;1600000\n'
)

AEROPORTS_EN_TETE = (
    "﻿ANMOIS;APT;APT_NOM;APT_ZON;APT_PEQ;APT_PAX_dep;APT_PAX_arr;APT_PAX_tr;"
    "APT_FRP_dep;APT_FRP_arr;APT_NMVT_mxt;APT_NMVT_cgo\n"
)

CHANTIERS = (
    "﻿Nom du chantier;Adresse 1;CP;Latitude;Longitude;VILLE;N°;"
    "Longueur apte à la manutention (ml);Nom de la cour;Manutention 2021\n"
    "AVIGNON COURTINE;Chemin de Courtine;84000;43.939732;4.792701;AVIGNON;116;277;116/120;Grue(s)\n"
    "AVIGNON COURTINE;Chemin de Courtine;84000;43.939732;4.792701;AVIGNON;120;285;;1 Portique\n"
)


def _archive(fichiers: dict[str, str]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for nom, texte in fichiers.items():
            archive.writestr(nom, texte.encode("utf-8"))
    return tampon.getvalue()


def test_ports_entrees_et_sorties_additionnees() -> None:
    [port] = lire_ports(PORTS.encode("utf-8"))

    assert (port["locode"], port["annee"], port["port"]) == ("FR001", "2025", "HAROPA")
    # Marchandises hors tare, séparées du tonnage total qui compte camions et conteneurs
    assert port["marchandises_t"] == 78e6 and port["tonnage_total_t"] == 84e6
    assert port["conteneurs_evp"] == 3.2e6


def test_aeroports_mois_additionnes_en_tonnes() -> None:
    contenu = _archive(
        {
            "ASP_APT_2025.csv": AEROPORTS_EN_TETE
            + "202501;LFPG;PARIS-CHARLES DE GAULLE;MT;1;1;1;0;80000,5;77000;100;3300\n"
            + "202502;LFPG;PARIS-CHARLES DE GAULLE;MT;1;1;1;0;79000;78000,5;100;3400\n",
            "LISEZMOI.txt": "ignoré",
        }
    )

    [aeroport] = lire_aeroports(contenu)

    assert aeroport["code_oaci"] == "LFPG" and aeroport["annee"] == "2025"
    assert aeroport["fret_t"] == 314_001.0 and aeroport["vols_cargo"] == 6_700.0
    # Deux mois sur douze : l'année n'est pas complète
    assert aeroport["nb_mois"] == 2.0


def test_chantier_reunit_ses_cours() -> None:
    [chantier] = lire_chantiers(CHANTIERS.encode("utf-8"))

    assert chantier["chantier"] == "AVIGNON COURTINE" and chantier["code_postal"] == "84000"
    assert chantier["longueur_m"] == 562.0
    assert chantier["manutention"] == "1 Portique, Grue(s)"
    assert (chantier["latitude"], chantier["longitude"]) == (43.939732, 4.792701)


def test_colonne_absente_refusee() -> None:
    with pytest.raises(FretError, match="MAR_TOT"):
        lire_ports(b'"LOCODE_PORT";"PORT";"STATUT";"ANNEE";"TONNAGE_TOT";"EVP_TOT"\n')


def test_archive_dgac_trouvee_par_le_catalogue(httpx_mock: HTTPXMock, settings: Settings) -> None:
    reglages = settings.model_copy(update={"aeroports_fret_dataset_url": CATALOGUE})
    httpx_mock.add_response(
        url=CATALOGUE,
        json={
            "resources": [
                {"title": "Notice_ASP_serie_APT.pdf", "url": "https://static.data.gouv.test/n.pdf"},
                {"title": "ASP_LSN_1990_2025.zip", "url": "https://static.data.gouv.test/lsn.zip"},
                {"title": "ASP_APT_1990_2025.zip", "url": ARCHIVE},
            ]
        },
    )
    httpx_mock.add_response(
        url=ARCHIVE,
        content=_archive(
            {"ASP_APT_2025.csv": AEROPORTS_EN_TETE + "202501;LFLL;LYON;MT;1;1;1;0;10;20;5;1\n"}
        ),
    )

    [aeroport] = telecharger_aeroports(settings=reglages)

    assert aeroport["code_oaci"] == "LFLL" and aeroport["fret_t"] == 30.0


def test_catalogue_sans_archive_refuse(httpx_mock: HTTPXMock, settings: Settings) -> None:
    reglages = settings.model_copy(update={"aeroports_fret_dataset_url": CATALOGUE})
    httpx_mock.add_response(url=CATALOGUE, json={"resources": []})
    with pytest.raises(FretError, match="sans archive"):
        telecharger_aeroports(settings=reglages)


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")
def test_gold_garde_les_poles_au_dessus_de_leur_seuil(medallion_db: Path) -> None:
    import duckdb

    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        poles = connection.execute(
            "select mode, nom, code_departement, annee, volume_t, longueur_voies_m"
            " from gold.fct_poles_fret order by mode, nom"
        ).fetchall()

    # Écartés : Sète sous 5 Mt, Nantes sous 20 000 t, Cognac sous 2 km, l'année 2026 incomplète
    assert poles == [
        ("aeroport", "Ajaccio-Napoléon Bonaparte", "2A", 2025, 6_793.0, None),
        ("aeroport", "Lyon-Saint-Exupéry", "69", 2025, 52_523.0, None),
        ("port", "Ajaccio", "2A", 2025, 600_000.0, None),
        ("port", "HAROPA (Le Havre)", "76", 2025, 78_000_000.0, None),
        ("rail", "Lyon Venissieux", "69", None, None, 2_630.0),
        ("rail", "Noisy-le-Sec", "93", None, None, 3_595.0),
    ]
