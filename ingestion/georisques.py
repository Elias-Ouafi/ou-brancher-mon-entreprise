from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import Settings, get_settings
from ingestion.extract_odre import (
    RETRYABLE_STATUS_CODES,
    USER_AGENT,
    ExtractionError,
    RetryableStatusError,
)

logger = logging.getLogger(__name__)

RAPPORT_PATH = "resultats_rapport_risque"
GASPAR_PATH = "gaspar/risques"
ZONAGE_PATH = "zonage_sismique"

# Libellés français des risques, par code Géorisques
LIBELLES: dict[str, str] = {
    "inondation": "Inondation",
    "remonteeNappe": "Remontée de nappe",
    "risqueCotier": "Risques côtiers (submersion marine, tsunami)",
    "seisme": "Séisme",
    "mouvementTerrain": "Mouvements de terrain",
    "reculTraitCote": "Recul du trait de côte",
    "retraitGonflementArgile": "Retrait-gonflement des argiles",
    "avalanche": "Avalanche",
    "feuForet": "Feu de forêt",
    "eruptionVolcanique": "Volcan",
    "cyclone": "Vent violent",
    "radon": "Radon",
    "icpe": "Installations industrielles classées (ICPE)",
    "nucleaire": "Nucléaire",
    "canalisationsMatieresDangereuses": "Canalisations de matières dangereuses",
    "pollutionSols": "Pollution des sols",
    "ruptureBarrage": "Rupture de barrage",
    "risqueMinier": "Risques miniers",
}

FAMILLES = {"risquesNaturels": "naturel", "risquesTechnologiques": "technologique"}


# Géorisques n'a pas fourni de rapport exploitable
class GeorisquesError(ExtractionError):
    pass


# Situation de l'adresse face à un risque
class Exposition(StrEnum):
    # Risque existant (ou « concerné ») à l'adresse
    EXPOSE = "expose"
    # Présent dans la commune, mais l'adresse n'est pas concernée
    NON_EXPOSE = "non_expose"
    # Présent dans la commune, non évalué à l'adresse
    INCONNU = "inconnu"
    # Absent de la commune
    ABSENT = "absent"


def _sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


# Classe un statut Géorisques (« Risque Existant - faible », « Risque non Connu »…)
def exposition(statut: str | None) -> Exposition:
    if not statut:
        return Exposition.ABSENT
    texte = _sans_accents(statut)
    if "non concerne" in texte:
        return Exposition.NON_EXPOSE
    # « Recensé dans la commune » : statut du repli communal, jamais évalué à l'adresse
    if "non connu" in texte or "inconnu" in texte or "recense" in texte:
        return Exposition.INCONNU
    if "existant" in texte or "concerne" in texte:
        return Exposition.EXPOSE
    logger.warning("statut Georisques inconnu, classe comme non evalue : %r", statut)
    return Exposition.INCONNU


# « Risque Existant - important » -> « important »
def niveau(statut: str | None) -> str | None:
    if statut and " - " in statut:
        return statut.split(" - ", 1)[1].strip().lower()
    return None


# Un risque du rapport Géorisques, vu de la commune et de l'adresse
@dataclass(frozen=True, slots=True)
class Risque:
    code: str
    libelle: str
    famille: str
    statut_commune: str | None
    statut_adresse: str | None

    # À l'adresse ; un risque absent de la commune l'est aussi de l'adresse
    @property
    def exposition(self) -> Exposition:
        if exposition(self.statut_commune) is Exposition.ABSENT and not self.statut_adresse:
            return Exposition.ABSENT
        situation = exposition(self.statut_adresse)
        # Présent dans la commune sans statut à l'adresse : non évalué, pas absent
        return Exposition.INCONNU if situation is Exposition.ABSENT else situation

    @property
    def niveau(self) -> str | None:
        return niveau(self.statut_adresse) or niveau(self.statut_commune)


# Risques d'un site : rapport à l'adresse, ou repli sur la commune
@dataclass(frozen=True, slots=True)
class RisquesSite:
    url: str | None
    risques: list[Risque]
    # `adresse` (rapport Géorisques) ou `commune` (repli : GASPAR et zonage sismique)
    maille: str = "adresse"

    # Risques dans une situation donnée, d'une famille ou des deux
    def selon(self, situation: Exposition, famille: str | None = None) -> list[Risque]:
        return [
            r
            for r in self.risques
            if r.exposition is situation and (famille is None or r.famille == famille)
        ]


# Transforme la réponse `resultats_rapport_risque` en `RisquesSite`
def lire_rapport(payload: dict[str, Any]) -> RisquesSite:
    risques = []
    for bloc, famille in FAMILLES.items():
        entrees = payload.get(bloc)
        if not isinstance(entrees, dict):
            raise GeorisquesError(f"rapport Georisques sans bloc {bloc!r}")
        for code, entree in entrees.items():
            if not isinstance(entree, dict):
                continue
            risques.append(
                Risque(
                    code=code,
                    libelle=LIBELLES.get(code) or str(entree.get("libelle") or code),
                    famille=famille,
                    statut_commune=entree.get("libelleStatutCommune"),
                    statut_adresse=entree.get("libelleStatutAdresse"),
                )
            )
    return RisquesSite(url=payload.get("url"), risques=risques)


# Statut donné aux risques du repli communal : recensés dans la commune
RECENSE = "Risque recensé dans la commune"

# Intensités du zonage sismique, publiées en majuscules sans accents
INTENSITES_SISMIQUES = {
    "TRES FAIBLE": "très faible",
    "FAIBLE": "faible",
    "MODEREE": "modérée",
    "MOYENNE": "moyenne",
    "FORTE": "forte",
}


# Risques recensés dans la commune (`gaspar/risques`)
def lire_gaspar(payload: dict[str, Any], codes_commune: set[str]) -> list[Risque]:
    communes = payload.get("data")
    if not isinstance(communes, list) or not communes:
        raise GeorisquesError("aucune commune dans la reponse GASPAR")
    commune = next((c for c in communes if c.get("code_insee") in codes_commune), communes[0])
    details = [d for d in commune.get("risques_detail") or [] if d.get("num_risque")]
    libelles = {str(d["num_risque"]): str(d.get("libelle_risque_long") or "") for d in details}
    risques = []
    for numero, libelle in libelles.items():
        if any(autre != numero and autre.startswith(numero) for autre in libelles):
            continue
        type_general = libelles.get(numero[:2], "")
        if len(numero) > 2 and type_general and libelle.lower().startswith("par "):
            libelle = f"{type_general} {libelle[0].lower()}{libelle[1:]}"
        risques.append(
            Risque(
                code=f"gaspar_{numero}",
                libelle=libelle or numero,
                famille="naturel" if numero.startswith("1") else "technologique",
                statut_commune=RECENSE,
                statut_adresse=None,
            )
        )
    return risques


# Zone de sismicité de la commune (`zonage_sismique`) : « 2 - FAIBLE »
def lire_zonage(payload: dict[str, Any]) -> Risque | None:
    zones = payload.get("data")
    if not isinstance(zones, list) or not zones:
        return None
    libelle = str(zones[0].get("zone_sismicite") or "")
    if " - " not in libelle:
        return None
    numero, intensite = (part.strip() for part in libelle.split(" - ", 1))
    return Risque(
        code="seisme",
        libelle=f"Séisme, zone {numero}",
        famille="naturel",
        statut_commune=f"{RECENSE} - {INTENSITES_SISMIQUES.get(intensite, intensite.lower())}",
        statut_adresse=None,
    )


# Client HTTP de l'API Géorisques, utilisable comme context manager
class GeorisquesClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.calls = 0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.georisques_base_url,
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._retrying = Retrying(
            stop=stop_after_attempt(self.settings.max_retries),
            wait=wait_exponential(
                multiplier=self.settings.retry_wait_seconds,
                max=max(self.settings.retry_wait_seconds * 30, 1),
            ),
            retry=retry_if_exception_type((httpx.TransportError, RetryableStatusError)),
            reraise=True,
        )

    def close(self) -> None:
        logger.info("georisques_api_calls=%d", self.calls)
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GeorisquesClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # Rapport de risques au point donné (WGS 84)
    def rapport(self, *, latitude: float, longitude: float) -> RisquesSite:
        return lire_rapport(self._json(RAPPORT_PATH, latitude=latitude, longitude=longitude))

    # Repli : risques recensés dans la commune du point, et sa zone de sismicité
    def rapport_commune(
        self, *, latitude: float, longitude: float, codes_commune: set[str]
    ) -> RisquesSite:
        risques = lire_gaspar(
            self._json(GASPAR_PATH, latitude=latitude, longitude=longitude), codes_commune
        )
        try:
            seisme = lire_zonage(self._json(ZONAGE_PATH, latitude=latitude, longitude=longitude))
        except GeorisquesError as error:
            logger.warning("zonage sismique indisponible : %s", error)
            seisme = None
        if seisme is not None:
            # La zone de sismicité remplace la mention « Séisme » (13) de GASPAR
            risques = [seisme, *(r for r in risques if r.code != "gaspar_13")]
        return RisquesSite(url=None, risques=risques, maille="commune")

    # GET au point donné, avec nouvelles tentatives sur les erreurs passagères
    def _json(self, path: str, *, latitude: float, longitude: float) -> dict[str, Any]:

        def envoyer() -> httpx.Response:
            self.calls += 1
            response = self._client.get(path, params={"latlon": f"{longitude},{latitude}"})
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RetryableStatusError(response.status_code)
            response.raise_for_status()
            return response

        try:
            payload = self._retrying(envoyer).json()
        except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
            raise GeorisquesError(f"{path} : {type(error).__name__} {error}") from error
        if not isinstance(payload, dict):
            raise GeorisquesError(f"{path} : reponse inattendue")
        return payload


# Rapport à l'adresse ; à défaut, repli sur la commune
def lire_risques(
    *,
    latitude: float,
    longitude: float,
    codes_commune: set[str],
    settings: Settings | None = None,
) -> RisquesSite:
    with GeorisquesClient(settings) as client:
        try:
            return client.rapport(latitude=latitude, longitude=longitude)
        except GeorisquesError as error:
            logger.warning("rapport a l'adresse indisponible, repli sur la commune : %s", error)
            return client.rapport_commune(
                latitude=latitude, longitude=longitude, codes_commune=codes_commune
            )
