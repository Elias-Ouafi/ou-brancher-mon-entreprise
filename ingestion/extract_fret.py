from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

_COLONNES_PORTS = {"LOCODE_PORT", "PORT", "STATUT", "ANNEE", "TONNAGE_TOT", "MAR_TOT", "EVP_TOT"}
_COLONNES_AEROPORTS = {"ANMOIS", "APT", "APT_NOM", "APT_ZON", "APT_FRP_dep", "APT_FRP_arr"}
_COLONNES_CHANTIERS = {"Nom du chantier", "VILLE", "CP", "Latitude", "Longitude"}
_LONGUEUR = "Longueur apte à la manutention (ml)"
_FICHIER_AEROPORTS = re.compile(r"ASP_APT_(\d{4})\.csv$")
_ARCHIVE_AEROPORTS = re.compile(r"ASP_APT_\d{4}_\d{4}\.zip$", re.IGNORECASE)


class FretError(ExtractionError):
    pass


def _telecharger(settings: Settings, client: httpx.Client | None, url: str) -> httpx.Response:
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        return _retrying(settings)(lambda: _verifier(http.get(url)))
    finally:
        if client is None:
            http.close()


def _nombre(valeur: str | None) -> float:
    texte = (valeur or "").strip().replace(",", ".")
    return float(texte) if texte else 0.0


def _lecteur(texte: str, colonnes: set[str], source: str) -> csv.DictReader:
    lecteur = csv.DictReader(io.StringIO(texte.lstrip("﻿")), delimiter=";")
    if manquantes := colonnes - set(lecteur.fieldnames or ()):
        raise FretError(f"{source} : colonnes absentes {sorted(manquantes)}")
    return lecteur


# Marchandises par port et par année (SDES), entrées et sorties additionnées
def lire_ports(contenu: bytes) -> list[dict[str, Any]]:
    cumul: dict[tuple[str, str], dict[str, Any]] = {}
    for ligne in _lecteur(contenu.decode("utf-8-sig"), _COLONNES_PORTS, "ports SDES"):
        cle = (ligne["LOCODE_PORT"], ligne["ANNEE"])
        port = cumul.setdefault(
            cle,
            {
                "locode": cle[0],
                "annee": cle[1],
                "port": ligne["PORT"],
                "statut": ligne["STATUT"],
                "marchandises_t": 0.0,
                "tonnage_total_t": 0.0,
                "conteneurs_evp": 0.0,
            },
        )
        port["marchandises_t"] += _nombre(ligne["MAR_TOT"])
        port["tonnage_total_t"] += _nombre(ligne["TONNAGE_TOT"])
        port["conteneurs_evp"] += _nombre(ligne["EVP_TOT"])
    return list(cumul.values())


# Fret et poste par aéroport et par année (DGAC, série APT), douze mois additionnés
def lire_aeroports(contenu: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as error:
        raise FretError(f"archive DGAC illisible : {error}") from error
    cumul: dict[tuple[str, str], dict[str, Any]] = {}
    mois: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    with archive:
        fichiers = [n for n in archive.namelist() if _FICHIER_AEROPORTS.search(n)]
        if not fichiers:
            raise FretError("archive DGAC sans fichier ASP_APT_<annee>.csv")
        for nom in fichiers:
            texte = archive.read(nom).decode("utf-8-sig")
            for ligne in _lecteur(texte, _COLONNES_AEROPORTS, nom):
                cle = (ligne["APT"], ligne["ANMOIS"][:4])
                aeroport = cumul.setdefault(
                    cle,
                    {
                        "code_oaci": cle[0],
                        "annee": cle[1],
                        "aeroport": ligne["APT_NOM"].strip(),
                        "zone": ligne["APT_ZON"],
                        "fret_t": 0.0,
                        "vols_cargo": 0.0,
                    },
                )
                aeroport["fret_t"] += _nombre(ligne["APT_FRP_dep"]) + _nombre(ligne["APT_FRP_arr"])
                aeroport["vols_cargo"] += _nombre(ligne.get("APT_NMVT_cgo"))
                mois[cle].add(ligne["ANMOIS"])
    for cle, aeroport in cumul.items():
        aeroport["nb_mois"] = float(len(mois[cle]))
    return list(cumul.values())


# Chantiers rail-route de SNCF Réseau : voies de manutention de toutes les cours d'un chantier
def lire_chantiers(contenu: bytes) -> list[dict[str, Any]]:
    cumul: dict[str, dict[str, Any]] = {}
    lecteur = _lecteur(contenu.decode("utf-8-sig"), _COLONNES_CHANTIERS, "chantiers SNCF")
    for ligne in lecteur:
        nom = ligne["Nom du chantier"].strip()
        chantier = cumul.setdefault(
            nom,
            {
                "chantier": nom,
                "ville": ligne["VILLE"].strip(),
                "code_postal": ligne["CP"].strip().zfill(5),
                "manutention": set(),
                "longueur_m": 0.0,
                "latitude": _nombre(ligne["Latitude"]),
                "longitude": _nombre(ligne["Longitude"]),
            },
        )
        chantier["longueur_m"] += _nombre(ligne.get(_LONGUEUR))
        if engins := (ligne.get("Manutention 2021") or "").strip():
            chantier["manutention"].add(engins)
    # Engins de toutes les cours, dans un ordre stable
    for chantier in cumul.values():
        chantier["manutention"] = ", ".join(sorted(chantier["manutention"])) or None
    return list(cumul.values())


# Ports du SDES : quels ports reçoivent et expédient vraiment des marchandises
def telecharger_ports(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    lignes = lire_ports(_telecharger(settings, client, settings.ports_fret_url).content)
    logger.info("ports SDES lignes=%d", len(lignes))
    return lignes


# L'archive DGAC change d'adresse à chaque publication : elle se retrouve par le catalogue
def telecharger_aeroports(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    try:
        jeu = _telecharger(settings, client, settings.aeroports_fret_dataset_url).json()
    except ValueError as error:
        raise FretError(f"catalogue DGAC illisible ({error})") from error
    ressources = jeu.get("resources", [])
    archives = [r for r in ressources if _ARCHIVE_AEROPORTS.search(r.get("title", ""))]
    if not archives:
        raise FretError("catalogue DGAC sans archive ASP_APT_<debut>_<fin>.zip")
    url = max(archives, key=lambda r: r["title"])["url"]
    lignes = lire_aeroports(_telecharger(settings, client, url).content)
    logger.info("aeroports DGAC lignes=%d", len(lignes))
    return lignes


# Chantiers rail-route : où charger un conteneur sur un train
def telecharger_chantiers(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    reponse = _telecharger(settings, client, settings.chantiers_rail_route_url)
    lignes = lire_chantiers(reponse.content)
    logger.info("chantiers rail-route lignes=%d", len(lignes))
    return lignes
