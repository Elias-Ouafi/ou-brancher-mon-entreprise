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

    rte_api_base_url: str = "https://digital.iservices.rte-france.com"

    ban_base_url: str = "https://data.geopf.fr/geocodage"

    georisques_base_url: str = "https://www.georisques.gouv.fr/api/v1/"

    # Export GASPAR servi hors de l'API Géorisques : repli quand elle se tait
    gaspar_url: str = "https://files.georisques.fr/GASPAR/gaspar.zip"

    apicarto_base_url: str = "https://apicarto.ign.fr/api/"

    geoplateforme_wfs_url: str = "https://data.geopf.fr/wfs/ows"

    # Profil voiture seulement : la Géoplateforme n'a pas de profil poids lourd
    itineraire_url: str = "https://data.geopf.fr/navigation/itineraire"
    itineraire_ressource: str = "bdtopo-pgr"

    pvgis_url: str = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"

    vigieau_base_url: str = "https://api.vigieau.beta.gouv.fr/api/"

    zonage_sismique_url: str = "https://www.georisques.gouv.fr/services"

    zones_repartition_eaux_url: str = "https://services.sandre.eaufrance.fr/geo/zrpe"

    ember_url: str = (
        "https://files.ember-energy.org/public-downloads/generation/outputs/"
        "release_generation_yearly_lower.csv"
    )

    # Nominatim (ODbL) : une requête par appel, règle d'usage du service
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"

    edf_sei_url: str = (
        "https://opendata.edf.fr/data-fair/api/v1/datasets/"
        "courbe-de-charge-de-la-production-delectricite-par-filiere/raw"
    )

    enedis_base_url: str = "https://opendata.enedis.fr/data-fair/api/v1/datasets/"

    eurostat_base_url: str = "https://ec.europa.eu/eurostat/api/dissemination"

    contours_url: str = (
        "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/"
        "departements-1000m.geojson.gz"
    )

    terres_url: str = (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
        "ne_50m_land.geojson"
    )

    pays_url: str = (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
        "ne_50m_admin_0_countries.geojson"
    )

    geo_communes_url: str = "https://geo.api.gouv.fr/communes"

    ports_fret_url: str = (
        "https://data.statistiques.developpement-durable.gouv.fr/dido/api/v1/datafiles/"
        "89c4e831-27ae-4fe7-8ff7-d6c82fa4a841/csv"
    )

    # L'archive DGAC change d'adresse à chaque publication : on passe par sa fiche data.gouv.fr
    aeroports_fret_dataset_url: str = (
        "https://www.data.gouv.fr/api/1/datasets/64808a5811da153f1439914c/"
    )

    chantiers_rail_route_url: str = (
        "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/"
        "chantiers-de-transport-combines/exports/csv?use_labels=true"
    )

    dvf_url: str = "https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/full.csv.gz"
    dvf_nb_annees: int = 5

    # Lien permanent data.gouv.fr : il suit la dernière version publiée de Cartofriches
    friches_url: str = "https://www.data.gouv.fr/fr/datasets/r/74feb3ed-5f9f-4ef8-8fab-b0128d569a99"

    # Fiche du jeu, pas une ressource : l'archive change de nom à chaque publication mensuelle
    reseaux_chaleur_jeu_url: str = (
        "https://www.data.gouv.fr/api/1/datasets/64f05d3568e4d575eb454ffe/"
    )

    altimetrie_base_url: str = "https://data.geopf.fr/altimetrie"

    # Base Diplômes-Formation du recensement (Insee) et enquête BMO (France Travail) : le
    # millésime est dans l'adresse, à changer à chaque publication
    diplomes_url: str = (
        "https://www.insee.fr/fr/statistiques/fichier/9006527/DS_RP_DIPLOMES_PRINC_2023_CSV_FR.zip"
    )
    bmo_url: str = (
        "https://www.francetravail.org/files/live/sites/peorg/files/documents/"
        "Statistiques-et-analyses/Open-data/BMO/Base-open-data-BMO-2026.xlsx"
    )
    isochrone_url: str = "https://data.geopf.fr/navigation/isochrone"
    isochrone_ressource: str = "bdtopo-valhalla"

    rte_client_id: str | None = None
    rte_client_secret: SecretStr | None = None
    ecowatt_client_id: str | None = None
    ecowatt_client_secret: SecretStr | None = None

    lookback_hours: int = Field(default=3, ge=1)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=4, ge=1)

    retry_wait_seconds: float = Field(default=1.0, ge=0)

    records_row_threshold: int = Field(default=900, ge=1)

    # Bronze : un Parquet immuable par exécution, fidèle à la source
    bronze_dir: Path = Path("data/bronze")
    duckdb_path: Path = Path("data/warehouse/eco2mix.duckdb")

    reports_dir: Path = Path("data/reports")

    warehouse_backend: Literal["duckdb"] = "duckdb"

    consolidation_lookback_months: int = Field(default=24, ge=1)

    freshness_max_lag_hours: int = Field(default=2, ge=1)

    consolidation_min_coverage: float = Field(default=0.95, gt=0, le=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
