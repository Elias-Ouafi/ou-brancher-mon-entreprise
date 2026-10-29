from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

_COLONNES = {
    "site_id", "site_nom", "site_type", "site_statut", "site_reconv_type", "site_actu_date",
    "comm_insee", "comm_nom", "bati_surface", "unite_fonciere_surface", "proprio_type",
    "sol_pollution_existe", "urba_zone_type", "geompoint",
}  # fmt: skip
_POINT = re.compile(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)")
# Séparateur « ; », valeurs absentes écrites « NA »
_SEPARATEUR = ";"


class FrichesError(ExtractionError):
    pass


# Les contributeurs saisissent en UTF-8 ou en Windows-1252 selon la ligne : on décode ligne à ligne
def _decoder(contenu: bytes) -> str:
    def ligne(brut: bytes) -> str:
        try:
            return brut.decode("utf-8")
        except UnicodeDecodeError:
            return brut.decode("cp1252", errors="replace")

    return "\n".join(ligne(brut) for brut in contenu.split(b"\n"))


def _texte(valeur: str | None) -> str | None:
    texte = (valeur or "").strip()
    return None if texte in {"", "NA"} else texte


def _nombre(valeur: str | None) -> float | None:
    texte = _texte(valeur)
    try:
        return None if texte is None else float(texte.replace(",", "."))
    except ValueError:
        return None


# Une ligne par site ; sans point géolocalisé, un site ne se situe pas et est écarté
def lire_friches(contenu: bytes) -> list[dict[str, Any]]:
    lecteur = csv.DictReader(io.StringIO(_decoder(contenu)), delimiter=_SEPARATEUR)
    if manquantes := _COLONNES - set(lecteur.fieldnames or ()):
        raise FrichesError(f"Cartofriches : colonnes absentes {sorted(manquantes)}")
    friches = []
    for ligne in lecteur:
        point = _POINT.fullmatch((ligne["geompoint"] or "").strip())
        identifiant = _texte(ligne["site_id"])
        if point is None or identifiant is None:
            continue
        friches.append(
            {
                "site_id": identifiant,
                "site_nom": _texte(ligne["site_nom"]),
                "site_type": _texte(ligne["site_type"]),
                "site_statut": _texte(ligne["site_statut"]),
                "site_reconv_type": _texte(ligne["site_reconv_type"]),
                "site_actu_date": _texte(ligne["site_actu_date"]),
                "comm_insee": _texte(ligne["comm_insee"]),
                "comm_nom": _texte(ligne["comm_nom"]),
                "proprio_type": _texte(ligne["proprio_type"]),
                "sol_pollution_existe": _texte(ligne["sol_pollution_existe"]),
                "urba_zone_type": _texte(ligne["urba_zone_type"]),
                "bati_surface_m2": _nombre(ligne["bati_surface"]),
                "unite_fonciere_surface_m2": _nombre(ligne["unite_fonciere_surface"]),
                "longitude": float(point.group(1)),
                "latitude": float(point.group(2)),
            }
        )
    return friches


def telecharger_friches(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        reponse = _retrying(settings)(lambda: _verifier(http.get(settings.friches_url)))
    finally:
        if client is None:
            http.close()
    friches = lire_friches(reponse.content)
    if not friches:
        raise FrichesError("Cartofriches : aucune friche geolocalisee lue")
    logger.info("Cartofriches friches=%d", len(friches))
    return friches
