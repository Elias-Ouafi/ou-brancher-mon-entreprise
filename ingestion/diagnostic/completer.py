"""Sources appelées à la demande : une panne est dite dans sa section sans bloquer le reste."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import duckdb

from ingestion.acces_routier import AccesRoutierError, lire_acces
from ingestion.config import Settings, get_settings
from ingestion.diagnostic.modeles import Diagnostic
from ingestion.diagnostic.requetes import DIPLOMES_SQL
from ingestion.eau import EauError, lire_situation_eau
from ingestion.extract_sismique import contient
from ingestion.georisques import (
    GeorisquesError,
    Risque,
    RisquesSite,
    avec_zonage,
    lire_risques,
    lire_voisinage_site,
    lire_zonage,
    risques_gaspar,
)
from ingestion.image_site import ImageSiteError, lire_image_site
from ingestion.main_oeuvre import (
    MINUTES_TRAJET,
    MainOeuvre,
    MainOeuvreError,
    lire_zone_trajet,
)
from ingestion.solaire import (
    SolaireError,
    lire_batiment,
    lire_productible_pvgis,
)
from ingestion.zones_activite import ZonesActiviteError, lire_zones_activite

logger = logging.getLogger(__name__)


_GASPAR_SQL = """
select risques.code_insee_commune, risques.num_risque, types.libelle_risque, risques.jour_millesime
from etoile.fait_risque_commune as risques
inner join etoile.dim_risque as types using (num_risque)
where risques.code_insee_commune in (select unnest(?))
order by risques.code_insee_commune, risques.num_risque
"""


# Plus forte zone d'abord : sur une limite entre deux communes, la plus exigeante s'applique
_ZONAGE_SQL = """
select libelle_zone, geometrie
from gold.fct_zonage_sismique
where $longitude between lon_min and lon_max and $latitude between lat_min and lat_max
order by zone_sismicite desc
"""


# Zone de sismicité du point d'après le zonage chargé par `refresh` ; `None` s'il manque
def _zonage_local(
    connection: duckdb.DuckDBPyConnection, latitude: float, longitude: float
) -> Risque | None:
    try:
        candidats = connection.execute(
            _ZONAGE_SQL, {"latitude": latitude, "longitude": longitude}
        ).fetchall()
    except duckdb.CatalogException:
        return None
    libelle = next(
        (z for z, geometrie in candidats if contient(json.loads(geometrie), longitude, latitude)),
        None,
    )
    return None if libelle is None else lire_zonage({"data": [{"zone_sismicite": libelle}]})


# Export GASPAR et zonage sismique chargés par `refresh` ; `None` si GASPAR n'est pas chargé
def _gaspar_local(
    codes_commune: set[str], settings: Settings | None, *, latitude: float, longitude: float
) -> RisquesSite | None:
    path = (settings or get_settings()).duckdb_path
    if not Path(path).exists():
        return None
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            lignes = connection.execute(_GASPAR_SQL, [sorted(codes_commune)]).fetchall()
            seisme = _zonage_local(connection, latitude, longitude)
    except duckdb.CatalogException:
        return None
    site = risques_gaspar(lignes)
    if site is None or seisme is None:
        return site
    return replace(site, risques=avec_zonage(site.risques, seisme))


def avec_risques_locaux(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    if diagnostic.risques is not None:
        return diagnostic
    t = diagnostic.territoire
    codes = {t.code_insee_commune, diagnostic.code_insee_commune}
    risques = _gaspar_local(codes, settings, latitude=t.latitude, longitude=t.longitude)
    return replace(diagnostic, risques=risques)


def completer_risques(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    codes_commune = {t.code_insee_commune, diagnostic.code_insee_commune}
    try:
        risques = lire_risques(
            latitude=t.latitude,
            longitude=t.longitude,
            codes_commune=codes_commune,
            settings=settings,
            repli=_gaspar_local(
                codes_commune, settings, latitude=t.latitude, longitude=t.longitude
            ),
        )
    except GeorisquesError as error:
        logger.warning("risques non evalues : %s", error)
        return replace(diagnostic, risques_erreur=str(error))
    return replace(diagnostic, risques=risques)


def completer_voisinage(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        voisinage = lire_voisinage_site(
            latitude=t.latitude, longitude=t.longitude, settings=settings
        )
    except GeorisquesError as error:
        logger.warning("voisinage non consulte : %s", error)
        return replace(diagnostic, voisinage_erreur=str(error))
    return replace(diagnostic, voisinage=voisinage)


def completer_solaire(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        batiment = lire_batiment(latitude=t.latitude, longitude=t.longitude, settings=settings)
        productible = lire_productible_pvgis(
            latitude=t.latitude, longitude=t.longitude, settings=settings
        )
    except SolaireError as error:
        logger.warning("bâtiment et solaire non evalues : %s", error)
        return replace(diagnostic, solaire_erreur=str(error))
    return replace(diagnostic, batiment=batiment, productible=productible, solaire_consulte=True)


def completer_image_site(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        image = lire_image_site(latitude=t.latitude, longitude=t.longitude, settings=settings)
    except ImageSiteError as error:
        logger.warning("image du site non consultee : %s", error)
        return replace(diagnostic, image_site_erreur=str(error))
    return replace(diagnostic, image_site=image)


def completer_acces_routier(
    diagnostic: Diagnostic, *, settings: Settings | None = None
) -> Diagnostic:
    t = diagnostic.territoire
    try:
        acces = lire_acces(
            latitude=t.latitude,
            longitude=t.longitude,
            poles=diagnostic.poles_proches,
            settings=settings,
        )
    except AccesRoutierError as error:
        logger.warning("acces routier non evalue : %s", error)
        return replace(diagnostic, acces_routier_erreur=str(error))
    return replace(diagnostic, acces_routier=acces)


def completer_zones_activite(
    diagnostic: Diagnostic, *, settings: Settings | None = None
) -> Diagnostic:
    t = diagnostic.territoire
    try:
        nb, zones = lire_zones_activite(
            latitude=t.latitude, longitude=t.longitude, settings=settings
        )
    except ZonesActiviteError as error:
        logger.warning("zones d'activite non consultees : %s", error)
        return replace(diagnostic, zones_activite_erreur=str(error))
    return replace(diagnostic, nb_zones_activite=nb, zones_activite=zones)


def completer_main_oeuvre(
    diagnostic: Diagnostic, *, settings: Settings | None = None
) -> Diagnostic:
    if "diplomes_commune" not in diagnostic.chargements:
        return diagnostic
    t = diagnostic.territoire
    try:
        zone = lire_zone_trajet(latitude=t.latitude, longitude=t.longitude, settings=settings)
    except MainOeuvreError as error:
        logger.warning("main-d'oeuvre non evaluee : %s", error)
        return replace(diagnostic, main_oeuvre_erreur=str(error))
    points = [point for poly in zone for anneau in poly for point in anneau]
    emprise = {
        "lon_min": min(x for x, _ in points),
        "lon_max": max(x for x, _ in points),
        "lat_min": min(y for _, y in points),
        "lat_max": max(y for _, y in points),
    }
    path = (settings or get_settings()).duckdb_path
    with duckdb.connect(str(path), read_only=True) as connection:
        candidates = connection.execute(DIPLOMES_SQL, emprise).fetchall()
    dedans = [c for c in candidates if contient(zone, c[0], c[1])]
    return replace(
        diagnostic,
        main_oeuvre=MainOeuvre(
            minutes=MINUTES_TRAJET,
            nb_communes=len(dedans),
            nb_superieur=sum(c[2] for c in dedans),
            nb_cap_bep_bac=sum(c[3] for c in dedans),
            nb_15_ans_plus=sum(c[4] for c in dedans),
            millesime=max((c[5] for c in dedans), default=""),
        ),
    )


def completer_eau(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        eau = lire_situation_eau(latitude=t.latitude, longitude=t.longitude, settings=settings)
    except EauError as error:
        logger.warning("restrictions d'eau non consultees : %s", error)
        return replace(diagnostic, eau_erreur=str(error))
    return replace(diagnostic, eau=eau)


# Tous les appels à la demande de `diagnose` ; aucun ne fait échouer le diagnostic
def completer_en_ligne(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    for completer in (
        completer_risques,
        completer_voisinage,
        completer_eau,
        completer_solaire,
        completer_image_site,
        completer_acces_routier,
        completer_zones_activite,
        completer_main_oeuvre,
    ):
        diagnostic = completer(diagnostic, settings=settings)
    return diagnostic
