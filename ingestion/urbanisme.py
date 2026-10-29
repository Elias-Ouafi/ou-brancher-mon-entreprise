from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

ZONE_URBA_PATH = "gpu/zone-urba"

# Types de zone du standard CNIG des documents d'urbanisme
TYPES_ZONE = {
    "U": "zone urbaine",
    "AU": "zone à urbaniser",
    "A": "zone agricole",
    "N": "zone naturelle et forestière",
}


class UrbanismeError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class ZoneUrbanisme:
    type_zone: str
    libelle: str
    libelle_long: str | None
    # Identifiant du document (PLU, PLUi, carte communale) au Géoportail de l'urbanisme
    document: str | None
    date_validite: str | None
    url_reglement: str | None

    @property
    def nom_type(self) -> str:
        return TYPES_ZONE.get(self.type_zone, f"zone de type {self.type_zone}")

    # Agricole ou naturelle : hors STECAL, l'activité industrielle ou logistique n'y est pas permise
    @property
    def est_inconstructible_activite(self) -> bool:
        return self.type_zone in {"A", "N"}


def _date(valeur: str | None) -> str | None:
    if valeur and len(valeur) == 8 and valeur.isdigit():
        return f"{valeur[6:]}/{valeur[4:6]}/{valeur[:4]}"
    return valeur or None


# Zone du point : `None` si aucun document d'urbanisme numérisé ne le couvre
def lire_zone(payload: dict[str, Any]) -> ZoneUrbanisme | None:
    if payload.get("type") != "FeatureCollection":
        raise UrbanismeError("reponse du Geoportail de l'urbanisme inattendue")
    zones = [f.get("properties") or {} for f in payload.get("features") or []]
    if not zones:
        return None
    # Zones superposées : la plus contraignante pour l'activité passe devant
    propres = sorted(zones, key=lambda z: z.get("typezone") not in {"A", "N"})
    p = propres[0]
    if not p.get("typezone"):
        raise UrbanismeError("zone sans type dans le Geoportail de l'urbanisme")
    return ZoneUrbanisme(
        type_zone=str(p["typezone"]).upper(),
        libelle=str(p.get("libelle") or ""),
        libelle_long=p.get("libelong") or None,
        document=p.get("idurba") or None,
        date_validite=_date(p.get("datvalid")),
        url_reglement=p.get("urlfic") or None,
    )


def lire_zone_urbanisme(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> ZoneUrbanisme | None:
    reglages = settings or get_settings()
    geometrie = json.dumps({"type": "Point", "coordinates": [longitude, latitude]})
    proprietaire = client is None
    http = client or httpx.Client(
        base_url=reglages.apicarto_base_url,
        timeout=reglages.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(ZONE_URBA_PATH, params={"geom": geometrie}))
        )
        return lire_zone(reponse.json())
    except (httpx.HTTPError, ValueError) as error:
        raise UrbanismeError(f"{ZONE_URBA_PATH} : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()
