from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import Settings, get_settings
from ingestion.extract_odre import RETRYABLE_STATUS_CODES, USER_AGENT, RetryableStatusError
from ingestion.hexagones import (
    arrondir,
    construire_grilles,
    lire_contours,
    lire_terres,
    points_terrestres,
)

logger = logging.getLogger(__name__)

CONTOURS_JEU = "contours_departements"
TERRES_JEU = "terres_emergees"
RELIEF_JEU = "relief_ign"

ELEVATION_PATH = "/1.0/calcul/alti/rest/elevation.json"
# MNT mondial de l'IGN : RGE ALTI sur la France, outre-mer compris
RESSOURCE_ALTI = "ign_rge_alti_wld"
# Plafond du service par requête
POINTS_PAR_REQUETE = 5000
# Valeur renvoyée hors couverture (en mer)
SANS_DONNEE = -99999.0

SCHEMA_RELIEF = pa.schema(
    [
        ("lon", pa.float64()),
        ("lat", pa.float64()),
        ("altitude_m", pa.float64()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def _retrying(settings: Settings) -> Retrying:
    return Retrying(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(
            multiplier=settings.retry_wait_seconds, max=max(settings.retry_wait_seconds * 30, 1)
        ),
        retry=retry_if_exception_type((httpx.TransportError, RetryableStatusError)),
        reraise=True,
    )


def _verifier(response: httpx.Response) -> httpx.Response:
    if response.status_code in RETRYABLE_STATUS_CODES:
        raise RetryableStatusError(response.status_code)
    response.raise_for_status()
    return response


# Télécharge un fichier GeoJSON dans le bronze, tel que publié
def telecharger_geojson(
    jeu: str,
    url: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> Path:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        response = _retrying(settings)(lambda: _verifier(http.get(url)))
    finally:
        if client is None:
            http.close()
    partition = (
        Path(settings.bronze_dir)
        / jeu
        / f"ingest_date={(today or datetime.now(UTC).date()).isoformat()}"
    )
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / url.rsplit("/", 1)[-1]
    path.write_bytes(response.content)
    logger.info("%s ecrit octets=%d path=%s", jeu, len(response.content), path)
    return path


# Fichier le plus récent d'un jeu du bronze, `None` s'il n'a jamais été téléchargé
def dernier_geojson(jeu: str, settings: Settings | None = None) -> Path | None:
    settings = settings or get_settings()
    fichiers = sorted((Path(settings.bronze_dir) / jeu).glob("ingest_date=*/*.geojson*"))
    return fichiers[-1] if fichiers else None


# Client du service d'altimétrie, utilisable comme context manager
class AltimetrieClient:
    def __init__(self, settings: Settings | None = None, *, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.calls = 0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.altimetrie_base_url,
            timeout=max(self.settings.http_timeout_seconds, 60.0),
            headers={"User-Agent": USER_AGENT},
        )
        self._retrying = _retrying(self.settings)

    def close(self) -> None:
        logger.info("altimetrie_api_calls=%d", self.calls)
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AltimetrieClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # Altitude de chaque point (lon, lat) en mètres, `None` hors couverture
    def altitudes(self, points: list[tuple[float, float]]) -> list[float | None]:
        resultat: list[float | None] = []
        for debut in range(0, len(points), POINTS_PAR_REQUETE):
            lot = points[debut : debut + POINTS_PAR_REQUETE]
            corps = {
                "lon": "|".join(f"{lon:.5f}" for lon, _ in lot),
                "lat": "|".join(f"{lat:.5f}" for _, lat in lot),
                "resource": RESSOURCE_ALTI,
                "zonly": "true",
            }

            def envoyer(corps: dict[str, str] = corps) -> httpx.Response:
                self.calls += 1
                return _verifier(self._client.post(ELEVATION_PATH, json=corps))

            altitudes = self._retrying(envoyer).json()["elevations"]
            if len(altitudes) != len(lot):
                raise ValueError(f"{len(altitudes)} altitudes recues pour {len(lot)} points")
            resultat.extend(None if a <= SANS_DONNEE else float(a) for a in altitudes)
        return resultat


# Altitudes déjà en bronze, toutes exécutions confondues
def lire_altitudes(settings: Settings | None = None) -> dict[tuple[float, float], float | None]:
    settings = settings or get_settings()
    altitudes: dict[tuple[float, float], float | None] = {}
    for path in sorted((Path(settings.bronze_dir) / RELIEF_JEU).glob("ingest_date=*/*.parquet")):
        table = pq.read_table(path, columns=["lon", "lat", "altitude_m"]).to_pydict()
        for lon, lat, altitude in zip(table["lon"], table["lat"], table["altitude_m"], strict=True):
            altitudes[arrondir(lon, lat)] = altitude
    return altitudes


# Complète le cache des altitudes : seuls les points absents du bronze sont demandés
def completer_altitudes(
    points: Iterable[tuple[float, float]],
    *,
    settings: Settings | None = None,
    client: AltimetrieClient | None = None,
    run_id: str = "manual",
) -> dict[tuple[float, float], float | None]:
    settings = settings or get_settings()
    altitudes = lire_altitudes(settings)
    manquants = sorted({arrondir(*p) for p in points} - altitudes.keys())
    if not manquants:
        return altitudes
    altimetrie = client or AltimetrieClient(settings)
    try:
        nouvelles = altimetrie.altitudes(manquants)
    finally:
        if client is None:
            altimetrie.close()
    maintenant = datetime.now(UTC)
    table = pa.table(
        {
            "lon": [lon for lon, _ in manquants],
            "lat": [lat for _, lat in manquants],
            "altitude_m": nouvelles,
            "ingested_at": [maintenant] * len(manquants),
        },
        schema=SCHEMA_RELIEF,
    )
    partition = Path(settings.bronze_dir) / RELIEF_JEU / f"ingest_date={maintenant.date()}"
    partition.mkdir(parents=True, exist_ok=True)
    # Horodatage dans le nom : deux exécutions du même jour ne s'écrasent pas
    path = partition / f"part-{run_id}-{maintenant:%H%M%S%f}.parquet"
    pq.write_table(table, path, compression="zstd")
    logger.info("altitudes ecrites points=%d path=%s", len(manquants), path)
    altitudes.update(zip(manquants, nouvelles, strict=True))
    return altitudes


# Étape du refresh : contours et terres à jour, puis relief de toutes les cases de la carte
def run_geo_ingestion(*, settings: Settings | None = None, run_id: str = "manual") -> int:
    settings = settings or get_settings()
    contours = telecharger_geojson(CONTOURS_JEU, settings.contours_url, settings=settings)
    terres = telecharger_geojson(TERRES_JEU, settings.terres_url, settings=settings)
    grilles = construire_grilles(lire_contours(contours), terres=lire_terres(terres))
    points = points_terrestres(grilles)
    completer_altitudes(points, settings=settings, run_id=run_id)
    return len(points)
