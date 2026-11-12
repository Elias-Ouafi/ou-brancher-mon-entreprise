from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from typing import Any

import httpx
import openpyxl

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT
from ingestion.main_oeuvre import MainOeuvreError

logger = logging.getLogger(__name__)

# Diplôme le plus élevé des 15 ans ou plus non scolarisés (retraités compris) : CAP-BEP, bac
# (général, technologique ou professionnel, brevet professionnel), supérieur, et le total
DIPLOMES = {
    "300_RP": "nb_cap_bep",
    "350T351_RP": "nb_bac",
    "500T702_RP": "nb_superieur",
    "_T": "nb_15_ans_plus",
}
_COLONNES_DIPLOMES = {"GEO", "GEO_OBJECT", "AGE", "SEX", "EDUC", "TIME_PERIOD", "OBS_VALUE"}
_COLONNES_BMO = {"annee", "Code métier BMO", "Nom métier BMO", "Dept", "met", "xmet"}
# Secret statistique de l'enquête BMO
SECRET = "*"


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        timeout=max(settings.http_timeout_seconds, 120),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def _telecharger(url: str, settings: Settings, client: httpx.Client | None) -> bytes:
    http = client or _client(settings)
    try:
        return _retrying(settings)(lambda: _verifier(http.get(url))).content
    finally:
        if client is None:
            http.close()


# Le fichier mêle toutes les mailles (commune, arrondissement, zone d'emploi…), les deux sexes
# et trois millésimes : on garde les communes entières, tous sexes, au dernier millésime
def lire_diplomes(contenu: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as error:
        raise MainOeuvreError("diplomes Insee : archive illisible") from error
    nom = next((n for n in archive.namelist() if n.endswith("_data.csv")), None)
    if nom is None:
        raise MainOeuvreError("diplomes Insee : fichier de donnees absent de l'archive")
    par_annee: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    with archive, archive.open(nom) as brut:
        lecteur = csv.DictReader(io.TextIOWrapper(brut, encoding="utf-8-sig"), delimiter=";")
        if manquantes := _COLONNES_DIPLOMES - set(lecteur.fieldnames or ()):
            raise MainOeuvreError(f"diplomes Insee : colonnes absentes {sorted(manquantes)}")
        for ligne in lecteur:
            colonne = DIPLOMES.get(ligne["EDUC"])
            if colonne is None or (ligne["GEO_OBJECT"], ligne["SEX"]) != ("COM", "_T"):
                continue
            # Valeur vide : comptée dans une autre catégorie (statut K), donc zéro ici
            valeur = float(ligne["OBS_VALUE"] or 0)
            par_annee[ligne["TIME_PERIOD"]][ligne["GEO"]][colonne] = valeur
    if not par_annee:
        raise MainOeuvreError("diplomes Insee : aucune commune lue")
    millesime = max(par_annee)
    return [
        {"code_commune": commune, "millesime": millesime, **{c: 0.0 for c in DIPLOMES.values()}}
        | valeurs
        for commune, valeurs in par_annee[millesime].items()
    ]


def _effectif(valeur: Any) -> float | None:
    return None if valeur in (None, "", SECRET) else float(valeur)


# Projets de recrutement par bassin d'emploi de France Travail, ramenés au département : aucune
# table officielle ne passe du bassin à la commune ou à la zone d'emploi de l'Insee
def lire_recrutements(contenu: bytes) -> list[dict[str, Any]]:
    try:
        classeur = openpyxl.load_workbook(io.BytesIO(contenu), read_only=True)
    except (zipfile.BadZipFile, OSError, KeyError) as error:
        raise MainOeuvreError("BMO : classeur illisible") from error
    feuille = next((f for f in classeur.worksheets if f.title.lower().startswith("bmo")), None)
    if feuille is None:
        raise MainOeuvreError("BMO : feuille de donnees absente")
    lignes = feuille.iter_rows(values_only=True)
    entete = [str(c) for c in next(lignes, ())]
    if manquantes := _COLONNES_BMO - set(entete):
        raise MainOeuvreError(f"BMO : colonnes absentes {sorted(manquantes)}")
    rang = {nom: entete.index(nom) for nom in _COLONNES_BMO}
    cumuls: dict[tuple[str, str], dict[str, Any]] = {}
    for ligne in lignes:
        departement = str(ligne[rang["Dept"]] or "").zfill(2)
        metier = str(ligne[rang["Code métier BMO"]] or "")
        if departement == "00" or not metier:
            continue
        cumul = cumuls.setdefault(
            (departement, metier),
            {
                "code_departement": departement,
                "code_metier": metier,
                "libelle_metier": ligne[rang["Nom métier BMO"]],
                "annee": str(ligne[rang["annee"]]),
                "projets": 0.0,
                "projets_difficiles": 0.0,
                "nb_bassins": 0.0,
                "nb_bassins_secrets": 0.0,
            },
        )
        projets = _effectif(ligne[rang["met"]])
        difficiles = _effectif(ligne[rang["xmet"]])
        cumul["nb_bassins"] += 1
        if projets is None or difficiles is None:
            cumul["nb_bassins_secrets"] += 1
            continue
        cumul["projets"] += projets
        cumul["projets_difficiles"] += difficiles
    classeur.close()
    if not cumuls:
        raise MainOeuvreError("BMO : aucun projet de recrutement lu")
    return list(cumuls.values())


def telecharger_diplomes(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    lignes = lire_diplomes(_telecharger(settings.diplomes_url, settings, client))
    logger.info("diplomes Insee communes=%d", len(lignes))
    return lignes


def telecharger_recrutements(
    *, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    lignes = lire_recrutements(_telecharger(settings.bmo_url, settings, client))
    logger.info("BMO departements x metiers=%d", len(lignes))
    return lignes
