from __future__ import annotations

from pathlib import Path

from ingestion.config import Settings
from ingestion.geocode import Territoire

DOSSIER = Path("data/demo")

# Lyon est l'une des communes de la base synthétique ; la tranche ID y a des prix, pas la tranche IF
# de l'exemple de référence (12 MW, 70 000 MWh), d'où un hub de recharge de 2 MW
TERRITOIRE = Territoire(
    adresse="Place Bellecour 69002 Lyon",
    code_insee_commune="69123",
    commune="Lyon",
    code_postal="69002",
    code_departement="69",
    departement="Rhône",
    code_insee_region="84",
    region="Auvergne-Rhône-Alpes",
    latitude=45.7578,
    longitude=4.8320,
    score=1.0,
    precision="housenumber",
)
PROJET = {"puissance_mw": 2.0, "conso_mwh": 15_000.0, "activite": "irve"}

AVERTISSEMENT = (
    "DÉMONSTRATION : base synthétique des tests, chiffres fictifs, sans donnée publique chargée.\n"
    "Projet fictif : hub de recharge de 2 MW et 15 000 MWh par an. Pour une vraie réponse :\n"
    'python -m ingestion.cli refresh --months 180, puis essentiel --adresse "…".'
)


def settings_demo(dossier: Path = DOSSIER) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=dossier / "eco2mix.duckdb",
        bronze_dir=dossier / "bronze",
        reports_dir=dossier / "reports",
    )


# Construite une seule fois (une à deux minutes : PySpark puis dbt build), relue ensuite
def preparer_base(dossier: Path = DOSSIER) -> Settings:
    from ingestion.tests import medallion

    settings = settings_demo(dossier)
    if not settings.duckdb_path.exists():
        if medallion.DBT_BIN is None:
            raise OSError("dbt introuvable : installer dbt-duckdb (voir le README)")
        dossier.mkdir(parents=True, exist_ok=True)
        try:
            # Chemin absolu : dbt résout --target-path depuis son projet, pas depuis la racine
            medallion.build_warehouse(dossier.resolve())
        except BaseException:
            # Base à moitié construite : la refaire au prochain lancement plutôt que la relire
            settings.duckdb_path.unlink(missing_ok=True)
            raise
    return settings
