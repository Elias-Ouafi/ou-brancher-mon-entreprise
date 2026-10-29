from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError
from ingestion.extract_sismique import contient

logger = logging.getLogger(__name__)

COUCHE = "BDTOPO_V3:zone_d_activite_ou_d_interet"
# Zones d'activité, industrielles et artisanales de la BD TOPO
NATURE = "Zone industrielle"
RAYON_KM = 5
NB_AFFICHEES = 3
KM_PAR_DEGRE = 111.32
# La BD TOPO plafonne la réponse ; au-delà le décompte serait un minorant
TAILLE_MAX = 500


class ZonesActiviteError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class ZoneActivite:
    nom: str
    distance_km: float
    surface_ha: float
    # L'adresse est dans la zone
    contient_adresse: bool


# Plan local en km autour de l'adresse : assez juste à l'échelle de quelques km
def _plan(x: float, y: float, longitude: float, latitude: float) -> tuple[float, float]:
    return (
        (x - longitude) * KM_PAR_DEGRE * math.cos(math.radians(latitude)),
        (y - latitude) * KM_PAR_DEGRE,
    )


def _distance_segment(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    longueur = dx * dx + dy * dy
    t = 0.0 if longueur == 0 else max(0.0, min(1.0, -(a[0] * dx + a[1] * dy) / longueur))
    return math.hypot(a[0] + t * dx, a[1] + t * dy)


def _surface_anneau(anneau: list[tuple[float, float]]) -> float:
    paires = zip(anneau, anneau[1:] + anneau[:1], strict=True)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in paires)) / 2


def lire_zones(payload: dict[str, Any], *, latitude: float, longitude: float) -> list[ZoneActivite]:
    if payload.get("type") != "FeatureCollection":
        raise ZonesActiviteError("reponse WFS de la BD TOPO inattendue")
    zones = []
    for feature in payload.get("features") or []:
        geometrie = feature.get("geometry") or {}
        if geometrie.get("type") != "MultiPolygon":
            continue
        polygones = geometrie["coordinates"]
        distance, surface_km2 = math.inf, 0.0
        for anneaux in polygones:
            plans = [[_plan(x, y, longitude, latitude) for x, y, *_ in a] for a in anneaux]
            for anneau in plans:
                distance = min(
                    distance,
                    *(_distance_segment(a, b) for a, b in pairwise(anneau)),
                )
            surface_km2 += _surface_anneau(plans[0]) - sum(_surface_anneau(a) for a in plans[1:])
        dedans = contient(polygones, longitude, latitude)
        zones.append(
            ZoneActivite(
                nom=(feature.get("properties") or {}).get("toponyme") or "zone sans nom",
                distance_km=0.0 if dedans else distance,
                surface_ha=surface_km2 * 100,
                contient_adresse=dedans,
            )
        )
    return sorted(zones, key=lambda z: z.distance_km)


def lire_zones_activite(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> tuple[int, list[ZoneActivite]]:
    reglages = settings or get_settings()
    demi_lat = RAYON_KM / KM_PAR_DEGRE
    demi_lon = demi_lat / math.cos(math.radians(latitude))
    boite = (
        f"{longitude - demi_lon},{latitude - demi_lat},{longitude + demi_lon},{latitude + demi_lat}"
    )
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": COUCHE,
        "outputFormat": "application/json",
        "count": TAILLE_MAX,
        "propertyName": "toponyme,geometrie",
        "cql_filter": f"nature='{NATURE}' AND BBOX(geometrie,{boite},'EPSG:4326')",
    }
    proprietaire = client is None
    http = client or httpx.Client(
        timeout=reglages.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(reglages.geoplateforme_wfs_url, params=params))
        )
        zones = [
            z
            for z in lire_zones(reponse.json(), latitude=latitude, longitude=longitude)
            if z.distance_km <= RAYON_KM
        ]
    except (httpx.HTTPError, ValueError) as error:
        raise ZonesActiviteError(f"WFS BD TOPO : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()
    return len(zones), zones[:NB_AFFICHEES]
