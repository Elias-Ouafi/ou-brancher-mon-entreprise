from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import Settings, get_settings
from ingestion.extract_odre import RETRYABLE_STATUS_CODES, USER_AGENT, RetryableStatusError

logger = logging.getLogger(__name__)

# En dessous, le meilleur résultat est souvent une autre adresse (« Fort-de-France » → Dracy-le-Fort
# à 0,59) ; une faute de frappe avec code postal reste au-dessus (0,75)
SCORE_MINIMUM = 0.65
# Deux communes à moins de cet écart de score : la BAN ne sait pas trancher (« 3 allée des
# Peupliers » : Bordeaux 0,980, Saint-Quentin 0,975…)
MARGE_AMBIGUITE = 0.05
NB_CANDIDATS = 5
ARRONDISSEMENTS: dict[str, str] = {"751": "75056", "693": "69123", "132": "13055"}
_CODE_POSTAL = re.compile(r"(?<!\d)(\d{5})(?!\d)")

REGIONS: dict[str, str] = {
    "84": "Auvergne-Rhône-Alpes",
    "27": "Bourgogne-Franche-Comté",
    "53": "Bretagne",
    "24": "Centre-Val de Loire",
    "94": "Corse",
    "44": "Grand Est",
    "32": "Hauts-de-France",
    "11": "Île-de-France",
    "28": "Normandie",
    "75": "Nouvelle-Aquitaine",
    "76": "Occitanie",
    "52": "Pays de la Loire",
    "93": "Provence-Alpes-Côte d'Azur",
    "01": "Guadeloupe",
    "02": "Martinique",
    "03": "Guyane",
    "04": "La Réunion",
    "06": "Mayotte",
}


class GeocodingError(RuntimeError):
    pass


class AdresseIntrouvableError(GeocodingError):
    pass


class AdresseAmbigueError(GeocodingError):
    def __init__(self, demande: str, candidats: list[str]) -> None:
        super().__init__(f"{demande!r} correspond a plusieurs communes : {' ; '.join(candidats)}")
        self.candidats = candidats


# Arrondissement de Paris, Lyon ou Marseille ramené à sa commune : maille des données
def code_commune_parent(code_insee: str) -> str:
    if len(code_insee) == 5 and code_insee[:3] in ARRONDISSEMENTS:
        return ARRONDISSEMENTS[code_insee[:3]]
    return code_insee


def normalise(valeur: str) -> str:
    sans_accent = unicodedata.normalize("NFKD", valeur)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return "".join(c for c in sans_accent.lower() if c.isalnum())


# Indice de répétition écrit en entier par la BAN (« 13 bis »), en une lettre par Enedis (« 13 B »)
_INDICES = {"bis": "b", "ter": "t", "quater": "q", "quinquies": "c"}


# Numéro et voie comparables à l'adresse publiée par Enedis (même règle que la macro dbt)
def adresse_normalisee(voie: str) -> str:
    sans_accent = unicodedata.normalize("NFKD", voie)
    texte = "".join(c for c in sans_accent if not unicodedata.combining(c)).lower()
    texte = re.sub(
        r"^\s*(\d+)\s*(bis|ter|quater|quinquies)\b", lambda m: m[1] + _INDICES[m[2]], texte
    )
    return re.sub(r"[^a-z0-9]", "", texte)


_REGIONS_PAR_NOM: dict[str, str] = {normalise(nom): code for code, nom in REGIONS.items()}


def code_region(nom: str) -> str | None:
    return _REGIONS_PAR_NOM.get(normalise(nom))


@dataclass(frozen=True, slots=True)
class Territoire:
    adresse: str
    code_insee_commune: str
    commune: str
    code_postal: str | None
    code_departement: str
    departement: str
    code_insee_region: str | None
    region: str
    latitude: float
    longitude: float
    score: float
    # Précision du résultat BAN : `housenumber`, `street`, `locality`, `municipality`
    precision: str
    # Numéro et voie seuls (« 9 Rue Ampère »), sans code postal ni commune
    voie: str | None = None

    @property
    def est_precis(self) -> bool:
        return self.precision in {"housenumber", "street"}

    def __str__(self) -> str:
        return (
            f"{self.adresse} [commune {self.code_insee_commune}, "
            f"dept {self.code_departement}, region {self.code_insee_region or '?'}]"
        )


# Découpe le champ `context` de la BAN : « 69, Rhône, Auvergne-Rhône-Alpes »
def _contexte(valeur: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in valeur.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], parts[1]
    raise GeocodingError(f"champ context inattendu : {valeur!r}")


def _props(feature: dict[str, Any]) -> dict[str, Any]:
    return feature.get("properties") or {}


def _score(feature: dict[str, Any]) -> float:
    return float(_props(feature).get("score") or 0.0)


# Une commune seule n'a pas de code postal dans son libellé : « Saint-Denis » (93 ou 974)
def _libelle_candidat(props: dict[str, Any]) -> str:
    libelle = str(props.get("label") or "")
    if props.get("postcode") and str(props["postcode"]) in libelle:
        return libelle
    departement = str(props.get("context") or "").split(",")[0].strip()
    return f"{libelle} ({departement})" if departement else libelle


# Mieux vaut refuser que répondre pour une autre commune : prix et carbone n'en dépendent
# pas, mais le réseau, les sites et les risques si
def _choisir(features: list[dict[str, Any]], demande: str) -> dict[str, Any]:
    candidats = sorted(features, key=_score, reverse=True)
    if code_postal := _CODE_POSTAL.search(demande):
        candidats = [f for f in candidats if _props(f).get("postcode") == code_postal.group(1)]
        if not candidats:
            raise AdresseIntrouvableError(
                f"aucun resultat dans le code postal {code_postal.group(1)} pour {demande!r}"
            )
    meilleur = candidats[0]
    if _score(meilleur) < SCORE_MINIMUM:
        raise AdresseIntrouvableError(
            f"resultat trop approximatif pour {demande!r} (score {_score(meilleur):.2f},"
            f" {_props(meilleur).get('label')!r})"
        )
    proches: dict[str, str] = {}
    for feature in candidats:
        if _score(feature) >= _score(meilleur) - MARGE_AMBIGUITE:
            commune = code_commune_parent(str(_props(feature).get("citycode") or ""))
            proches.setdefault(commune, _libelle_candidat(_props(feature)))
    if len(proches) > 1:
        raise AdresseAmbigueError(demande, list(proches.values()))
    return meilleur


class BanClient:
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
            base_url=self.settings.ban_base_url,
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
        logger.info("ban_api_calls=%d", self.calls)
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BanClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def geocode(self, adresse: str) -> Territoire:
        if not adresse.strip():
            raise AdresseIntrouvableError("adresse vide")

        payload = self._get(
            "search",
            {
                "q": adresse,
                "index": "address",
                "limit": NB_CANDIDATS,
                "returntruegeometry": "false",
            },
        ).json()
        features = payload.get("features") or []
        if not features:
            raise AdresseIntrouvableError(f"aucun resultat pour {adresse!r}")

        return self._to_territoire(_choisir(features, adresse), demande=adresse)

    def _to_territoire(self, feature: dict[str, Any], *, demande: str) -> Territoire:
        props: dict[str, Any] = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]

        citycode = props.get("citycode")
        if not citycode:
            raise GeocodingError(f"reponse BAN sans code commune pour {demande!r}")

        code_dept, departement, region = _contexte(str(props.get("context", "")))
        score = float(props.get("score") or 0.0)
        precision = str(props.get("type") or "")

        if precision not in {"housenumber", "street"}:
            logger.warning(
                "adresse resolue a la maille %r seulement pour %r", precision or "inconnue", demande
            )

        insee_region = code_region(region)
        if insee_region is None:
            logger.warning("region %r non reconnue : code INSEE absent du diagnostic", region)

        return Territoire(
            adresse=str(props.get("label") or demande),
            code_insee_commune=str(citycode),
            commune=str(props.get("city") or ""),
            code_postal=str(props["postcode"]) if props.get("postcode") else None,
            code_departement=code_dept,
            departement=departement,
            code_insee_region=insee_region,
            region=region,
            latitude=float(coords[1]) if coords[1] is not None else 0.0,
            longitude=float(coords[0]) if coords[0] is not None else 0.0,
            score=score,
            precision=precision,
            voie=str(props["name"]) if props.get("name") else None,
        )

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:

        def envoyer() -> httpx.Response:
            self.calls += 1
            response = self._client.get(path, params=params)
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RetryableStatusError(response.status_code)
            response.raise_for_status()
            return response

        return self._retrying(envoyer)


def geocode(adresse: str, *, settings: Settings | None = None) -> Territoire:
    with BanClient(settings) as client:
        return client.geocode(adresse)
