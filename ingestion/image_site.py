from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError, RetryableStatusError
from ingestion.zones_activite import KM_PAR_DEGRE

logger = logging.getLogger(__name__)

COUCHE_GRAPHE = "ORTHOIMAGERY.ORTHOPHOTOS.GRAPHE-MOSAIQUAGE:graphe_bdortho"
COUCHE_IMAGE = "HR.ORTHOIMAGERY.ORTHOPHOTOS"
COTE_M = 400
PIXELS = 800
# Le point d'adresse sert de boîte de requête : le graphe ne répond que par ses polygones
DEMI_POINT_DEG = 1e-5


class ImageSiteError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class ImageSite:
    # Prise de vue de la mosaïque la plus récente à l'adresse (plusieurs vols d'un même chantier
    # se suivent parfois de quelques jours)
    debut: date
    fin: date
    cote_m: int
    url: str


def lire_dates(payload: dict[str, Any]) -> tuple[date, date]:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ImageSiteError("reponse WFS du graphe de mosaiquage inattendue")
    vols = []
    for feature in payload.get("features") or []:
        proprietes = feature.get("properties") or {}
        try:
            vols.append((int(proprietes["pva"]), date.fromisoformat(proprietes["date_vol"][:10])))
        except (KeyError, TypeError, ValueError):
            continue
    if not vols:
        raise ImageSiteError("aucune date de prise de vue a cette adresse")
    # Plusieurs campagnes se recouvrent : l'image affichée est celle de la plus récente
    recente = max(annee for annee, _ in vols)
    jours = [jour for annee, jour in vols if annee == recente]
    return min(jours), max(jours)


def url_image(latitude: float, longitude: float, settings: Settings) -> str:
    demi_lat = COTE_M / 2000 / KM_PAR_DEGRE
    demi_lon = demi_lat / math.cos(math.radians(latitude))
    boite = (
        f"{latitude - demi_lat:.6f},{longitude - demi_lon:.6f},"
        f"{latitude + demi_lat:.6f},{longitude + demi_lon:.6f}"
    )
    return str(
        httpx.URL(
            settings.geoplateforme_wms_url,
            params={
                "SERVICE": "WMS",
                "VERSION": "1.3.0",
                "REQUEST": "GetMap",
                "LAYERS": COUCHE_IMAGE,
                "STYLES": "",
                "CRS": "EPSG:4326",
                "BBOX": boite,
                "WIDTH": PIXELS,
                "HEIGHT": PIXELS,
                "FORMAT": "image/jpeg",
            },
        )
    )


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def lire_image_site(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> ImageSite:
    reglages = settings or get_settings()
    boite = (
        f"{latitude - DEMI_POINT_DEG:.5f},{longitude - DEMI_POINT_DEG:.5f},"
        f"{latitude + DEMI_POINT_DEG:.5f},{longitude + DEMI_POINT_DEG:.5f},"
        "urn:ogc:def:crs:EPSG::4326"
    )
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": COUCHE_GRAPHE,
        "OUTPUTFORMAT": "application/json",
        "BBOX": boite,
    }
    proprietaire = client is None
    http = client or _client(reglages)
    try:
        reponse = _retrying(reglages)(
            lambda: _verifier(http.get(reglages.geoplateforme_wfs_url, params=params))
        )
        debut, fin = lire_dates(reponse.json())
    except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
        raise ImageSiteError(f"graphe de mosaiquage : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()
    return ImageSite(debut, fin, COTE_M, url_image(latitude, longitude, reglages))


def telecharger_image(
    image: ImageSite, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> bytes:
    reglages = settings or get_settings()
    proprietaire = client is None
    http = client or _client(reglages)
    try:
        reponse = _retrying(reglages)(lambda: _verifier(http.get(image.url)))
    except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
        raise ImageSiteError(f"image BD ORTHO : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()
    # Une erreur WMS revient en XML avec un code 200 : seul un JPEG est une image
    if not reponse.content.startswith(b"\xff\xd8"):
        raise ImageSiteError("image BD ORTHO : la reponse n'est pas un JPEG")
    return reponse.content
