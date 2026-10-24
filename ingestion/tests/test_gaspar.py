from __future__ import annotations

import io
import zipfile

import pytest
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_gaspar import GasparError, lire_export, telecharger_export
from ingestion.georisques import risques_gaspar

GASPAR_URL = "https://files.georisques.test/GASPAR/gaspar.zip"
ENTETE = "cod_commune;lib_commune;lib_risque;num_risque"


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

    assert [ligne["num_risque"] for ligne in telecharger_export(settings=reglages)] == ["13"]


def test_no_local_rows_means_no_fallback() -> None:
    assert risques_gaspar([]) is None
