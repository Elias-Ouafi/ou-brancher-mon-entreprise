from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

# Seul fichier de l'export qui liste les risques de chaque commune : `ddrm_risq_gaspar_<date>.csv`
_FICHIER_RISQUES = re.compile(r"ddrm_risq_gaspar_(\d{4}-\d{2}-\d{2})\.csv$")
_COLONNES = {"cod_commune", "lib_commune", "lib_risque", "num_risque"}

# Plans de prévention naturels, technologiques et miniers : `pprn_gaspar_<date>.csv`…
_FICHIER_PLANS = re.compile(r"(ppr[nmt])_gaspar_(\d{4}-\d{2}-\d{2})\.csv$")
_COLONNES_PLANS = {
    "CODE INSEE COMMUNE",
    "CODE PROCEDURE",
    "LIBELLE PROCEDURE",
    "CODE RISQUE 2",
    "LIBELLE RISQUE 2",
    "CODE RISQUE 3",
    "LIBELLE RISQUE 3",
    "LIBELLE ETAT",
    "LIBELLE SOUS-ETAT",
    "PRESCRIPTION",
    "APPROBATION",
}


class GasparError(ExtractionError):
    pass


def _ouvrir(contenu: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as error:
        raise GasparError(f"export GASPAR illisible : {error}") from error


def _lire_csv(archive: zipfile.ZipFile, nom: str, colonnes: set[str]) -> csv.DictReader:
    texte = archive.read(nom).decode("utf-8-sig")
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    if manquantes := colonnes - set(lecteur.fieldnames or ()):
        raise GasparError(f"{nom} : colonnes absentes {sorted(manquantes)}")
    return lecteur


# Risques par commune lus dans l'archive GASPAR : les risques sans dépendre de l'API
def lire_export(contenu: bytes) -> list[dict[str, Any]]:
    with _ouvrir(contenu) as archive:
        fichiers = [(m, n) for n in archive.namelist() if (m := _FICHIER_RISQUES.search(n))]
        if not fichiers:
            raise GasparError("export GASPAR sans fichier ddrm_risq_gaspar_<date>.csv")
        correspondance, nom = max(fichiers, key=lambda f: f[0].group(1))
        lecteur = _lire_csv(archive, nom, _COLONNES)
        millesime = correspondance.group(1)
        return [
            {
                "code_commune": ligne["cod_commune"],
                "num_risque": ligne["num_risque"],
                "libelle_commune": ligne["lib_commune"],
                "libelle_risque": ligne["lib_risque"],
                "millesime": millesime,
            }
            for ligne in lecteur
        ]


# Plans de prévention par commune : ce qui contraint vraiment une construction, à la différence
# des risques simplement recensés ; dernier fichier de chaque type (PPRN, PPRT, PPRM)
def lire_plans(contenu: bytes) -> list[dict[str, Any]]:
    with _ouvrir(contenu) as archive:
        derniers: dict[str, tuple[str, str]] = {}
        for nom in archive.namelist():
            if (m := _FICHIER_PLANS.search(nom)) and m.group(2) > derniers.get(
                m.group(1), ("", "")
            )[0]:
                derniers[m.group(1)] = (m.group(2), nom)
        if not derniers:
            raise GasparError("export GASPAR sans fichier pprn/pprt/pprm_gaspar_<date>.csv")
        plans: dict[tuple[str, str, str], dict[str, Any]] = {}
        for type_plan, (millesime, nom) in sorted(derniers.items()):
            for ligne in _lire_csv(archive, nom, _COLONNES_PLANS):
                code_risque = ligne["CODE RISQUE 3"] or ligne["CODE RISQUE 2"]
                cle = (ligne["CODE INSEE COMMUNE"], ligne["CODE PROCEDURE"], code_risque)
                # Doublon d'une même clé dans le fichier : la dernière ligne publiée l'emporte
                plans[cle] = {
                    "code_commune": ligne["CODE INSEE COMMUNE"] or None,
                    "code_procedure": ligne["CODE PROCEDURE"],
                    "code_risque": code_risque,
                    "type_plan": type_plan.upper(),
                    "libelle_plan": ligne["LIBELLE PROCEDURE"].strip(),
                    "libelle_risque": ligne["LIBELLE RISQUE 2"] or None,
                    "libelle_alea": ligne["LIBELLE RISQUE 3"] or None,
                    "etat": ligne["LIBELLE ETAT"] or None,
                    "sous_etat": ligne["LIBELLE SOUS-ETAT"] or None,
                    "date_prescription": ligne["PRESCRIPTION"] or None,
                    "date_approbation": ligne["APPROBATION"] or None,
                    "millesime": millesime,
                }
        return list(plans.values())


# Archive GASPAR complète, relue à chaque refresh : risques recensés et plans de prévention
def telecharger_archive(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> bytes:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        response = _retrying(settings)(lambda: _verifier(http.get(settings.gaspar_url)))
    finally:
        if client is None:
            http.close()
    logger.info("gaspar octets=%d", len(response.content))
    return response.content
