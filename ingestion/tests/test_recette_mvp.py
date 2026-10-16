from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.diagnostic import essentiel_en_dict, lire_diagnostic
from ingestion.tests.medallion import DBT_BIN, PRIX_IA
from ingestion.tests.test_diagnostic import territoire

pytestmark = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

RECETTE = [
    ("75056", "Paris", "France continentale", True),
    ("69123", "Lyon", "France continentale", True),
    ("59350", "Lille", "France continentale", True),
    ("13055", "Marseille", "France continentale", True),
    ("2A004", "Ajaccio", "Corse", False),
    ("2B033", "Bastia", "Corse", False),
    ("97120", "Pointe-à-Pitre", "Guadeloupe", False),
    ("97209", "Fort-de-France", "Martinique", False),
    ("97302", "Cayenne", "Guyane", False),
    ("97411", "Saint-Denis", "La Réunion", False),
    ("97611", "Mamoudzou", "Mayotte", False),
]


@pytest.mark.parametrize(("code", "commune", "systeme", "decarbone"), RECETTE)
def test_prix_et_verdict_carbone(
    medallion_db: Path, code: str, commune: str, systeme: str, decarbone: bool
) -> None:
    reponse = essentiel_en_dict(lire_diagnostic(territoire(code, commune), database=medallion_db))

    assert reponse["prix"]["code_tranche"] == "IA"
    assert reponse["prix"]["prix_htva_eur_kwh"] == pytest.approx(PRIX_IA)
    assert reponse["carbone"]["systeme_electrique"] == systeme
    assert reponse["carbone"]["est_decarbone"] is decarbone
