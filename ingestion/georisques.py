from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.acces_routier import _distance_km
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

# Rayons du voisinage : un choix de l'outil, aucun texte n'en fixe ; l'appartenance à un périmètre
# de plan de prévention technologique (PPRT) se lit dans les plans de prévention, pas ici
RAYON_SEVESO_KM = 5
RAYON_ICPE_KM = 1
RAYON_SOLS_M = 500
# Taille de page maximale admise par l'API
TAILLE_PAGE = 1000
REGIMES_ICPE = {"Autorisation", "Enregistrement"}
EN_EXPLOITATION = "En exploitation avec titre"

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
    pass


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


def niveau(statut: str | None) -> str | None:
    if statut and " - " in statut:
        return statut.split(" - ", 1)[1].strip().lower()
    return None


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


@dataclass(frozen=True, slots=True)
class RisquesSite:
    url: str | None
    risques: list[Risque]
    # `adresse` (rapport Géorisques) ou `commune` (repli : GASPAR et zonage sismique)
    maille: str = "adresse"
    # Repli sur l'export GASPAR local : date du fichier, l'API n'ayant pas répondu
    millesime: date | None = None

    def selon(self, situation: Exposition, famille: str | None = None) -> list[Risque]:
        return [
            r
            for r in self.risques
            if r.exposition is situation and (famille is None or r.famille == famille)
        ]


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


RECENSE = "Risque recensé dans la commune"

# Intensités du zonage sismique, publiées en majuscules sans accents
INTENSITES_SISMIQUES = {
    "TRES FAIBLE": "très faible",
    "FAIBLE": "faible",
    "MODEREE": "modérée",
    "MOYENNE": "moyenne",
    "FORTE": "forte",
}


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


# La zone de sismicité remplace la mention « Séisme » (13) de GASPAR, moins précise
def avec_zonage(risques: list[Risque], seisme: Risque) -> list[Risque]:
    return [seisme, *(r for r in risques if r.code != "gaspar_13")]


@dataclass(frozen=True, slots=True)
class Installation:
    nom: str
    commune: str
    regime: str
    # `seuil bas` ou `seuil haut` ; `None` hors Seveso
    seveso: str | None
    distance_km: float


@dataclass(frozen=True, slots=True)
class Voisinage:
    # Exposition aux argiles (retrait-gonflement) à l'adresse ; `None` hors carte
    argiles: str | None
    nb_cavites: int
    # Établissements Seveso en exploitation à moins de `RAYON_SEVESO_KM`, les plus proches d'abord
    seveso: list[Installation]
    # Installations classées (autorisation ou enregistrement) en exploitation à moins de
    # `RAYON_ICPE_KM`, Seveso compris
    nb_icpe: int
    nb_icpe_autorisation: int
    # Sols pollués à moins de `RAYON_SOLS_M` : secteurs d'information sur les sols (SIS),
    # servitudes d'utilité publique (SUP), sites en cours d'instruction, anciens sites (CASIAS)
    sis: list[str]
    nb_sup: int
    nb_instructions_en_cours: int
    nb_casias: int


def departement(code: str) -> str:
    # Corse : 20xxx au code postal, 2A ou 2B au code commune ; outre-mer sur trois chiffres
    if code[:2] in {"2A", "2B"}:
        return "20"
    return code[:3] if code.startswith("97") else code[:2]


def departements_differents(code_postal: Any, code_insee: Any) -> bool:
    if not code_postal or not code_insee:
        return False
    return departement(str(code_postal)) != departement(str(code_insee))


def lire_installations(
    entrees: list[dict[str, Any]], *, latitude: float, longitude: float
) -> list[Installation]:
    installations = []
    for e in entrees:
        # « Non ICPE » et « Autres régimes » sont sans titre ni état d'activité
        if e.get("regime") not in REGIMES_ICPE or e.get("etatActivite") != EN_EXPLOITATION:
            continue
        if e.get("latitude") is None or e.get("longitude") is None:
            continue
        # Fiche incohérente : TRIADIS SERVICES, rue de Madagascar à Rouen (76000), porte le code
        # commune de Paris et le centre de Paris pour position ; elle sortait à 5 km du 15e
        if departements_differents(e.get("codePostal"), e.get("codeInsee")):
            continue
        statut = str(e.get("statutSeveso") or "")
        installations.append(
            Installation(
                nom=str(e.get("raisonSociale") or "établissement sans nom"),
                commune=str(e.get("commune") or ""),
                regime=str(e["regime"]),
                seveso=statut.removeprefix("Seveso ") if statut.startswith("Seveso") else None,
                distance_km=_distance_km(latitude, longitude, e["latitude"], e["longitude"]),
            )
        )
    return sorted(installations, key=lambda i: i.distance_km)


def lire_voisinage(
    *,
    rga: dict[str, Any],
    cavites: dict[str, Any],
    installations: list[Installation],
    sis: dict[str, Any],
    sup: dict[str, Any],
    instructions: dict[str, Any],
    casias: dict[str, Any],
) -> Voisinage:
    proches = [i for i in installations if i.distance_km <= RAYON_ICPE_KM]
    return Voisinage(
        argiles=rga.get("exposition") or None,
        nb_cavites=int(cavites.get("results") or 0),
        seveso=[i for i in installations if i.seveso and i.distance_km <= RAYON_SEVESO_KM],
        nb_icpe=len(proches),
        nb_icpe_autorisation=sum(1 for i in proches if i.regime == "Autorisation"),
        sis=[str(e.get("nom") or "secteur sans nom") for e in sis.get("data") or []],
        nb_sup=int(sup.get("results") or 0),
        nb_instructions_en_cours=sum(
            1 for e in instructions.get("data") or [] if e.get("statut") == "En cours"
        ),
        nb_casias=int(casias.get("results") or 0),
    )


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

    def rapport(self, *, latitude: float, longitude: float) -> RisquesSite:
        return lire_rapport(self._json(RAPPORT_PATH, latitude=latitude, longitude=longitude))

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
            risques = avec_zonage(risques, seisme)
        return RisquesSite(url=None, risques=risques, maille="commune")

    def voisinage(self, *, latitude: float, longitude: float) -> Voisinage:
        point = {"latitude": latitude, "longitude": longitude}
        sols = {"rayon": RAYON_SOLS_M, "page_size": TAILLE_PAGE}
        entrees: list[dict[str, Any]] = []
        page, pages = 1, 1
        while page <= pages:
            reponse = self._json(
                "installations_classees",
                **point,
                rayon=RAYON_SEVESO_KM * 1000,
                page_size=TAILLE_PAGE,
                page=page,
            )
            entrees += reponse.get("data") or []
            pages, page = int(reponse.get("total_pages") or 1), page + 1
        return lire_voisinage(
            rga=self._json("rga", **point),
            cavites=self._json("cavites", **point, rayon=RAYON_SOLS_M),
            installations=lire_installations(entrees, **point),
            sis=self._json("ssp/conclusions_sis", **point, **sols),
            sup=self._json("ssp/conclusions_sup", **point, **sols),
            instructions=self._json("ssp/instructions", **point, **sols),
            casias=self._json("ssp/casias", **point, **sols),
        )

    def _json(
        self, path: str, *, latitude: float, longitude: float, **params: Any
    ) -> dict[str, Any]:

        def envoyer() -> httpx.Response:
            self.calls += 1
            response = self._client.get(
                path, params={"latlon": f"{longitude},{latitude}", **params}
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RetryableStatusError(response.status_code)
            response.raise_for_status()
            return response

        try:
            reponse = self._retrying(envoyer)
            # `rga` répond 200 sans corps là où la carte des argiles n'existe pas
            payload = reponse.json() if reponse.content else {}
        except (httpx.HTTPError, RetryableStatusError, ValueError) as error:
            raise GeorisquesError(f"{path} : {type(error).__name__} {error}") from error
        if not isinstance(payload, dict):
            raise GeorisquesError(f"{path} : reponse inattendue")
        return payload


def risques_gaspar(lignes: list[tuple[str, str, str, date]]) -> RisquesSite | None:
    if not lignes:
        return None
    code_insee = lignes[0][0]
    payload = {
        "data": [
            {
                "code_insee": code_insee,
                "risques_detail": [
                    {"num_risque": numero, "libelle_risque_long": libelle}
                    for _, numero, libelle, _ in lignes
                ],
            }
        ]
    }
    return RisquesSite(
        url=None,
        risques=lire_gaspar(payload, {code_insee}),
        maille="commune",
        millesime=max(ligne[3] for ligne in lignes),
    )


# Risques du site : rapport à l'adresse, sinon export local, sinon API par commune
def lire_risques(
    *,
    latitude: float,
    longitude: float,
    codes_commune: set[str],
    settings: Settings | None = None,
    repli: RisquesSite | None = None,
) -> RisquesSite:
    with GeorisquesClient(settings) as client:
        try:
            return client.rapport(latitude=latitude, longitude=longitude)
        except GeorisquesError as error:
            # L'API commune tombe d'ordinaire en même temps : le fichier local passe avant
            if repli is not None:
                logger.warning("rapport a l'adresse indisponible, repli sur GASPAR : %s", error)
                return repli
            logger.warning("rapport a l'adresse indisponible, repli sur la commune : %s", error)
            return client.rapport_commune(
                latitude=latitude, longitude=longitude, codes_commune=codes_commune
            )


def lire_voisinage_site(
    *, latitude: float, longitude: float, settings: Settings | None = None
) -> Voisinage:
    with GeorisquesClient(settings) as client:
        return client.voisinage(latitude=latitude, longitude=longitude)
