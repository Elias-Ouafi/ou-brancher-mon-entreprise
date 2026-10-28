from __future__ import annotations

import codecs
import csv
import logging
import zlib
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ingestion.config import Settings, get_settings
from ingestion.extract_geo import _retrying, _verifier
from ingestion.extract_odre import USER_AGENT, ExtractionError

logger = logging.getLogger(__name__)

TYPE_ACTIVITE = "Local industriel. commercial ou assimilé"
_TYPES_HABITATION = {"Maison", "Appartement"}
_COLONNES = {
    "id_mutation", "date_mutation", "numero_disposition", "nature_mutation", "valeur_fonciere",
    "code_commune", "code_departement", "type_local", "surface_reelle_bati", "longitude",
    "latitude",
}  # fmt: skip
_TAILLE_BLOC = 1 << 20


class DvfError(ExtractionError):
    pass


def _nombre(valeur: str | None) -> float:
    texte = (valeur or "").strip().replace(",", ".")
    return float(texte) if texte else 0.0


# Fichier gzip lu au fil de l'eau : une année de DVF pèse une centaine de Mo compressés
def _lignes_csv(blocs: Iterable[bytes]) -> Iterator[dict[str, str]]:
    decompresseur = zlib.decompressobj(wbits=31)
    decodeur = codecs.getincrementaldecoder("utf-8")()

    def texte() -> Iterator[str]:
        for bloc in blocs:
            if morceau := decodeur.decode(decompresseur.decompress(bloc)):
                yield morceau
        if fin := decodeur.decode(decompresseur.flush(), final=True):
            yield fin

    lecteur = csv.DictReader(_en_lignes(texte()))
    if manquantes := _COLONNES - set(lecteur.fieldnames or ()):
        raise DvfError(f"DVF : colonnes absentes {sorted(manquantes)}")
    yield from lecteur


def _en_lignes(morceaux: Iterable[str]) -> Iterator[str]:
    reste = ""
    for morceau in morceaux:
        lignes = (reste + morceau).split("\n")
        reste = lignes.pop()
        for ligne in lignes:
            yield ligne + "\n"
    if reste:
        yield reste


# Ventes de locaux d'activité, une ligne par mutation : prix et surface, avec le décompte des
# autres biens pour que la couche silver écarte les ventes à plusieurs types de local
def lire_mutations(lignes: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    mutations: list[dict[str, Any]] = []
    courante: dict[str, Any] | None = None

    def clore() -> None:
        if courante and courante["nb_locaux_activite"]:
            mutations.append({k: v for k, v in courante.items() if not k.startswith("_")})

    for ligne in lignes:
        if courante is None or ligne["id_mutation"] != courante["id_mutation"]:
            clore()
            courante = {
                "id_mutation": ligne["id_mutation"],
                "date_mutation": ligne["date_mutation"],
                "nature_mutation": ligne["nature_mutation"],
                "code_commune": ligne["code_commune"],
                "code_departement": ligne["code_departement"],
                "valeur_fonciere": _nombre(ligne["valeur_fonciere"]),
                "surface_bati_m2": 0.0,
                "nb_locaux_activite": 0.0,
                "nb_locaux_habitation": 0.0,
                "nb_dispositions": 0.0,
                "latitude": 0.0,
                "longitude": 0.0,
            }
        courante["nb_dispositions"] = max(
            courante["nb_dispositions"], _nombre(ligne["numero_disposition"])
        )
        if ligne["type_local"] == TYPE_ACTIVITE:
            courante["nb_locaux_activite"] += 1
            courante["surface_bati_m2"] += _nombre(ligne["surface_reelle_bati"])
            courante["latitude"] = courante["latitude"] or _nombre(ligne["latitude"])
            courante["longitude"] = courante["longitude"] or _nombre(ligne["longitude"])
        elif ligne["type_local"] in _TYPES_HABITATION:
            courante["nb_locaux_habitation"] += 1
    clore()
    return [m for m in mutations if m["nature_mutation"] == "Vente"]


# Années candidates, de la plus récente à la plus ancienne : une année pas encore publiée est sautée
def _annees(settings: Settings, aujourd_hui: datetime | None = None) -> list[int]:
    courante = (aujourd_hui or datetime.now(UTC)).year
    return [courante - i for i in range(settings.dvf_nb_annees + 1)]


def _telecharger_annee(
    settings: Settings, http: httpx.Client, annee: int
) -> list[dict[str, Any]] | None:
    url = settings.dvf_url.format(annee=annee)

    def lire() -> list[dict[str, Any]] | None:
        with http.stream("GET", url) as reponse:
            if reponse.status_code == 404:
                return None
            _verifier(reponse)
            return lire_mutations(_lignes_csv(reponse.iter_bytes(_TAILLE_BLOC)))

    return _retrying(settings)(lire)


def telecharger_ventes_locaux(
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    aujourd_hui: datetime | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    ventes: list[dict[str, Any]] = []
    retenues = 0
    try:
        for annee in _annees(settings, aujourd_hui):
            if retenues == settings.dvf_nb_annees:
                break
            lignes = _telecharger_annee(settings, http, annee)
            if lignes is None:
                logger.info("DVF %d non publie", annee)
                continue
            retenues += 1
            logger.info("DVF %d ventes de locaux d'activite=%d", annee, len(lignes))
            ventes.extend(lignes)
    finally:
        if client is None:
            http.close()
    if not ventes:
        raise DvfError("DVF : aucune vente de local d'activite lue")
    return ventes
