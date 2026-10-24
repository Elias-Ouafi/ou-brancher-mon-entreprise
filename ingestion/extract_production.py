from __future__ import annotations

import logging
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

REGISTRE_DATASET = "registre-national-installation-production-stockage-electricite-agrege"

# Au-dessous de 10 MW, une installation ne fait pas un pôle de production : 2 500 lignes sur 141 000
SEUIL_INSTALLATION_KW = 10_000

# Agrégation faite par ODRÉ : les noms « Confidentiel » ne permettent pas de clé par installation
_SELECT = (
    "codeinseecommuneimplantation as code_commune, commune, codedepartement as code_departement,"
    " filiere, sum(puismaxinstallee) as puissance_kw,"
    " sum(energieannuelleglissanteproduite) as energie_kwh, count(*) as nb_installations"
)
_GROUP_BY = "codeinseecommuneimplantation, commune, codedepartement, filiere"


class ProductionError(ExtractionError):
    pass


def _client(settings: Settings, client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def _diviser(valeur: float | None, par: float) -> float | None:
    return None if valeur is None else valeur / par


def _json(settings: Settings, client: httpx.Client | None, url: str, params: dict) -> Any:
    http = _client(settings, client)
    try:
        response = _retrying(settings)(lambda: _verifier(http.get(url, params=params)))
    finally:
        if client is None:
            http.close()
    try:
        return response.json()
    except ValueError as error:
        raise ProductionError(f"{url} : reponse illisible ({error})") from error


# Puissance en service par commune et filière (registre national, installations d'au moins 10 MW)
def telecharger_sites(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    url = f"{settings.api_base_url}/catalog/datasets/{REGISTRE_DATASET}/exports/json"
    lignes = _json(
        settings,
        client,
        url,
        {
            "select": _SELECT,
            "where": f"puismaxinstallee >= {SEUIL_INSTALLATION_KW} and regime = 'En service'",
            "group_by": _GROUP_BY,
        },
    )
    if not isinstance(lignes, list):
        raise ProductionError("registre de production : liste attendue")
    logger.info("registre production lignes=%d", len(lignes))
    return [
        {
            "code_commune": ligne.get("code_commune"),
            "commune": ligne.get("commune"),
            "filiere": ligne.get("filiere"),
            "code_departement": ligne.get("code_departement"),
            "puissance_mw": _diviser(ligne.get("puissance_kw"), 1e3),
            "energie_gwh": _diviser(ligne.get("energie_kwh"), 1e6),
            "nb_installations": ligne.get("nb_installations"),
        }
        for ligne in lignes
    ]


# Centre de chaque commune (geo.api.gouv.fr) : là où la carte pose un pôle de production
def telecharger_centres(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    communes = _json(
        settings,
        client,
        settings.geo_communes_url,
        {"fields": "code,nom,centre,codeDepartement", "format": "json"},
    )
    if not isinstance(communes, list):
        raise ProductionError("communes geo.api : liste attendue")
    lignes = []
    for commune in communes:
        point = (commune.get("centre") or {}).get("coordinates")
        if not point:
            continue
        lignes.append(
            {
                "code_commune": commune.get("code"),
                "nom": commune.get("nom"),
                "code_departement": commune.get("codeDepartement"),
                "longitude": point[0],
                "latitude": point[1],
            }
        )
    logger.info("centres de communes=%d", len(lignes))
    return lignes
