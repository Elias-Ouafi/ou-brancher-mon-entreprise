from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError, RetryableStatusError

logger = logging.getLogger(__name__)

COUCHE_TRONCONS = "BDTOPO_V3:troncon_de_route"
RAYON_AUTOROUTE_KM = 10
KM_PAR_DEGRE = 111.32
TAILLE_MAX = 500


class AccesRoutierError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class Acces:
    # `autoroute` (à vol d'oiseau seulement), `port`, `aeroport` ou `rail`
    type_acces: str
    nom: str
    distance_vol_km: float
    # Itinéraire voiture ; `None` si le calcul n'a pas abouti ou n'a pas de sens
    distance_route_km: float | None
    duree_min: float | None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return (
        2
        * 6371
        * math.asin(
            math.sqrt(
                math.sin(math.radians(lat2 - lat1) / 2) ** 2
                + math.cos(math.radians(lat1))
                * math.cos(math.radians(lat2))
                * math.sin(math.radians(lon2 - lon1) / 2) ** 2
            )
        )
    )


# Tronçon de type autoroutier le plus proche : nom de la route et distance à vol d'oiseau
def lire_autoroute(
    payload: dict[str, Any], *, latitude: float, longitude: float
) -> tuple[str, float] | None:
    if payload.get("type") != "FeatureCollection":
        raise AccesRoutierError("reponse WFS de la BD TOPO inattendue")
    meilleur = None
    for feature in payload.get("features") or []:
        geometrie = feature.get("geometry") or {}
        if geometrie.get("type") == "MultiLineString":
            lignes = geometrie["coordinates"]
        elif geometrie.get("type") == "LineString":
            lignes = [geometrie["coordinates"]]
        else:
            continue
        nom = (feature.get("properties") or {}).get("cpx_numero") or "autoroute"
        for ligne in lignes:
            for x, y, *_ in ligne:
                distance = _distance_km(latitude, longitude, y, x)
                if meilleur is None or distance < meilleur[1]:
                    meilleur = (nom, distance)
    return meilleur


def chercher_autoroute(
    http: httpx.Client, reglages: Settings, *, latitude: float, longitude: float
) -> tuple[str, float] | None:
    demi_lat = RAYON_AUTOROUTE_KM / KM_PAR_DEGRE
    demi_lon = demi_lat / math.cos(math.radians(latitude))
    boite = (
        f"{longitude - demi_lon},{latitude - demi_lat},{longitude + demi_lon},{latitude + demi_lat}"
    )
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": COUCHE_TRONCONS,
        "outputFormat": "application/json",
        "count": TAILLE_MAX,
        "propertyName": "cpx_numero,geometrie",
        "cql_filter": (
            "nature='Type autoroutier' AND cpx_classement_administratif LIKE 'Autoroute%'"
            f" AND BBOX(geometrie,{boite},'EPSG:4326')"
        ),
    }
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(reglages.geoplateforme_wfs_url, params=params))
        )
        trouve = lire_autoroute(reponse.json(), latitude=latitude, longitude=longitude)
    except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
        raise AccesRoutierError(f"WFS BD TOPO : {type(error).__name__} {error}") from error
    return trouve if trouve and trouve[1] <= RAYON_AUTOROUTE_KM else None


# (kilomètres, minutes) en voiture, ou `None` : pas de profil poids lourd à la Géoplateforme
def itineraire(
    http: httpx.Client,
    reglages: Settings,
    origine: tuple[float, float],
    destination: tuple[float, float],
) -> tuple[float, float] | None:
    params = {
        "resource": reglages.itineraire_ressource,
        "start": f"{origine[1]},{origine[0]}",
        "end": f"{destination[1]},{destination[0]}",
        "profile": "car",
        "optimization": "fastest",
        "getSteps": "false",
        "distanceUnit": "kilometer",
        "timeUnit": "minute",
    }
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(reglages.itineraire_url, params=params))
        )
        corps = reponse.json()
        return float(corps["distance"]), float(corps["duration"])
    except (httpx.HTTPError, RetryableStatusError, ValueError, KeyError, TypeError) as error:
        logger.warning("itineraire non calcule : %s", error)
        return None


# Autoroute la plus proche (à vol d'oiseau), puis temps en voiture vers les pôles de fret donnés
# sous forme (type, nom, latitude, longitude)
def lire_acces(
    *,
    latitude: float,
    longitude: float,
    poles: list[tuple[str, str, float, float]],
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[Acces]:
    reglages = settings or get_settings()
    proprietaire = client is None
    http = client or httpx.Client(
        timeout=reglages.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    acces = []
    try:
        if autoroute := chercher_autoroute(http, reglages, latitude=latitude, longitude=longitude):
            acces.append(Acces("autoroute", autoroute[0], autoroute[1], None, None))
        for type_acces, nom, lat, lon in poles:
            trajet = itineraire(http, reglages, (latitude, longitude), (lat, lon))
            acces.append(
                Acces(
                    type_acces=type_acces,
                    nom=nom,
                    distance_vol_km=_distance_km(latitude, longitude, lat, lon),
                    distance_route_km=None if trajet is None else trajet[0],
                    duree_min=None if trajet is None else trajet[1],
                )
            )
    finally:
        if proprietaire:
            http.close()
    return acces
