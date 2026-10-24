from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Identifiants hors de `.env`, dans un fichier ignoré par git : il prime sur `.env`
IDENTIFIANTS_LOCAUX = Path(__file__).resolve().parents[1] / "identifiants.local.txt"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECO2MIX_",
        env_file=(".env", IDENTIFIANTS_LOCAUX),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_base_url: str = "https://odre.opendatasoft.com/api/explore/v2.1"

    dataset_id: str = "eco2mix-national-tr"

    consolidation_dataset_id: str = "eco2mix-national-cons-def"

    # Portail API de RTE. Compte gratuit sur https://data.rte-france.com
    rte_api_base_url: str = "https://digital.iservices.rte-france.com"

    ban_base_url: str = "https://data.geopf.fr/geocodage"

    # API Géorisques (risques naturels et technologiques à l'adresse), sans clé
    georisques_base_url: str = "https://www.georisques.gouv.fr/api/v1/"

    # Export GASPAR (risques recensés par commune), servi hors de l'API : repli quand elle se tait
    gaspar_url: str = "https://files.georisques.fr/GASPAR/gaspar.zip"

    # Intensité carbone annuelle des pays européens (Ember, CC BY 4.0)
    ember_url: str = (
        "https://files.ember-energy.org/public-downloads/generation/outputs/"
        "release_generation_yearly_lower.csv"
    )

    # Pays d'une adresse hors de France (Nominatim, OpenStreetMap, ODbL) : une requête par appel
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"

    # Production horaire par filière en Corse et outre-mer (EDF SEI, licence ouverte)
    edf_sei_url: str = (
        "https://opendata.edf.fr/data-fair/api/v1/datasets/"
        "courbe-de-charge-de-la-production-delectricite-par-filiere/raw"
    )

    # API de diffusion d'Eurostat (JSON-stat), sans compte ni clé
    eurostat_base_url: str = "https://ec.europa.eu/eurostat/api/dissemination"

    # Contours des départements d'Etalab (IGN Admin Express simplifié à 1 km), sans clé
    contours_url: str = (
        "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/"
        "departements-1000m.geojson.gz"
    )

    # Terres émergées de Natural Earth (domaine public) : pays voisins en brouillard sur la carte
    terres_url: str = (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
        "ne_50m_land.geojson"
    )

    # Service d'altimétrie de la Géoplateforme (IGN, RGE ALTI), sans clé
    altimetrie_base_url: str = "https://data.geopf.fr/altimetrie"

    rte_client_id: str | None = None
    rte_client_secret: SecretStr | None = None

    lookback_hours: int = Field(default=3, ge=1)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=4, ge=1)

    retry_wait_seconds: float = Field(default=1.0, ge=0)

    # Au-delà de ce nombre de lignes estimées, on abandonne la pagination de `/records`
    records_row_threshold: int = Field(default=900, ge=1)

    # Couche bronze : un Parquet immuable par exécution, fidèle à la source
    bronze_dir: Path = Path("data/bronze")
    duckdb_path: Path = Path("data/warehouse/eco2mix.duckdb")

    reports_dir: Path = Path("data/reports")

    warehouse_backend: Literal["duckdb"] = "duckdb"

    consolidation_lookback_months: int = Field(default=24, ge=1)

    freshness_max_lag_hours: int = Field(default=2, ge=1)

    consolidation_min_coverage: float = Field(default=0.95, gt=0, le=1)


# Instance partagée, mise en cache pour éviter de relire `.env` à chaque appel
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
