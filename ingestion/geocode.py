from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import Settings, get_settings
from ingestion.extract_odre import RETRYABLE_STATUS_CODES, USER_AGENT, RetryableStatusError

logger = logging.getLogger(__name__)

# En dessous, la BAN n'a pas trouvé grand-chose de ressemblant
SCORE_MINIMUM = 0.4

REGIONS: dict[str, str] = {
    "84": "Auvergne-Rhône-Alpes",
    "27": "Bourgogne-Franche-Comté",
    "53": "Bretagne",
    "24": "Centre-Val de Loire",
    "94": "Corse",
    "44": "Grand Est",
    "32": "Hauts-de-France",
    "11": "Île-de-France",
    "28": "Normandie",
    "75": "Nouvelle-Aquitaine",
    "76": "Occitanie",
    "52": "Pays de la Loire",
    "93": "Provence-Alpes-Côte d'Azur",
    "01": "Guadeloupe",
    "02": "Martinique",
    "03": "Guyane",
    "04": "La Réunion",
    "06": "Mayotte",
}


class GeocodingError(RuntimeError):
    pass


class AdresseIntrouvableError(GeocodingError):
    pass


def normalise(valeur: str) -> str:
    sans_accent = unicodedata.normalize("NFKD", valeur)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return "".join(c for c in sans_accent.lower() if c.isalnum())


_REGIONS_PAR_NOM: dict[str, str] = {normalise(nom): code for code, nom in REGIONS.items()}


def code_region(nom: str) -> str | None:
    return _REGIONS_PAR_NOM.get(normalise(nom))


@dataclass(frozen=True, slots=True)
class Territoire:
    adresse: str
    code_insee_commune: str
    commune: str
    code_postal: str | None
    code_departement: str
    departement: str
    code_insee_region: str | None
    region: str
    latitude: float
    longitude: float
    score: float
    # Précision du résultat BAN : `housenumber`, `street`, `locality`, `municipality`
    precision: str

    @property
    def est_precis(self) -> bool:
        return self.precision in {"housenumber", "street"}

    def __str__(self) -> str:
        return (
            f"{self.adresse} [commune {self.code_insee_commune}, "
            f"dept {self.code_departement}, region {self.code_insee_region or '?'}]"
        )


# Découpe le champ `context` de la BAN : « 69, Rhône, Auvergne-Rhône-Alpes »
def _contexte(valeur: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in valeur.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], parts[1]
    raise GeocodingError(f"champ context inattendu : {valeur!r}")


class BanClient:
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
            base_url=self.settings.ban_base_url,
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
        logger.info("ban_api_calls=%d", self.calls)
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BanClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def geocode(self, adresse: str) -> Territoire:
        if not adresse.strip():
            raise AdresseIntrouvableError("adresse vide")

        payload = self._get(
            "search",
            {"q": adresse, "index": "address", "limit": 1, "returntruegeometry": "false"},
        ).json()
        features = payload.get("features") or []
        if not features:
            raise AdresseIntrouvableError(f"aucun resultat pour {adresse!r}")

        return self._to_territoire(features[0], demande=adresse)

    def _to_territoire(self, feature: dict[str, Any], *, demande: str) -> Territoire:
        props: dict[str, Any] = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]

        citycode = props.get("citycode")
        if not citycode:
            raise GeocodingError(f"reponse BAN sans code commune pour {demande!r}")

        code_dept, departement, region = _contexte(str(props.get("context", "")))
        score = float(props.get("score") or 0.0)
        precision = str(props.get("type") or "")

        if score < SCORE_MINIMUM:
            logger.warning(
                "score de geocodage faible (%.2f) pour %r : resultat retenu %r",
                score,
                demande,
                props.get("label"),
            )
        if precision not in {"housenumber", "street"}:
            logger.warning(
                "adresse resolue a la maille %r seulement pour %r", precision or "inconnue", demande
            )

        insee_region = code_region(region)
        if insee_region is None:
            logger.warning("region %r non reconnue : code INSEE absent du diagnostic", region)

        return Territoire(
            adresse=str(props.get("label") or demande),
            code_insee_commune=str(citycode),
            commune=str(props.get("city") or ""),
            code_postal=str(props["postcode"]) if props.get("postcode") else None,
            code_departement=code_dept,
            departement=departement,
            code_insee_region=insee_region,
            region=region,
            latitude=float(coords[1]) if coords[1] is not None else 0.0,
            longitude=float(coords[0]) if coords[0] is not None else 0.0,
            score=score,
            precision=precision,
        )

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:

        def envoyer() -> httpx.Response:
            self.calls += 1
            response = self._client.get(path, params=params)
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RetryableStatusError(response.status_code)
            response.raise_for_status()
            return response

        return self._retrying(envoyer)


def geocode(adresse: str, *, settings: Settings | None = None) -> Territoire:
    with BanClient(settings) as client:
        return client.geocode(adresse)
