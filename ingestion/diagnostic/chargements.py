"""Date de chargement de chaque source et alerte quand elle est trop ancienne."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

# Pas `transform.PARIS_TZ` : l'importer chargerait PySpark à chaque diagnostic
PARIS_TZ = ZoneInfo("Europe/Paris")

# Sources rechargées par `refresh`, dans l'ordre d'affichage ; les seeds n'y figurent pas
SOURCES_CHARGEES: dict[str, str] = {
    "eurostat_nrg_pc_205": "prix Eurostat",
    "national_tr": "éCO2mix temps réel",
    "national_cons_def": "éCO2mix consolidé",
    "consommation_annuelle_par_iris": "industrie ODRÉ",
    "enedis_conso_entreprises_adresse": "entreprises Enedis",
    "equilibre_regional_mensuel_prod_conso_brute": "équilibre régional ODRÉ",
    "contraintes_region": "contraintes RTE",
    "postes_electriques_rte": "postes RTE",
    "gaspar_risques": "risques GASPAR",
    "gaspar_plans": "plans de prévention GASPAR",
    "zonage_sismique": "zonage sismique",
    "enedis_continuite_alimentation": "continuité Enedis",
    "ventes_locaux": "ventes de locaux DVF",
    "friches": "friches Cartofriches",
    "reseaux_chaleur": "réseaux de chaleur",
    "zones_repartition_eaux": "zones de répartition des eaux",
    "diplomes_commune": "diplômes Insee",
    "recrutements_bmo": "recrutements BMO",
}
# Au-delà, une source n'a pas été rechargée depuis plus d'un cycle mensuel de `refresh`
AGE_MAX_JOURS = 45
# Une source rechargée à part (un `refresh` partiel) n'est signalée qu'au-delà
ECART_TOLERE_JOURS = 7


def lire_chargements(connection: duckdb.DuckDBPyConnection) -> dict[str, date]:
    presentes = {
        nom
        for (nom,) in connection.execute(
            "select table_name from information_schema.tables where table_schema = 'bronze'"
        ).fetchall()
    }
    chargements = {}
    for table in SOURCES_CHARGEES:
        if table not in presentes:
            continue
        (dernier,) = connection.execute(
            f"select max(ingested_at_utc) from bronze.{table}"
        ).fetchone()
        if isinstance(dernier, datetime):
            chargements[table] = dernier.astimezone(PARIS_TZ).date()
    return chargements


def ligne_chargements(chargements: dict[str, date], *, aujourd_hui: date | None = None) -> str:
    if not chargements:
        return "Date des données inconnue (warehouse sans source chargée)."
    dernier, premier = max(chargements.values()), min(chargements.values())
    en_retard = {t: j for t, j in chargements.items() if (dernier - j).days > ECART_TOLERE_JOURS}
    if premier == dernier:
        ligne = f"Données chargées le {dernier:%d/%m/%Y}"
    elif not en_retard:
        ligne = f"Données chargées du {premier:%d/%m/%Y} au {dernier:%d/%m/%Y}"
    else:
        retard = ", ".join(f"{SOURCES_CHARGEES[t]} le {j:%d/%m/%Y}" for t, j in en_retard.items())
        ligne = f"Données chargées le {dernier:%d/%m/%Y}, sauf {retard}"
    age = ((aujourd_hui or date.today()) - premier).days
    if age > AGE_MAX_JOURS:
        ligne += f" : plus de {AGE_MAX_JOURS} jours, relancer refresh"
    return ligne + "."
