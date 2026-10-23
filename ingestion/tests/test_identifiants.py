from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ingestion.config import IDENTIFIANTS_LOCAUX, Settings

RACINE = IDENTIFIANTS_LOCAUX.parent


def test_identifiants_lus_et_masques(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ECO2MIX_RTE_CLIENT_SECRET=ancien\n", encoding="utf-8")
    identifiants = tmp_path / "identifiants.local.txt"
    identifiants.write_text(
        "# commentaire\nECO2MIX_RTE_CLIENT_ID=id\nECO2MIX_RTE_CLIENT_SECRET=s3cret\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=(env, identifiants))

    assert settings.rte_client_id == "id"
    assert settings.rte_client_secret.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings) and "s3cret" not in str(settings.model_dump())


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_identifiants_jamais_versionnes() -> None:
    def ignore(nom: str) -> bool:
        resultat = subprocess.run(
            ["git", "check-ignore", "-q", nom], cwd=RACINE, check=False, capture_output=True
        )
        return resultat.returncode == 0

    assert ignore("identifiants.local.txt")
    assert ignore(".env")
    assert not ignore("identifiants.exemple.txt")


def test_le_modele_ne_contient_aucune_valeur() -> None:
    lignes = (RACINE / "identifiants.exemple.txt").read_text(encoding="utf-8").splitlines()
    affectations = [ligne for ligne in lignes if "=" in ligne and not ligne.startswith("#")]
    assert affectations and all(ligne.endswith("=") for ligne in affectations)
