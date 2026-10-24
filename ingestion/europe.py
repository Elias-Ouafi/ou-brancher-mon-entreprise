from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb
import httpx

from ingestion.config import Settings, get_settings
from ingestion.diagnostic import DonneesAbsentesError
from ingestion.extract_odre import USER_AGENT
from ingestion.geocode import AdresseIntrouvableError, GeocodingError

logger = logging.getLogger(__name__)

# Seuil de la taxonomie européenne, le même que pour la France
SEUIL_DECARBONE_G_CO2E_KWH = 100.0


class HorsUnionEuropeenneError(GeocodingError):
    pass


@dataclass(frozen=True, slots=True)
class Localisation:
    libelle: str
    code_pays: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class Pays:
    code_eurostat: str
    pays: str
    code_tranche: str
    libelle_tranche: str
    prix_reference_eur_kwh: float | None
    premier_semestre_reference: str | None
    dernier_semestre: str | None
    intensite_g_co2e_kwh: float | None
    annee_carbone: int | None

    @property
    def est_decarbone(self) -> bool | None:
        if self.intensite_g_co2e_kwh is None:
            return None
        return self.intensite_g_co2e_kwh < SEUIL_DECARBONE_G_CO2E_KWH


# Pays d'une adresse hors de France (Nominatim, OpenStreetMap) : la BAN ne couvre que la France
def localiser(
    adresse: str, *, settings: Settings | None = None, client: httpx.Client | None = None
) -> Localisation:
    settings = settings or get_settings()
    http = client or httpx.Client(
        timeout=settings.http_timeout_seconds, headers={"User-Agent": USER_AGENT}
    )
    try:
        response = http.get(
            settings.nominatim_url,
            params={
                "q": adresse,
                "format": "jsonv2",
                "limit": 1,
                "addressdetails": 1,
                "accept-language": "fr",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise GeocodingError(f"Nominatim injoignable : {error}") from error
    finally:
        if client is None:
            http.close()
    resultats = response.json()
    if not resultats:
        raise AdresseIntrouvableError(adresse)
    meilleur = resultats[0]
    return Localisation(
        libelle=meilleur["display_name"],
        code_pays=meilleur["address"]["country_code"].upper(),
        latitude=float(meilleur["lat"]),
        longitude=float(meilleur["lon"]),
    )


_PAYS_SQL = """
select *
from gold.fct_pays_ue
where iso2 = $iso2
  and $conso >= conso_min_mwh
  and (conso_max_mwh is null or $conso < conso_max_mwh)
"""

# Sans `--conso`, la même tranche par défaut que pour la France : moins de 20 MWh par an
CONSO_DEFAUT_MWH = 10.0


# Prix et carbone moyens d'un pays de l'UE, et de la France pour comparer, à tranche égale
def lire_pays(
    code_pays: str,
    *,
    conso_mwh: float | None = None,
    settings: Settings | None = None,
    database: Path | None = None,
) -> tuple[Pays, Pays | None]:
    path = database or (settings or get_settings()).duckdb_path
    conso = CONSO_DEFAUT_MWH if conso_mwh is None else conso_mwh
    if not Path(path).exists():
        raise DonneesAbsentesError(f"warehouse introuvable : {path}")
    with duckdb.connect(str(path), read_only=True) as connection:

        def ligne(iso2: str) -> Pays | None:
            resultat = connection.execute(_PAYS_SQL, {"iso2": iso2, "conso": conso})
            row = resultat.fetchone()
            if row is None:
                return None
            valeurs = dict(zip([d[0] for d in resultat.description], row, strict=True))
            return Pays(**{cle: valeurs[cle] for cle in Pays.__dataclass_fields__})

        try:
            pays = ligne(code_pays)
            france = ligne("FR") if code_pays != "FR" else None
        except duckdb.CatalogException as error:
            raise DonneesAbsentesError(
                f"couche gold incomplete : {str(error).splitlines()[0]}"
            ) from error
    if pays is None:
        raise HorsUnionEuropeenneError(
            f"pays {code_pays} hors de l'Union européenne ou absent des données (lancer refresh)"
        )
    return pays, france


def _prix(p: Pays) -> str:
    if p.prix_reference_eur_kwh is None:
        return "non publié"
    return f"{p.prix_reference_eur_kwh:.4f}".replace(".", ",") + " €/kWh HTVA"


def _ecart(valeur: float | None, reference: float | None) -> str:
    if valeur is None or not reference:
        return ""
    ecart = (valeur / reference - 1) * 100
    return f" ({'+' if ecart >= 0 else '-'}{abs(ecart):.0f} % par rapport à la France)"


# Réponse courte pour une adresse européenne : prix et carbone du pays, face à la France
def formater_europe(localisation: Localisation, pays: Pays, france: Pays | None) -> str:
    lignes = [localisation.libelle, f"Pays : {pays.pays} ({pays.code_eurostat})", ""]
    prix = f"Prix moyen : {_prix(pays)} pour la tranche {pays.code_tranche}"
    prix += f" ({pays.libelle_tranche} ; Eurostat"
    prix += f", {pays.premier_semestre_reference} – {pays.dernier_semestre})"
    if france is not None:
        prix += _ecart(pays.prix_reference_eur_kwh, france.prix_reference_eur_kwh)
    lignes.append(prix)
    if pays.intensite_g_co2e_kwh is None:
        lignes.append("Électricité décarbonée : non disponible (Ember)")
    else:
        oui = "oui" if pays.est_decarbone else "non"
        position = "sous le" if pays.est_decarbone else "au-dessus du"
        carbone = (
            f"Électricité décarbonée : {oui}, {pays.intensite_g_co2e_kwh:.0f} gCO₂e/kWh en cycle"
            f" de vie (Ember, {pays.annee_carbone}), {position} seuil de"
            f" {SEUIL_DECARBONE_G_CO2E_KWH:.0f} gCO₂e/kWh"
        )
        if france is not None and france.intensite_g_co2e_kwh is not None:
            carbone += f" ; France : {france.intensite_g_co2e_kwh:.0f} selon Ember"
        lignes.append(carbone)
    lignes.append(
        "À savoir : moyennes nationales. Le prix varie selon le pays et la consommation ; le"
        " carbone est celui de la production du pays, sans les échanges aux frontières."
    )
    return "\n".join(lignes)
