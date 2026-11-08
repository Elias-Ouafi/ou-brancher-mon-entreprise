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

# Zones de répartition des eaux (code de l'environnement, R211-71), référentiel ZRPE du Sandre :
# une zone par entité, France entière, 118 au 8 novembre 2026, sans pagination nécessaire
COUCHE = "sa:ZRE"
_NOMBRE = re.compile(r'numberMatched="(\d+)"')
# Cinq décimales, environ un mètre : assez pour situer une adresse
DECIMALES = 5
TYPES_ZRE = {"ZRESup", "ZRESout", "Mixte"}


class ZonesRepartitionError(ExtractionError):
    pass


def _polygones(geometrie: dict[str, Any]) -> list[list[list[list[float]]]]:
    if geometrie.get("type") == "Polygon":
        polygones = [geometrie["coordinates"]]
    elif geometrie.get("type") == "MultiPolygon":
        polygones = geometrie["coordinates"]
    else:
        raise ZonesRepartitionError(f"geometrie inattendue : {geometrie.get('type')!r}")
    return [
        [[[round(x, DECIMALES), round(y, DECIMALES)] for x, y, *_ in anneau] for anneau in poly]
        for poly in polygones
    ]


def lire_zones(payload: dict[str, Any]) -> list[dict[str, Any]]:
    lignes = []
    for feature in payload.get("features") or []:
        proprietes = feature.get("properties") or {}
        if (type_zre := proprietes.get("TypeZRE")) not in TYPES_ZRE:
            raise ZonesRepartitionError(f"type de ZRE inconnu : {type_zre!r}")
        polygones = _polygones(feature.get("geometry") or {})
        points = [p for poly in polygones for anneau in poly for p in anneau]
        lignes.append(
            {
                "gid": str(proprietes["gid"]),
                "code_zone": str(proprietes.get("CodeNatZone") or ""),
                # Une partie des zones n'a pas de nom publié : vide plutôt qu'inventé
                "nom_zone": str(proprietes.get("NomZone") or "").strip(),
                "type_zre": type_zre,
                "geometrie": json.dumps(polygones, separators=(",", ":")),
                "lon_min": min(x for x, _ in points),
                "lat_min": min(y for _, y in points),
                "lon_max": max(x for x, _ in points),
                "lat_max": max(y for _, y in points),
            }
        )
    return lignes


def telecharger_zones(
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
            lambda: _verifier(http.get(settings.zones_repartition_eaux_url, params=parametres))
        )

    try:
        if not (nombre := _NOMBRE.search(lire({**base, "resultType": "hits"}).text)):
            raise ZonesRepartitionError("nombre de zones non annonce par le service WFS")
        attendu = int(nombre.group(1))
        lignes = lire_zones(lire({**base, "outputFormat": "geojson", "sortBy": "gid"}).json())
    finally:
        if client is None:
            http.close()
    if (distincts := len({ligne["gid"] for ligne in lignes})) != attendu:
        raise ZonesRepartitionError(f"zones de repartition incompletes : {distincts} sur {attendu}")
    logger.info("zones de repartition des eaux=%d", len(lignes))
    return lignes
