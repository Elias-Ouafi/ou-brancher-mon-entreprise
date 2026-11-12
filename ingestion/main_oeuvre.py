from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError, RetryableStatusError

logger = logging.getLogger(__name__)

# Hypothèse de l'outil, aucun texte français ne fixe ce temps : pratique des profils
# d'attractivité britanniques (décompte des diplômés à 45 min de route)
MINUTES_TRAJET = 45


class MainOeuvreError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class MainOeuvre:
    minutes: int
    nb_communes: int
    nb_superieur: float
    nb_cap_bep_bac: float
    nb_15_ans_plus: float
    millesime: str


@dataclass(frozen=True, slots=True)
class Recrutements:
    code_departement: str
    annee: str
    projets: float
    projets_difficiles: float
    part_difficiles: float | None
    nb_lignes_secretes: float
    nb_lignes: float


# Polygones de l'isochrone au format de `extract_sismique.contient` : [polygone][anneau][point]
def lire_isochrone(payload: Any) -> list[list[list[list[float]]]]:
    geometrie = payload.get("geometry") if isinstance(payload, dict) else None
    if not isinstance(geometrie, dict):
        raise MainOeuvreError("isochrone : geometrie absente de la reponse")
    if geometrie.get("type") == "Polygon":
        polygones = [geometrie["coordinates"]]
    elif geometrie.get("type") == "MultiPolygon":
        polygones = geometrie["coordinates"]
    else:
        raise MainOeuvreError(f"isochrone : geometrie inattendue {geometrie.get('type')!r}")
    zone = [[[[x, y] for x, y, *_ in anneau] for anneau in poly] for poly in polygones]
    if not any(anneau for poly in zone for anneau in poly):
        raise MainOeuvreError("isochrone : polygone vide")
    return zone


# Zone atteinte en voiture en `MINUTES_TRAJET` à l'arrivée sur l'adresse : le trajet du salarié
def lire_zone_trajet(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[list[list[list[float]]]]:
    reglages = settings or get_settings()
    http = client or httpx.Client(
        timeout=reglages.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    params = {
        "resource": reglages.isochrone_ressource,
        "point": f"{longitude},{latitude}",
        "costType": "time",
        "costValue": MINUTES_TRAJET * 60,
        "timeUnit": "second",
        "profile": "car",
        "direction": "arrival",
        "geometryFormat": "geojson",
    }
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(reglages.isochrone_url, params=params))
        )
        return lire_isochrone(reponse.json())
    except (httpx.HTTPError, RetryableStatusError, ValueError, KeyError) as error:
        raise MainOeuvreError(
            f"isochrone Geoplateforme : {type(error).__name__} {error}"
        ) from error
    finally:
        if client is None:
            http.close()
