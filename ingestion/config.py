from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# Paramètres du pipeline d'ingestion
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ECO2MIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_base_url: str = "https://odre.opendatasoft.com/api/explore/v2.1"

    # Dataset temps réel, ingéré toutes les heures
    dataset_id: str = "eco2mix-national-tr"

    # Dataset consolidé puis définitif, ingéré tous les mois
    consolidation_dataset_id: str = "eco2mix-national-cons-def"

    # Portail API de RTE. Compte gratuit sur https://data.rte-france.com
    rte_api_base_url: str = "https://digital.iservices.rte-france.com"

    # API Adresse de la Géoplateforme (IGN)
    ban_base_url: str = "https://data.geopf.fr/geocodage"

    # API Géorisques (risques naturels et technologiques à l'adresse), sans clé
    georisques_base_url: str = "https://www.georisques.gouv.fr/api/v1/"

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

    # Profondeur de la fenêtre glissante par défaut
    lookback_hours: int = Field(default=3, ge=1)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=4, ge=1)

    # Multiplicateur de l'attente exponentielle entre deux tentatives
    retry_wait_seconds: float = Field(default=1.0, ge=0)

    # Au-delà de ce nombre de lignes estimées, on abandonne la pagination de `/records`
    records_row_threshold: int = Field(default=900, ge=1)

    # Couche bronze : un Parquet immuable par exécution, fidèle à la source
    bronze_dir: Path = Path("data/bronze")
    duckdb_path: Path = Path("data/warehouse/eco2mix.duckdb")

    # Rapports PDF générés à la demande (commandes `report` et `diagnose`)
    reports_dir: Path = Path("data/reports")

    # Backend de chargement
    warehouse_backend: Literal["duckdb"] = "duckdb"

    # Profondeur relue à chaque run mensuel
    consolidation_lookback_months: int = Field(default=24, ge=1)

    freshness_max_lag_hours: int = Field(default=2, ge=1)

    # Part minimale des pas de temps attendus pour valider un mois consolidé
    consolidation_min_coverage: float = Field(default=0.95, gt=0, le=1)


# Instance partagée, mise en cache pour éviter de relire `.env` à chaque appel
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
