from __future__ import annotations

import csv
import io
import logging
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

_COLONNES = {
    "Area",
    "ISO 3 code",
    "Year",
    "Area type",
    "Electricity source",
    "Generation (TWh)",
    "Emissions intensity (gCO2e/kWh)",
    "EU member",
}


class EmberError(ExtractionError):
    pass


def lire_csv(contenu: bytes) -> list[dict[str, Any]]:
    lecteur = csv.DictReader(io.StringIO(contenu.decode("utf-8-sig")))
    if manquantes := _COLONNES - set(lecteur.fieldnames or ()):
        raise EmberError(f"export Ember : colonnes absentes {sorted(manquantes)}")
    return [
        {
            "iso3": ligne["ISO 3 code"],
            "annee": ligne["Year"],
            "pays_ember": ligne["Area"],
            "production_twh": float(ligne["Generation (TWh)"] or 0),
            "intensite_g_co2e_kwh": float(ligne["Emissions intensity (gCO2e/kWh)"]),
        }
        for ligne in lecteur
        if ligne["Electricity source"] == "Total generation"
        and ligne["Area type"] == "Country or economy"
        and ligne["EU member"] == "True"
        and ligne["Emissions intensity (gCO2e/kWh)"]
    ]


def telecharger_intensites(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        response = _retrying(settings)(lambda: _verifier(http.get(settings.ember_url)))
    finally:
        if client is None:
            http.close()
    lignes = lire_csv(response.content)
    logger.info("ember lignes=%d octets=%d", len(lignes), len(response.content))
    return lignes
