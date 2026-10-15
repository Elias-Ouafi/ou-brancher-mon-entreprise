"""Risques naturels et technologiques à l'adresse, d'après Géorisques.

C'est le seul volet du diagnostic **propre à l'adresse exacte** : le prix est
national, la tension du réseau régionale, la pression industrielle
départementale. Géorisques (ministère de la Transition écologique, BRGM) croise
la position géocodée avec les zonages réglementaires et les cartographies
d'aléas, et publie pour chaque risque un statut à l'échelle de la commune et un
statut à l'adresse.

Points de conception :

* **Un appel, au moment du diagnostic.** Le rapport (`resultats_rapport_risque`)
  dépend de la position exacte : il n'a pas de sens en entrepôt. Comme la BAN,
  il est appelé à la demande et simulé dans les tests.
* **API instable : réessayer, se replier, puis ne pas bloquer.** Constaté le
  15 octobre 2026 : le rapport à l'adresse coupe la plupart des connexions sans
  réponse, alors que les points d'accès communaux répondent. Le client réessaie
  sur les erreurs passagères, puis se replie sur les risques **recensés dans la
  commune** (base GASPAR) et la zone de sismicité, en le disant. Si rien ne
  répond, le diagnostic sort quand même et le signale
  (`ingestion.diagnostic.completer_risques`).
* **« Non connu » n'est pas « absent ».** Un risque présent dans la commune mais
  non évalué à l'adresse est affiché comme tel, jamais comme une absence.

Documentation : https://www.georisques.gouv.fr/doc-api
"""

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

#: Libellés français des risques, par code Géorisques. L'API renvoie aussi un
#: libellé, mais sans accents pour certains (« Feu de foret ») : il sert de repli.
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


class GeorisquesError(ExtractionError):
    """Géorisques n'a pas fourni de rapport exploitable."""


class Exposition(StrEnum):
    """Situation de l'adresse face à un risque."""

    #: Risque existant (ou « concerné ») à l'adresse.
    EXPOSE = "expose"
    #: Présent dans la commune, mais l'adresse n'est pas concernée.
    NON_EXPOSE = "non_expose"
    #: Présent dans la commune, non évalué à l'adresse.
    INCONNU = "inconnu"
    #: Absent de la commune.
    ABSENT = "absent"


def _sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def exposition(statut: str | None) -> Exposition:
    """Classe un statut Géorisques (« Risque Existant - faible », « Risque non Connu »…)."""
    if not statut:
        return Exposition.ABSENT
    texte = _sans_accents(statut)
    if "non concerne" in texte:
        return Exposition.NON_EXPOSE
    # « Recensé dans la commune » : statut du repli communal, jamais évalué à l'adresse.
    if "non connu" in texte or "inconnu" in texte or "recense" in texte:
        return Exposition.INCONNU
    if "existant" in texte or "concerne" in texte:
        return Exposition.EXPOSE
    logger.warning("statut Georisques inconnu, classe comme non evalue : %r", statut)
    return Exposition.INCONNU


def niveau(statut: str | None) -> str | None:
    """« Risque Existant - important » -> « important »."""
    if statut and " - " in statut:
        return statut.split(" - ", 1)[1].strip().lower()
    return None


@dataclass(frozen=True, slots=True)
class Risque:
    """Un risque du rapport Géorisques, vu de la commune et de l'adresse."""

    code: str
    libelle: str
    famille: str
    statut_commune: str | None
    statut_adresse: str | None

    @property
    def exposition(self) -> Exposition:
        """À l'adresse ; un risque absent de la commune l'est aussi de l'adresse."""
        if exposition(self.statut_commune) is Exposition.ABSENT and not self.statut_adresse:
            return Exposition.ABSENT
        situation = exposition(self.statut_adresse)
        # Présent dans la commune sans statut à l'adresse : non évalué, pas absent.
        return Exposition.INCONNU if situation is Exposition.ABSENT else situation

    @property
    def niveau(self) -> str | None:
        return niveau(self.statut_adresse) or niveau(self.statut_commune)


@dataclass(frozen=True, slots=True)
class RisquesSite:
    """Risques d'un site : rapport à l'adresse, ou repli sur la commune."""

    url: str | None
    risques: list[Risque]
    #: `adresse` (rapport Géorisques) ou `commune` (repli : GASPAR et zonage sismique).
    maille: str = "adresse"

    def selon(self, situation: Exposition, famille: str | None = None) -> list[Risque]:
        """Risques dans une situation donnée, d'une famille ou des deux."""
        return [
            r
            for r in self.risques
            if r.exposition is situation and (famille is None or r.famille == famille)
        ]


def lire_rapport(payload: dict[str, Any]) -> RisquesSite:
    """Transforme la réponse `resultats_rapport_risque` en `RisquesSite`."""
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


#: Statut donné aux risques du repli communal : recensés dans la commune, sans
#: évaluation à l'adresse (classés `Exposition.INCONNU`).
RECENSE = "Risque recensé dans la commune"

#: Intensités du zonage sismique, publiées en majuscules sans accents.
INTENSITES_SISMIQUES = {
    "TRES FAIBLE": "très faible",
    "FAIBLE": "faible",
    "MODEREE": "modérée",
    "MOYENNE": "moyenne",
    "FORTE": "forte",
}


def lire_gaspar(payload: dict[str, Any], codes_commune: set[str]) -> list[Risque]:
    """Risques recensés dans la commune (`gaspar/risques`).

    GASPAR numérote les risques naturels en 1x et les technologiques en 2x, avec
    des sous-types à trois chiffres (11 inondation, 112 crue lente…). Un type
    général est omis quand un de ses sous-types, plus parlant, est listé.
    """
    communes = payload.get("data")
    if not isinstance(communes, list) or not communes:
        raise GeorisquesError("aucune commune dans la reponse GASPAR")
    commune = next((c for c in communes if c.get("code_insee") in codes_commune), communes[0])
    details = [d for d in commune.get("risques_detail") or [] if d.get("num_risque")]
    numeros = {str(d["num_risque"]) for d in details}
    risques = []
    for detail in details:
        numero = str(detail["num_risque"])
        if any(autre != numero and autre.startswith(numero) for autre in numeros):
            continue
        risques.append(
            Risque(
                code=f"gaspar_{numero}",
                libelle=str(detail.get("libelle_risque_long") or numero),
                famille="naturel" if numero.startswith("1") else "technologique",
                statut_commune=RECENSE,
                statut_adresse=None,
            )
        )
    return risques


def lire_zonage(payload: dict[str, Any]) -> Risque | None:
    """Zone de sismicité de la commune (`zonage_sismique`) : « 2 - FAIBLE »."""
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


class GeorisquesClient:
    """Client HTTP de l'API Géorisques, utilisable comme context manager."""

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

    def rapport(self, *, latitude: float, longitude: float) -> RisquesSite:
        """Rapport de risques au point donné (WGS 84)."""
        return lire_rapport(self._json(RAPPORT_PATH, latitude=latitude, longitude=longitude))

    def rapport_commune(
        self, *, latitude: float, longitude: float, codes_commune: set[str]
    ) -> RisquesSite:
        """Repli : risques recensés dans la commune du point, et sa zone de sismicité."""
        risques = lire_gaspar(
            self._json(GASPAR_PATH, latitude=latitude, longitude=longitude), codes_commune
        )
        try:
            seisme = lire_zonage(self._json(ZONAGE_PATH, latitude=latitude, longitude=longitude))
        except GeorisquesError as error:
            logger.warning("zonage sismique indisponible : %s", error)
            seisme = None
        if seisme is not None:
            risques = [seisme, *risques]
        return RisquesSite(url=None, risques=risques, maille="commune")

    def _json(self, path: str, *, latitude: float, longitude: float) -> dict[str, Any]:
        """GET au point donné, avec nouvelles tentatives sur les erreurs passagères."""

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


def lire_risques(
    *,
    latitude: float,
    longitude: float,
    codes_commune: set[str],
    settings: Settings | None = None,
) -> RisquesSite:
    """Rapport à l'adresse ; à défaut, repli sur la commune.

    Lève `GeorisquesError` si Géorisques ne fournit ni l'un ni l'autre.
    """
    with GeorisquesClient(settings) as client:
        try:
            return client.rapport(latitude=latitude, longitude=longitude)
        except GeorisquesError as error:
            logger.warning("rapport a l'adresse indisponible, repli sur la commune : %s", error)
            return client.rapport_commune(
                latitude=latitude, longitude=longitude, codes_commune=codes_commune
            )
