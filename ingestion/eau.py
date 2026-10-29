from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError, RetryableStatusError

logger = logging.getLogger(__name__)

ZONES_PATH = "zones"

# Du moins au plus restrictif : le niveau le plus haut d'une zone décide de la situation
NIVEAUX = ("vigilance", "alerte", "alerte_renforcee", "crise")
LIBELLES_NIVEAU = {
    "vigilance": "vigilance",
    "alerte": "alerte",
    "alerte_renforcee": "alerte renforcée",
    "crise": "crise",
}
LIBELLES_TYPE = {"SUP": "eau superficielle", "SOU": "eau souterraine", "AEP": "eau potable"}


class EauError(ExtractionError):
    pass


@dataclass(frozen=True, slots=True)
class ZoneRestriction:
    # `SUP` (superficielle), `SOU` (souterraine) ou `AEP` (eau potable)
    type_zone: str
    nom: str
    niveau: str
    date_fin: date | None
    # Usages de l'arrêté qui concernent les entreprises
    nb_usages_entreprise: int
    url_arrete: str | None

    @property
    def rang(self) -> int:
        return NIVEAUX.index(self.niveau)


@dataclass(frozen=True, slots=True)
class SituationEau:
    zones: list[ZoneRestriction]

    @property
    def niveau_max(self) -> str | None:
        return max(self.zones, key=lambda z: z.rang).niveau if self.zones else None

    # Alerte renforcée ou crise : les prélèvements des entreprises sont réduits ou suspendus
    @property
    def est_contraignante(self) -> bool:
        return any(z.rang >= NIVEAUX.index("alerte_renforcee") for z in self.zones)


def _jour(valeur: str | None) -> date | None:
    return date.fromisoformat(valeur[:10]) if valeur else None


# Zones de restriction en vigueur au point, vigilance comprise ; liste vide : aucun arrêté
def lire_zones(payload: list[dict[str, Any]]) -> SituationEau:
    if not isinstance(payload, list):
        raise EauError("reponse VigiEau inattendue")
    zones = []
    for zone in payload:
        niveau = zone.get("niveauGravite")
        if niveau not in NIVEAUX:
            raise EauError(f"niveau de gravite VigiEau inconnu : {niveau!r}")
        arrete = zone.get("arrete") or {}
        zones.append(
            ZoneRestriction(
                type_zone=str(zone.get("type") or ""),
                nom=str(zone.get("nom") or ""),
                niveau=niveau,
                date_fin=_jour(arrete.get("dateFinValidite")),
                nb_usages_entreprise=sum(
                    1 for usage in zone.get("usages") or [] if usage.get("concerneEntreprise")
                ),
                url_arrete=arrete.get("cheminFichier") or None,
            )
        )
    return SituationEau(zones)


def lire_situation_eau(
    *,
    latitude: float,
    longitude: float,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> SituationEau:
    reglages = settings or get_settings()
    proprietaire = client is None
    http = client or httpx.Client(
        base_url=reglages.vigieau_base_url,
        timeout=reglages.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    params = {"lon": longitude, "lat": latitude, "profil": "entreprise"}
    try:
        reponse = _retrying(reglages)(lambda: _verifier(http.get(ZONES_PATH, params=params)))
        return lire_zones(reponse.json())
    except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
        raise EauError(f"{ZONES_PATH} : {type(error).__name__} {error}") from error
    finally:
        if proprietaire:
            http.close()
