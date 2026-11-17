"""Lecture du warehouse pour une adresse : assemble un `Diagnostic` à partir des requêtes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from ingestion.config import Settings, get_settings
from ingestion.courbe import Courbe, bilan_courbe
from ingestion.diagnostic.chargements import lire_chargements
from ingestion.diagnostic.formats import nombre_fr
from ingestion.diagnostic.modeles import (
    ACTIVITES,
    DOMAINES_TURPE,
    PROFIL_DEFAUT,
    TRANCHE_DEFAUT,
    Accise,
    Carbone,
    Continuite,
    ContrainteOuvrage,
    CoutProfil,
    Diagnostic,
    DiagnosticIndisponibleError,
    DonneesAbsentesError,
    EntreprisesAdresse,
    EntreprisesCommune,
    Friche,
    PlanPrevention,
    PrixEntreprise,
    PrixLocaux,
    ReseauChaleur,
    Seuil,
    SiteCleEnMain,
    ZoneRepartition,
    ZoneRte,
    code_departement,
)
from ingestion.diagnostic.requetes import (
    ACCISE_REDUITS_SQL,
    ACCISE_SQL,
    CARBONE_HORAIRE_SQL,
    CARBONE_SQL,
    CONTINUITE_SQL,
    CONTRAINTES_SQL,
    COUT_SQL,
    DIAGNOSTIC_SQL,
    ENTREPRISES_ADRESSE_SQL,
    ENTREPRISES_COMMUNE_SQL,
    FRICHES_SQL,
    LOCAUX_SQL,
    NB_CONTRAINTES_AFFICHEES,
    NB_FRICHES_AFFICHEES,
    PLANS_SQL,
    POLES_PROCHES_SQL,
    PRIX_DEFAUT_SQL,
    PRIX_SQL,
    PROFIL_CHARGE_SQL,
    RECRUTEMENTS_SQL,
    RESEAUX_CHALEUR_SQL,
    SEUIL_SQL,
    SITE_SQL,
    ZONES_SQL,
    ZRE_SQL,
)
from ingestion.extract_sismique import contient
from ingestion.geocode import Territoire, adresse_normalisee, code_commune_parent
from ingestion.main_oeuvre import (
    Recrutements,
)
from ingestion.turpe import lire_acheminement


def lire_diagnostic(
    territoire: Territoire,
    *,
    puissance_mw: float | None = None,
    conso_mwh: float | None = None,
    ca_eur: float | None = None,
    va_eur: float | None = None,
    surface_m2: float | None = None,
    activite: str | None = None,
    courbe: Courbe | None = None,
    settings: Settings | None = None,
    database: Path | None = None,
) -> Diagnostic:
    path = database or (settings or get_settings()).duckdb_path
    # La courbe réelle donne la consommation et la pointe quand elles ne sont pas déclarées
    if courbe is not None:
        conso_mwh = round(courbe.conso_annuelle_mwh, 1) if conso_mwh is None else conso_mwh
        puissance_mw = courbe.puissance_max_kw / 1000 if puissance_mw is None else puissance_mw
    if not Path(path).exists():
        raise DonneesAbsentesError(f"warehouse introuvable : {path}")

    commune = code_commune_parent(territoire.code_insee_commune)
    departement = code_departement(commune)
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            ligne = _une_ligne(
                connection, DIAGNOSTIC_SQL, {"commune": commune, "departement": departement}
            )
            seuil = (
                _seuil(connection, puissance_mw, departement) if puissance_mw is not None else None
            )
            profil_charge = ACTIVITES[activite].profil_charge if activite else None
            journee_type = (
                {
                    (type_jour, heure): kw
                    for type_jour, heure, kw in connection.execute(
                        PROFIL_CHARGE_SQL, {"profil": profil_charge}
                    ).fetchall()
                }
                if profil_charge
                else None
            )
            acheminement = (
                lire_acheminement(
                    connection,
                    seuil.domaine_turpe,
                    puissance_kva=seuil.puissance_mw * 1000,
                    conso_mwh=conso_mwh,
                    points=None if courbe is None else courbe.points,
                    journee_type=journee_type,
                    code_region=territoire.code_insee_region,
                )
                if seuil is not None and seuil.domaine_turpe in DOMAINES_TURPE and conso_mwh
                else None
            )
            prix = (
                _prix(connection, conso_mwh) if conso_mwh is not None else _prix_defaut(connection)
            )
            zone_rte, zone_rte_p3 = _zones_rte(connection, territoire)
            site = _une_ligne(
                connection,
                SITE_SQL,
                {
                    "latitude": territoire.latitude,
                    "longitude": territoire.longitude,
                    "departement": departement,
                    "est_zni": bool(ligne and ligne["est_zni"]),
                },
            )
            contraintes = [
                ContrainteOuvrage(*ligne)
                for ligne in connection.execute(
                    CONTRAINTES_SQL,
                    {"departement": departement, "limite": NB_CONTRAINTES_AFFICHEES},
                ).fetchall()
            ]
            cout_ligne = _une_ligne(connection, COUT_SQL, {"profil": PROFIL_DEFAUT})
            carbone = (
                None
                if ligne is None
                else _une_ligne(connection, CARBONE_SQL, {"region": ligne["code_insee_region"]})
            )
            if carbone is not None:
                heures = connection.execute(
                    CARBONE_HORAIRE_SQL, {"region": ligne["code_insee_region"]}
                ).fetchall()
                carbone["profil_horaire"] = tuple((h, i) for _, h, i in heures)
                carbone["annee_profil"] = heures[0][0] if heures else None
            bilan = (
                None
                if courbe is None or ligne is None
                else bilan_courbe(
                    courbe,
                    connection,
                    profil_horaire=carbone["profil_horaire"] if carbone else (),
                    code_region=ligne["code_insee_region"],
                )
            )
            limites = dict(
                connection.execute(
                    "select indicateur, limite from gold.limites_methodologiques"
                ).fetchall()
            )
            chargements = lire_chargements(connection)
            accise = (
                None
                if va_eur is None and activite is None
                else _une_ligne(connection, ACCISE_SQL, {})
            )
            if accise is not None:
                accise["reduits"] = dict(connection.execute(ACCISE_REDUITS_SQL).fetchall())
            entreprises_commune = _une_ligne(
                connection, ENTREPRISES_COMMUNE_SQL, {"commune": commune}
            )
            # Seule une adresse au numéro près se compare à celles d'Enedis
            entreprises_adresse = (
                _une_ligne(
                    connection,
                    ENTREPRISES_ADRESSE_SQL,
                    {"commune": commune, "adresse": adresse_normalisee(territoire.voie)},
                )
                if territoire.voie and territoire.precision == "housenumber"
                else None
            )
            plans = (
                _plans(connection, {territoire.code_insee_commune, commune})
                if "gaspar_plans" in chargements
                else None
            )
            continuite = (
                _une_ligne(connection, CONTINUITE_SQL, {"departement": departement})
                if "enedis_continuite_alimentation" in chargements
                else None
            )
            prix_locaux = (
                _une_ligne(connection, LOCAUX_SQL, {"commune": commune, "departement": departement})
                if "ventes_locaux" in chargements
                else None
            )
            recrutements = (
                _une_ligne(connection, RECRUTEMENTS_SQL, {"departement": departement})
                if "recrutements_bmo" in chargements
                else None
            )
            poles_proches = connection.execute(
                POLES_PROCHES_SQL,
                {
                    "latitude": territoire.latitude,
                    "longitude": territoire.longitude,
                    "departement": departement,
                    "est_zni": bool(ligne and ligne["est_zni"]),
                },
            ).fetchall()
            friches_proches = (
                [
                    Friche(*ligne)
                    for ligne in connection.execute(
                        FRICHES_SQL,
                        {"latitude": territoire.latitude, "longitude": territoire.longitude},
                    ).fetchall()
                ]
                if "friches" in chargements
                else None
            )
            reseaux_chaleur = (
                [
                    ReseauChaleur(*ligne)
                    for ligne in connection.execute(
                        RESEAUX_CHALEUR_SQL,
                        {"latitude": territoire.latitude, "longitude": territoire.longitude},
                    ).fetchall()
                ]
                if "reseaux_chaleur" in chargements
                else None
            )
            zones_repartition = (
                [
                    ZoneRepartition(nom, ressource)
                    for nom, ressource, geometrie in connection.execute(
                        ZRE_SQL,
                        {"latitude": territoire.latitude, "longitude": territoire.longitude},
                    ).fetchall()
                    if contient(json.loads(geometrie), territoire.longitude, territoire.latitude)
                ]
                if "zones_repartition_eaux" in chargements
                else None
            )
    except duckdb.CatalogException as error:
        raise DonneesAbsentesError(
            f"couche gold incomplete : {str(error).splitlines()[0]}"
        ) from error

    if ligne is None:
        raise DiagnosticIndisponibleError(
            f"departement {departement!r} absent de gold.fct_diagnostic_territoire"
        )
    return Diagnostic(
        territoire=territoire,
        code_insee_commune=commune,
        seuil=seuil,
        conso_mwh=conso_mwh,
        ca_eur=ca_eur,
        va_eur=va_eur,
        prix=prix,
        contraintes=contraintes,
        zone_rte=zone_rte,
        zone_rte_p3=zone_rte_p3,
        site_cle_en_main=None if site is None else SiteCleEnMain(**site),
        cout=None if cout_ligne is None else CoutProfil(**cout_ligne),
        carbone=None if carbone is None else Carbone(**carbone),
        limites=limites,
        chargements=chargements,
        courbe=bilan,
        plans_prevention=plans,
        accise=None if accise is None else Accise(**accise),
        entreprises_commune=None
        if entreprises_commune is None
        else EntreprisesCommune(**entreprises_commune),
        entreprises_adresse=None
        if entreprises_adresse is None
        else EntreprisesAdresse(**entreprises_adresse),
        acheminement=acheminement,
        continuite=None if continuite is None else Continuite(**continuite),
        prix_locaux=None if prix_locaux is None else PrixLocaux(**prix_locaux),
        surface_m2=surface_m2,
        activite=activite,
        poles_proches=poles_proches,
        nb_friches=None if friches_proches is None else len(friches_proches),
        friches=(friches_proches or [])[:NB_FRICHES_AFFICHEES],
        reseaux_chaleur=reseaux_chaleur,
        zones_repartition=zones_repartition,
        recrutements=None if recrutements is None else Recrutements(**recrutements),
        **ligne,
    )


def _plans(connection: duckdb.DuckDBPyConnection, codes: set[str]) -> list[PlanPrevention]:
    resultat = connection.execute(PLANS_SQL, {"codes": sorted(codes)})
    colonnes = [d[0] for d in resultat.description]
    plans: dict[str, PlanPrevention] = {}
    for row in resultat.fetchall():
        plan = PlanPrevention(**dict(zip(colonnes, row, strict=True)))
        plans.setdefault(plan.code_procedure, plan)
    return list(plans.values())


def _une_ligne(
    connection: duckdb.DuckDBPyConnection, sql: str, parametres: dict[str, Any]
) -> dict[str, Any] | None:
    result = connection.execute(sql, parametres)
    row = result.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in result.description], row, strict=True))


def _seuil(
    connection: duckdb.DuckDBPyConnection, puissance_mw: float, departement: str | None = None
) -> Seuil | None:
    if puissance_mw < 0:
        raise ValueError("la puissance du projet doit etre positive")
    ligne = _une_ligne(
        connection, SEUIL_SQL, {"puissance": puissance_mw, "departement": departement}
    )
    return None if ligne is None else Seuil(puissance_mw=puissance_mw, **ligne)


def _prix(connection: duckdb.DuckDBPyConnection, conso_mwh: float) -> PrixEntreprise | None:
    if conso_mwh <= 0:
        raise ValueError("la consommation annuelle doit etre strictement positive")
    ligne = _une_ligne(connection, PRIX_SQL, {"conso": conso_mwh})
    return None if ligne is None else PrixEntreprise(conso_mwh=conso_mwh, **ligne)


def _prix_defaut(connection: duckdb.DuckDBPyConnection) -> PrixEntreprise | None:
    ligne = _une_ligne(connection, PRIX_DEFAUT_SQL, {"code": TRANCHE_DEFAUT})
    return None if ligne is None else PrixEntreprise(conso_mwh=None, **ligne)


# 8 784 heures dans une année bissextile : au-delà, la puissance ne permet pas la consommation
HEURES_AN_MAX = 8784


def incoherence_projet(puissance_mw: float | None, conso_mwh: float | None) -> str | None:
    if puissance_mw is None or conso_mwh is None:
        return None
    plafond = puissance_mw * HEURES_AN_MAX
    if conso_mwh <= plafond:
        return None
    return (
        f"consommation de {nombre_fr(conso_mwh)} MWh par an impossible avec {puissance_mw:g} MW :"
        f" au plus {nombre_fr(plafond)} MWh"
    )


# Zone P1 ou P2 la plus proche, et une zone P3 seulement si elle l'est davantage
def _zones_rte(
    connection: duckdb.DuckDBPyConnection, territoire: Territoire
) -> tuple[ZoneRte | None, ZoneRte | None]:
    zones = [
        ZoneRte(*ligne)
        for ligne in connection.execute(
            ZONES_SQL, {"latitude": territoire.latitude, "longitude": territoire.longitude}
        ).fetchall()
    ]
    prioritaire = next((z for z in zones if z.priorite in ("P1", "P2")), None)
    p3 = next((z for z in zones if z.priorite == "P3"), None)
    if prioritaire is not None and p3 is not None and p3.distance_km >= prioritaire.distance_km:
        p3 = None
    return prioritaire, p3
