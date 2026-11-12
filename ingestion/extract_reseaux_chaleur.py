from __future__ import annotations

import io
import json
import logging
import math
import zipfile
from collections.abc import Iterator
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

# Un réseau sans tracé publié n'est qu'un point au centre de sa commune
COUCHES = {"reseaux_de_chaleur.geojson": "oui", "reseaux_de_chaleur_sans_traces.geojson": "non"}
# Un point gardé tous les 100 m de tracé : la distance à l'adresse se trompe de 50 m au plus
PAS_M = 100.0
# Métropole et Corse : quelques tracés publiés hors de la France ou en coordonnées fausses
EMPRISE = {"latitude": (41.0, 51.6), "longitude": (-5.8, 10.0)}

# Lambert-93 (IGN, notice NTG 71) : RGF93 et WGS84 diffèrent de moins d'un mètre
_N = 0.7256077650
_C = 11754255.426
_XS, _YS = 700000.0, 12655612.050
_E = 0.0818191910428158
_LON0 = math.radians(3.0)


class ReseauxChaleurError(ExtractionError):
    pass


def lambert93_vers_wgs84(x: float, y: float) -> tuple[float, float]:
    dx, dy = x - _XS, _YS - y
    rayon = math.hypot(dx, dy)
    longitude = _LON0 + math.atan2(dx, dy) / _N
    iso = -math.log(rayon / _C) / _N
    latitude = 2 * math.atan(math.exp(iso)) - math.pi / 2
    for _ in range(12):
        s = _E * math.sin(latitude)
        latitude = 2 * math.atan(((1 + s) / (1 - s)) ** (_E / 2) * math.exp(iso)) - math.pi / 2
    return math.degrees(latitude), math.degrees(longitude)


def _sommets(geometrie: dict[str, Any] | None) -> Iterator[tuple[float, float]]:
    if not geometrie:
        return
    lignes = {
        "Point": [[geometrie["coordinates"]]],
        "LineString": [geometrie["coordinates"]],
        "MultiLineString": geometrie["coordinates"],
    }.get(geometrie["type"], [])
    # Les deux bouts de chaque ligne sont gardés : l'extrémité d'une antenne compte
    for ligne in lignes:
        dernier = None
        for k, (x, y, *_) in enumerate(ligne):
            bout = k in (0, len(ligne) - 1)
            if bout or math.hypot(x - dernier[0], y - dernier[1]) >= PAS_M:
                dernier = (x, y)
                yield x, y


def _texte(valeur: Any) -> str | None:
    texte = str(valeur).strip() if valeur is not None else ""
    return texte or None


def _nombre(valeur: Any) -> float | None:
    try:
        return None if valeur is None else float(valeur)
    except (TypeError, ValueError):
        return None


# Une ligne par point gardé ; près de 200 réseaux sans identifiant national : le nom en tient lieu
def lire_reseaux(archive: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if manquantes := set(COUCHES) - set(zf.namelist()):
            raise ReseauxChaleurError(
                f"France Chaleur Urbaine : couches absentes {sorted(manquantes)}"
            )
        couches = {nom: json.loads(zf.read(nom)) for nom in COUCHES}
    lignes: list[dict[str, Any]] = []
    # Un même nom peut porter plusieurs entités : leurs points se suivent sous ce nom
    rangs: dict[str, int] = {}
    for nom, a_trace in COUCHES.items():
        for entite in couches[nom]["features"]:
            p = entite.get("properties") or {}
            nom_reseau = _texte(p.get("nom_reseau"))
            reseau_id = _texte(p.get("Identifiant reseau")) or (
                nom_reseau and f"sans-id:{nom_reseau}"
            )
            if reseau_id is None:
                continue
            communs = {
                "reseau_id": reseau_id,
                "nom_reseau": nom_reseau,
                "communes": _texte(p.get("communes")),
                "departements": _texte(p.get("departement")),
                "gestionnaire": _texte(p.get("Gestionnaire")),
                "a_trace": a_trace,
                "classe": {True: "oui", False: "non"}.get(p.get("reseaux classes")),
                "taux_enr_pct": _nombre(p.get("Taux EnR&R")),
                "contenu_co2_kg_kwh": _nombre(p.get("contenu CO2")),
                "livraisons_mwh": _nombre(p.get("livraisons_totale_MWh")),
            }
            for x, y in _sommets(entite.get("geometry")):
                latitude, longitude = lambert93_vers_wgs84(x, y)
                if not (
                    EMPRISE["latitude"][0] <= latitude <= EMPRISE["latitude"][1]
                    and EMPRISE["longitude"][0] <= longitude <= EMPRISE["longitude"][1]
                ):
                    continue
                rangs[reseau_id] = rangs.get(reseau_id, 0) + 1
                lignes.append(
                    {
                        **communs,
                        "rang": str(rangs[reseau_id]),
                        "latitude": round(latitude, 6),
                        "longitude": round(longitude, 6),
                    }
                )
    return lignes


# La ressource change de nom chaque mois : on prend l'archive la plus récente du jeu
def _url_derniere_archive(http: httpx.Client, settings: Settings) -> str:
    jeu = _retrying(settings)(lambda: _verifier(http.get(settings.reseaux_chaleur_jeu_url))).json()
    archives = [r for r in jeu.get("resources", []) if str(r.get("url", "")).endswith(".zip")]
    if not archives:
        raise ReseauxChaleurError("France Chaleur Urbaine : aucune archive dans le jeu")
    return max(archives, key=lambda r: r.get("last_modified") or "")["url"]


def telecharger_reseaux_chaleur(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        url = _url_derniere_archive(http, settings)
        reponse = _retrying(settings)(lambda: _verifier(http.get(url)))
    finally:
        if client is None:
            http.close()
    lignes = lire_reseaux(reponse.content)
    if not lignes:
        raise ReseauxChaleurError("France Chaleur Urbaine : aucun réseau situé")
    logger.info(
        "France Chaleur Urbaine reseaux=%d points=%d",
        len({ligne["reseau_id"] for ligne in lignes}),
        len(lignes),
    )
    return lignes
