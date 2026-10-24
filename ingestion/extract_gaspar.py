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


class GasparError(ExtractionError):
    pass


# Risques par commune lus dans l'archive GASPAR : les risques sans dépendre de l'API
def lire_export(contenu: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as error:
        raise GasparError(f"export GASPAR illisible : {error}") from error
    with archive:
        fichiers = [(m, n) for n in archive.namelist() if (m := _FICHIER_RISQUES.search(n))]
        if not fichiers:
            raise GasparError("export GASPAR sans fichier ddrm_risq_gaspar_<date>.csv")
        correspondance, nom = max(fichiers, key=lambda f: f[0].group(1))
        texte = archive.read(nom).decode("utf-8-sig")
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    if manquantes := _COLONNES - set(lecteur.fieldnames or ()):
        raise GasparError(f"{nom} : colonnes absentes {sorted(manquantes)}")
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


# Export GASPAR complet, relu à chaque refresh
def telecharger_export(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
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
    lignes = lire_export(response.content)
    logger.info("gaspar lignes=%d octets=%d", len(lignes), len(response.content))
    return lignes
