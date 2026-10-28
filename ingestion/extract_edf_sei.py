from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

# Filières EDF SEI, en MW moyens sur l'heure ; vides quand l'île n'a pas la filière
FILIERES = (
    "production_totale_mw",
    "thermique_mw",
    "bagasse_charbon_mw",
    "hydraulique_mw",
    "micro_hydraulique_mw",
    "photovoltaique_mw",
    "eolien_mw",
    "bioenergies_mw",
    "geothermie_mw",
    "importations_mw",
    "stockage_mw",
    "cout_moyen_de_production_eur_mwh",
)
_COLONNES = {"territoire", "statut", "date_heure", "production_totale_mw", "thermique_mw"}


class EdfSeiError(ExtractionError):
    pass


def _nombre(valeur: str | None) -> float | None:
    return float(valeur) if valeur not in (None, "") else None


def lire_csv(contenu: bytes) -> list[dict[str, Any]]:
    lecteur = csv.DictReader(io.StringIO(contenu.decode("utf-8-sig")), delimiter=";")
    if manquantes := _COLONNES - set(lecteur.fieldnames or ()):
        raise EdfSeiError(f"production EDF SEI : colonnes absentes {sorted(manquantes)}")
    return [
        {
            "territoire": ligne["territoire"],
            # Depuis 2024, le fichier omet parfois les secondes (« 2024-01-01T00:00+00:00 »)
            "date_heure": datetime.fromisoformat(ligne["date_heure"]).isoformat(),
            "statut": ligne["statut"],
            **{filiere: _nombre(ligne.get(filiere)) for filiere in FILIERES},
        }
        for ligne in lecteur
    ]


def telecharger_production(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=max(settings.http_timeout_seconds, 120),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        response = _retrying(settings)(lambda: _verifier(http.get(settings.edf_sei_url)))
    finally:
        if client is None:
            http.close()
    lignes = lire_csv(response.content)
    logger.info("edf_sei lignes=%d octets=%d", len(lignes), len(response.content))
    return lignes
