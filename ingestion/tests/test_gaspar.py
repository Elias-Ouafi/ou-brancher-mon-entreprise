from __future__ import annotations

import io
import zipfile

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_gaspar import GasparError, lire_export, lire_plans, telecharger_archive
from ingestion.georisques import risques_gaspar

GASPAR_URL = "https://files.georisques.test/GASPAR/gaspar.zip"
ENTETE = "cod_commune;lib_commune;lib_risque;num_risque"
ENTETE_PLANS = (
    "CODE MODELE;CODE PROCEDURE;LIBELLE PROCEDURE;CODE INSEE COMMUNE;CODE RISQUE 1;"
    "LIBELLE RISQUE 1;CODE RISQUE 2;LIBELLE RISQUE 2;CODE RISQUE 3;LIBELLE RISQUE 3;"
    "PRESCRIPTION;APPROBATION;LIBELLE ETAT;LIBELLE SOUS-ETAT"
)


def archive(**fichiers: str) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as zip_:
        for nom, contenu in fichiers.items():
            zip_.writestr(nom, contenu.encode("utf-8-sig"))
    return tampon.getvalue()


def test_latest_ddrm_file_of_the_export_is_read() -> None:
    contenu = archive(
        **{
            "ddrm_risq_gaspar_2026-04-01.csv": f"{ENTETE}\n91161;Chilly;Séisme;13\n",
            "ddrm_risq_gaspar_2026-10-05.csv": (
                f"{ENTETE}\r\n91161;Chilly-Mazarin;Inondation;11\r\n"
                "2A004;Ajaccio;Feu de forêt;16\r\n"
            ),
            "catnat_gaspar_2026-10-05.csv": "id_gaspar;code_commune\n",
        }
    )

    lignes = lire_export(contenu)

    assert lignes[0] == {
        "code_commune": "91161",
        "num_risque": "11",
        "libelle_commune": "Chilly-Mazarin",
        "libelle_risque": "Inondation",
        "millesime": "2026-10-05",
    }
    assert [ligne["code_commune"] for ligne in lignes] == ["91161", "2A004"]


@pytest.mark.parametrize(
    ("contenu", "message"),
    [
        (b"pas un zip", "illisible"),
        (archive(**{"catnat_gaspar_2026-10-05.csv": "x\n"}), "sans fichier ddrm"),
        (archive(**{"ddrm_risq_gaspar_2026-10-05.csv": "commune;risque\n"}), "colonnes absentes"),
    ],
)
def test_unexpected_export_is_rejected(contenu: bytes, message: str) -> None:
    with pytest.raises(GasparError, match=message):
        lire_export(contenu)


def test_export_is_downloaded_over_https(httpx_mock: HTTPXMock, settings: Settings) -> None:
    httpx_mock.add_response(
        url=GASPAR_URL,
        content=archive(**{"ddrm_risq_gaspar_2026-10-05.csv": f"{ENTETE}\n01001;Ain;Séisme;13\n"}),
    )
    reglages = settings.model_copy(update={"gaspar_url": GASPAR_URL})

    lignes = lire_export(telecharger_archive(settings=reglages))
    assert [ligne["num_risque"] for ligne in lignes] == ["13"]


def test_latest_plan_file_of_each_type_is_read() -> None:
    contenu = archive(
        **{
            "pprn_gaspar_2026-04-01.csv": f"{ENTETE_PLANS}\n",
            "pprn_gaspar_2026-10-05.csv": (
                f"{ENTETE_PLANS}\r\n"
                "PPRN-I;P1;PPRI de Lyon ;69123;10;Naturel;11;Inondation;112;Par une crue lente;"
                "2004-01-07;2009-03-02;Opposable;Approuvé\r\n"
                # Même commune, procédure et aléa : la dernière ligne publiée l'emporte
                "PPRN-I;P1;PPRI de Lyon;69123;10;Naturel;11;Inondation;112;Par une crue lente;"
                "2004-01-07;2010-01-01;Opposable;Approuvé\r\n"
                "PPRN-I;P2;Plan sans commune;;10;Naturel;11;Inondation;;;2020-01-01;;Prescrit;"
                "Prescrit\r\n"
            ),
            "pprt_gaspar_2026-10-05.csv": (
                f"{ENTETE_PLANS}\n"
                "PPRT;T1;PPRT Total;69152;20;Technologique;21;Risque industriel;;;"
                "2015-04-21;;Prescrit;Prorogé\n"
            ),
        }
    )

    plans = lire_plans(contenu)

    assert [(p["code_commune"], p["code_procedure"], p["code_risque"]) for p in plans] == [
        ("69123", "P1", "112"),
        (None, "P2", "11"),
        ("69152", "T1", "21"),
    ]
    assert plans[0]["date_approbation"] == "2010-01-01"
    assert plans[0]["libelle_plan"] == "PPRI de Lyon"
    assert (plans[0]["type_plan"], plans[2]["type_plan"]) == ("PPRN", "PPRT")
    assert plans[2]["date_approbation"] is None and plans[2]["sous_etat"] == "Prorogé"
    assert {p["millesime"] for p in plans} == {"2026-10-05"}


@pytest.mark.parametrize(
    ("contenu", "message"),
    [
        (archive(**{"ddrm_risq_gaspar_2026-10-05.csv": f"{ENTETE}\n"}), "sans fichier pprn"),
        (archive(**{"pprt_gaspar_2026-10-05.csv": "CODE PROCEDURE\n"}), "colonnes absentes"),
    ],
)
def test_export_without_readable_plans_is_rejected(contenu: bytes, message: str) -> None:
    with pytest.raises(GasparError, match=message):
        lire_plans(contenu)


def test_no_local_rows_means_no_fallback() -> None:
    assert risques_gaspar([]) is None
