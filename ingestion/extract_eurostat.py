from __future__ import annotations

import itertools
import logging
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import Settings, get_settings
from ingestion.extract_odre import (
    RETRYABLE_STATUS_CODES,
    USER_AGENT,
    ExtractionError,
    RetryableStatusError,
)

logger = logging.getLogger(__name__)

DATASET = "nrg_pc_205"
DATA_PATH = f"/statistics/1.0/data/{DATASET}"

TRANCHES: tuple[str, ...] = (
    "MWH_LT20",
    "MWH20-499",
    "MWH500-1999",
    "MWH2000-19999",
    "MWH20000-69999",
    "MWH70000-149999",
    "MWH_GE150000",
)

# `X_TAX` : hors taxes ; `X_VAT` : hors TVA et taxes récupérables
FISCALITES: tuple[str, ...] = ("X_TAX", "X_VAT")

PREMIER_SEMESTRE = "2007-S1"

# Pays de l'UE-27 en codes Eurostat (Grèce : EL) ; l'historique récent suffit au repère
PAYS_UE: tuple[str, ...] = (
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU",
    "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
)  # fmt: skip
PREMIER_SEMESTRE_UE = "2024-S1"


class EurostatResponseError(ExtractionError):
    pass


class EurostatClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.calls = 0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.eurostat_base_url,
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._retrying = Retrying(
            stop=stop_after_attempt(self.settings.max_retries),
            wait=wait_exponential(
                multiplier=self.settings.retry_wait_seconds,
                max=max(self.settings.retry_wait_seconds * 30, 1),
            ),
            retry=retry_if_exception_type((httpx.TransportError, RetryableStatusError)),
            reraise=True,
        )

    def close(self) -> None:
        logger.info("eurostat_api_calls=%d", self.calls)
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EurostatClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch_prix_entreprises(self, *, geo: str = "FR") -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("format", "JSON"),
            ("lang", "fr"),
            ("geo", geo),
            ("currency", "EUR"),
            ("unit", "KWH"),
            ("sinceTimePeriod", PREMIER_SEMESTRE),
            *(("nrg_cons", tranche) for tranche in TRANCHES),
            *(("tax", fiscalite) for fiscalite in FISCALITES),
        ]
        return aplatir_json_stat(self._get(DATA_PATH, params).json())

    def fetch_prix_entreprises_ue(self) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("format", "JSON"),
            ("lang", "fr"),
            *(("geo", pays) for pays in PAYS_UE),
            ("currency", "EUR"),
            ("unit", "KWH"),
            ("sinceTimePeriod", PREMIER_SEMESTRE_UE),
            *(("nrg_cons", tranche) for tranche in TRANCHES),
            *(("tax", fiscalite) for fiscalite in FISCALITES),
        ]
        return aplatir_json_stat(self._get(DATA_PATH, params).json())

    def _get(self, path: str, params: list[tuple[str, str]]) -> httpx.Response:
        def envoyer() -> httpx.Response:
            self.calls += 1
            response = self._client.get(path, params=params)
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RetryableStatusError(response.status_code)
            response.raise_for_status()
            return response

        return self._retrying(envoyer)


def aplatir_json_stat(payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        dimensions: list[str] = payload["id"]
        tailles: list[int] = payload["size"]
        categories = {nom: payload["dimension"][nom]["category"] for nom in dimensions}
        valeurs: dict[str, float] = payload["value"]
    except (KeyError, TypeError) as error:
        raise EurostatResponseError(f"reponse JSON-stat inattendue : {error!r}") from error
    for attendue in ("nrg_cons", "tax", "time"):
        if attendue not in dimensions:
            raise EurostatResponseError(f"dimension {attendue!r} absente de la reponse")
    if isinstance(valeurs, list):
        valeurs = {str(i): v for i, v in enumerate(valeurs) if v is not None}
    statuts: dict[str, str] = payload.get("status") or {}

    # Codes de chaque dimension, rangés selon leur position dans le cube
    codes = {nom: sorted(cat["index"], key=cat["index"].get) for nom, cat in categories.items()}
    if [len(codes[d]) for d in dimensions] != tailles:
        raise EurostatResponseError("tailles et dimensions incoherentes")
    records = []
    for position, combinaison in enumerate(itertools.product(*(codes[d] for d in dimensions))):
        valeur = valeurs.get(str(position))
        if valeur is None:
            continue
        cellule = dict(zip(dimensions, combinaison, strict=True))
        records.append(
            {
                "pays": cellule.get("geo"),
                "semestre": cellule["time"],
                "tranche": cellule["nrg_cons"],
                "fiscalite": cellule["tax"],
                "libelle_tranche": categories["nrg_cons"]["label"].get(cellule["nrg_cons"]),
                "statut": statuts.get(str(position)),
                "prix_eur_kwh": valeur,
            }
        )
    logger.info("eurostat %s : %d valeurs publiees", DATASET, len(records))
    return records
