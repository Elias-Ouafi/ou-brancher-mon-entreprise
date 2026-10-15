"""Client de l'API de diffusion d'Eurostat : prix de l'électricité des entreprises.

Jeu `nrg_pc_205` : prix moyen semestriel payé par les consommateurs non
résidentiels, par tranche de consommation annuelle (IA à IG). C'est la seule
série publique, longue et officielle de prix **effectivement payés** par les
entreprises françaises : au-delà de 36 kVA, il n'existe plus de tarif réglementé
et les contrats de fourniture sont confidentiels.

Points de conception :

* **Un seul appel**, sans compte ni clé : toutes les tranches, les deux niveaux
  de fiscalité et tous les semestres depuis 2007 (la nomenclature IA–IG date de
  2007 ; les tranches antérieures ne sont pas comparables).
* **JSON-stat aplati.** La réponse est un cube : un tableau de valeurs indexé par
  la position dans le produit des dimensions. Chaque cellule renseignée devient
  un enregistrement bronze `(semestre, tranche, fiscalite)`.
* **Même robustesse que les clients ODRÉ, RTE et BAN.** Timeout explicite, retry
  exponentiel sur erreurs réseau et statuts transitoires, jamais sur une 4xx.

Documentation : https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access
"""

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

#: Tranches de consommation annuelle de la nomenclature 2007 (IA à IG).
TRANCHES: tuple[str, ...] = (
    "MWH_LT20",
    "MWH20-499",
    "MWH500-1999",
    "MWH2000-19999",
    "MWH20000-69999",
    "MWH70000-149999",
    "MWH_GE150000",
)

#: `X_TAX` : hors taxes ; `X_VAT` : hors TVA et taxes récupérables.
FISCALITES: tuple[str, ...] = ("X_TAX", "X_VAT")

#: Premier semestre de la nomenclature IA–IG.
PREMIER_SEMESTRE = "2007-S1"


class EurostatResponseError(ExtractionError):
    """La réponse d'Eurostat n'a pas la forme JSON-stat attendue."""


class EurostatClient:
    """Client HTTP de l'API de diffusion d'Eurostat, utilisable comme context manager."""

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

    # -- Cycle de vie -------------------------------------------------------

    def close(self) -> None:
        logger.info("eurostat_api_calls=%d", self.calls)
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EurostatClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- Extraction ---------------------------------------------------------

    def fetch_prix_entreprises(self, *, geo: str = "FR") -> list[dict[str, Any]]:
        """Prix semestriels de toutes les tranches, sous forme d'enregistrements bronze."""
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

    # -- Couche HTTP --------------------------------------------------------

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
    """Une ligne par cellule renseignée du cube `nrg_pc_205`.

    Dans JSON-stat, la valeur de la combinaison `(i_1, …, i_n)` est rangée à la
    position `((i_1 * s_2 + i_2) * s_3 + …)`, où `s_k` est la taille de la
    dimension `k`. `value` (et `status`) sont des dictionnaires creux : une
    cellule absente n'est pas publiée.
    """
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
    if isinstance(valeurs, list):  # JSON-stat autorise aussi un tableau dense
        valeurs = {str(i): v for i, v in enumerate(valeurs) if v is not None}
    statuts: dict[str, str] = payload.get("status") or {}

    # Codes de chaque dimension, rangés selon leur position dans le cube.
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
