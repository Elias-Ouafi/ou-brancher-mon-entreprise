from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError, RetryableStatusError
from ingestion.extract_sismique import contient
from ingestion.zones_activite import KM_PAR_DEGRE, _distance_segment, _plan, _surface_anneau

logger = logging.getLogger(__name__)

COUCHE_BATIMENT = "BDTOPO_V3:batiment"
# Un point d'adresse tombe souvent dans la parcelle plutôt que sur le bâtiment
RAYON_BATIMENT_M = 60
# Pertes du système (câbles, onduleur, salissures) : la valeur par défaut de PVGIS
PERTES_PCT = 14


class SolaireError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class Batiment:
    usage: str | None
    nature: str | None
    emprise_m2: float
    hauteur_m: float | None
    # L'adresse est sur le bâtiment ; sinon c'est le plus proche, à `distance_m`
    contient_adresse: bool
    distance_m: float


@dataclass(frozen=True, slots=True)
class Productible:
    # kWh par kWc et par an, panneaux fixes à l'inclinaison et à l'orientation optimales
    kwh_par_kwc_an: float
    inclinaison_deg: float | None
    azimut_deg: float | None


def lire_batiments(
    payload: dict[str, Any], *, latitude: float, longitude: float
) -> Batiment | None:
    if payload.get("type") != "FeatureCollection":
        raise SolaireError("reponse WFS de la BD TOPO inattendue")
    meilleur = None
    for feature in payload.get("features") or []:
        geometrie = feature.get("geometry") or {}
        if geometrie.get("type") != "MultiPolygon":
            continue
        # Les bâtiments de la BD TOPO portent une altitude : on ne garde que x et y
        polygones = [[[c[:2] for c in a] for a in anneaux] for anneaux in geometrie["coordinates"]]
        distance, surface_km2 = math.inf, 0.0
        for anneaux in polygones:
            plans = [[_plan(x, y, longitude, latitude) for x, y, *_ in a] for a in anneaux]
            for anneau in plans:
                distance = min(distance, *(_distance_segment(a, b) for a, b in pairwise(anneau)))
            surface_km2 += _surface_anneau(plans[0]) - sum(_surface_anneau(a) for a in plans[1:])
        dedans = contient(polygones, longitude, latitude)
        proprietes = feature.get("properties") or {}
        batiment = Batiment(
            usage=proprietes.get("usage_1"),
            nature=proprietes.get("nature"),
            emprise_m2=surface_km2 * 1e6,
            hauteur_m=proprietes.get("hauteur"),
            contient_adresse=dedans,
            distance_m=0.0 if dedans else distance * 1000,
        )
        if meilleur is None or batiment.distance_m < meilleur.distance_m:
            meilleur = batiment
    return meilleur if meilleur and meilleur.distance_m <= RAYON_BATIMENT_M else None


def lire_productible(payload: dict[str, Any]) -> Productible:
    try:
        totaux = payload["outputs"]["totals"]["fixed"]
        montage = (payload.get("inputs") or {}).get("mounting_system", {}).get("fixed", {})
        return Productible(
            kwh_par_kwc_an=float(totaux["E_y"]),
            inclinaison_deg=(montage.get("slope") or {}).get("value"),
            azimut_deg=(montage.get("azimuth") or {}).get("value"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SolaireError(f"reponse PVGIS inattendue : {error!r}") from error


def lire_batiment(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> Batiment | None:
    reglages = settings or get_settings()
    demi_lat = RAYON_BATIMENT_M / 1000 / KM_PAR_DEGRE
    demi_lon = demi_lat / math.cos(math.radians(latitude))
    boite = (
        f"{longitude - demi_lon},{latitude - demi_lat},{longitude + demi_lon},{latitude + demi_lat}"
    )
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": COUCHE_BATIMENT,
        "outputFormat": "application/json",
        "count": 100,
        "propertyName": "usage_1,nature,hauteur,geometrie",
        "cql_filter": f"BBOX(geometrie,{boite},'EPSG:4326')",
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
        return lire_batiments(reponse.json(), latitude=latitude, longitude=longitude)
    except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
        raise SolaireError(f"WFS BD TOPO : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()


def lire_productible_pvgis(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> Productible:
    reglages = settings or get_settings()
    params = {
        "lat": latitude,
        "lon": longitude,
        "peakpower": 1,
        "loss": PERTES_PCT,
        "optimalangles": 1,
        "outputformat": "json",
    }
    proprietaire = client is None
    http = client or httpx.Client(
        timeout=reglages.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(reglages.pvgis_url, params=params))
        )
        return lire_productible(json.loads(reponse.content))
    except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
        raise SolaireError(f"PVGIS : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()
