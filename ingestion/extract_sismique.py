from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

# Zonage réglementaire (décret n° 2010-1255) publié par Géorisques : un polygone par commune
COUCHE = "ms:risq_zonage_sismique"
TAILLE_PAGE = 5_000
_NOMBRE = re.compile(r'numberMatched="(\d+)"')
# Cinq décimales, environ un mètre : assez pour situer une adresse, deux fois moins lourd
DECIMALES = 5
_ZONE = re.compile(r"^([1-5]) - (.+)$")


class ZonageSismiqueError(ExtractionError):
    pass


# Polygones d'une géométrie GeoJSON, chacun une liste d'anneaux (extérieur puis trous)
def _polygones(geometrie: dict[str, Any]) -> list[list[list[list[float]]]]:
    if geometrie.get("type") == "Polygon":
        polygones = [geometrie["coordinates"]]
    elif geometrie.get("type") == "MultiPolygon":
        polygones = geometrie["coordinates"]
    else:
        raise ZonageSismiqueError(f"geometrie inattendue : {geometrie.get('type')!r}")
    return [
        [[[round(x, DECIMALES), round(y, DECIMALES)] for x, y, *_ in anneau] for anneau in poly]
        for poly in polygones
    ]


# Une page WFS en lignes du bronze : zone, libellé, polygones et leur emprise
def lire_page(payload: dict[str, Any]) -> list[dict[str, Any]]:
    lignes = []
    for feature in payload.get("features") or []:
        proprietes = feature.get("properties") or {}
        libelle = str(proprietes.get("zone_sismicite") or "").strip()
        if not (zone := _ZONE.match(libelle)):
            raise ZonageSismiqueError(f"zone de sismicite illisible : {libelle!r}")
        polygones = _polygones(feature.get("geometry") or {})
        points = [p for poly in polygones for anneau in poly for p in anneau]
        lignes.append(
            {
                "gid": str(proprietes["gid"]),
                "zone_sismicite": zone.group(1),
                "libelle_zone": libelle,
                "geometrie": json.dumps(polygones, separators=(",", ":")),
                "lon_min": min(x for x, _ in points),
                "lat_min": min(y for _, y in points),
                "lon_max": max(x for x, _ in points),
                "lat_max": max(y for _, y in points),
            }
        )
    return lignes


# Toute la couche, page par page : la zone réglementaire de chaque point du territoire
def telecharger_zonage(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=max(settings.http_timeout_seconds, 120),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": COUCHE}

    def lire(parametres: dict[str, Any]) -> httpx.Response:
        return _retrying(settings)(
            lambda: _verifier(http.get(settings.zonage_sismique_url, params=parametres))
        )

    lignes: list[dict[str, Any]] = []
    try:
        if not (nombre := _NOMBRE.search(lire({**base, "resultType": "hits"}).text)):
            raise ZonageSismiqueError("nombre de polygones non annonce par le service WFS")
        attendu = int(nombre.group(1))
        while len(lignes) < attendu:
            # Sans tri explicite, l'ordre change d'une page à l'autre : polygones perdus
            page = lire_page(
                lire(
                    {
                        **base,
                        "outputFormat": "geojson",
                        "sortBy": "gid",
                        "count": TAILLE_PAGE,
                        "startIndex": len(lignes),
                    }
                ).json()
            )
            if not page:
                break
            lignes += page
    finally:
        if client is None:
            http.close()
    if (distincts := len({ligne["gid"] for ligne in lignes})) != attendu:
        raise ZonageSismiqueError(f"zonage sismique incomplet : {distincts} sur {attendu}")
    logger.info("zonage sismique polygones=%d", len(lignes))
    return lignes


# Le point est-il dans l'un des polygones ? Règle pair-impair, trous compris
def contient(polygones: list[list[list[list[float]]]], longitude: float, latitude: float) -> bool:
    for anneaux in polygones:
        dedans = False
        for anneau in anneaux:
            for (x1, y1), (x2, y2) in zip(anneau, anneau[1:] + anneau[:1], strict=True):
                if (y1 > latitude) != (y2 > latitude) and longitude < x1 + (latitude - y1) * (
                    x2 - x1
                ) / (y2 - y1):
                    dedans = not dedans
        if dedans:
            return True
    return False
