from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ingestion.config import Settings
from ingestion.datasets import (
    AEROPORTS_FRET,
    CHANTIERS_RAIL_ROUTE,
    COMMUNES_CENTRES,
    CONSO_IRIS,
    CONTINUITE_ALIMENTATION,
    CONTRAINTES_OUVRAGES,
    CONTRAINTES_REGIONALES,
    EDF_SEI_PRODUCTION,
    EMBER_INTENSITE_CARBONE,
    ENEDIS_CONSO_ENTREPRISES,
    EQUILIBRE_REGIONAL,
    EUROSTAT_PRIX_ENTREPRISES,
    EUROSTAT_PRIX_UE,
    GASPAR_PLANS,
    GASPAR_RISQUES,
    NATIONAL_CONS_DEF,
    NATIONAL_TR,
    PORTS_FRET,
    POSTES_RTE,
    RTE_TEMPO,
    SITES_PRODUCTION,
    VENTES_LOCAUX,
    ZONAGE_SISMIQUE,
    DatasetSpec,
)
from ingestion.pipeline import ensure_bronze_tables
from ingestion.transform import PARIS_TZ, records_to_table, write_parquet
from ingestion.warehouse import DuckDBLoader

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"
DBT_BIN = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt")

# Puissances constantes (MW) : les énergies attendues se calculent de tête
MESURES = {
    "consommation": 50_000.0,
    "nucleaire": 40_000.0,
    "eolien": 5_000.0,
    "solaire": 1_000.0,
    "hydraulique": 3_000.0,
    "gaz": 1_000.0,
    "fioul": 0.0,
    "charbon": 0.0,
    "bioenergies": 500.0,
    "pompage": -500.0,
    "ech_physiques": -500.0,
    "taux_co2": 20.0,
}
PRODUCTION_TOTALE = 50_500.0
PRODUCTION_BAS_CARBONE = 49_500.0

# Jour de retour à l'heure d'hiver : 25 h en heure de Paris
JOUR_25H = date(2025, 10, 26)
# Dernier jour couvert par le consolidé, recouvert ensuite par le temps réel
JOUR_BASCULE = date(2026, 6, 30)

# Pic de carbone placé en soirée du jour de bascule (heures locales 19 h et 20 h)
PIC_CO2 = 80.0
HEURES_PIC = (19, 20)

# Calendrier Tempo : la veille du jour de bascule est bleue, le jour lui-même rouge
TEMPO = {
    date(2025, 10, 25): "BLUE",
    JOUR_25H: "BLUE",
    date(2026, 6, 29): "BLUE",
    JOUR_BASCULE: "RED",
    date(2026, 7, 1): "WHITE",
}


def quarts_d_heure(start: datetime, end: datetime) -> Iterator[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(minutes=15)


def jour_utc(jour: date) -> tuple[datetime, datetime]:
    start = datetime(jour.year, jour.month, jour.day, tzinfo=PARIS_TZ)
    lendemain = jour + timedelta(days=1)
    end = datetime(lendemain.year, lendemain.month, lendemain.day, tzinfo=PARIS_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


def _record(instant: datetime, nature: str, *, mesure: bool, **overrides: float) -> dict[str, Any]:
    record: dict[str, Any] = {
        "date_heure": instant.isoformat(),
        "perimetre": "France",
        "nature": nature,
    }
    if mesure:
        record |= MESURES | overrides
        if instant.astimezone(PARIS_TZ).date() == JOUR_BASCULE and (
            instant.astimezone(PARIS_TZ).hour in HEURES_PIC
        ):
            record["taux_co2"] = PIC_CO2
    return record


# Mesures à :00 et :30 seulement, lignes vides à :15 et :45 (comme l'API)
def consolide() -> list[dict[str, Any]]:
    records = []
    for jour, nature in ((JOUR_25H, "Données définitives"), (JOUR_BASCULE, "Données consolidées")):
        for instant in quarts_d_heure(*jour_utc(jour)):
            records.append(_record(instant, nature, mesure=instant.minute in (0, 30)))
    return records


# Recouvre la fin du consolidé, puis le prolonge ; dernière ligne « à blanc »
def temps_reel() -> list[dict[str, Any]]:
    start = datetime(2026, 6, 30, 20, 0, tzinfo=UTC)
    end = datetime(2026, 7, 1, 2, 0, tzinfo=UTC)
    instants = list(quarts_d_heure(start, end))
    # Une consommation distincte permet de reconnaître la source dans le résultat
    return [
        _record(instant, "Données temps réel", mesure=instant != instants[-1], consommation=45_000)
        for instant in instants
    ]


# Format renvoyé par `RteTempoClient.fetch_calendar` : minuit de Paris + couleur
def tempo() -> list[dict[str, Any]]:
    return [
        {"date_heure": datetime(j.year, j.month, j.day, tzinfo=PARIS_TZ).isoformat(), "couleur": c}
        for j, c in sorted(TEMPO.items())
    ]


# Année complète, retenue comme millésime de référence : 2022 a du secret statistique
ANNEE_REFERENCE = 2021


# `commune` = (code commune, nom, code département, code région), ou rien
def _iris(
    annee: int,
    code_iris: str,
    conso: float | None,
    sites: float | None,
    *,
    commune: tuple[str, str, str, str] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "annee": str(annee),
        "code_iris": code_iris,
        "consommation_electricite_rte": conso,
        "pdl_electricite_rte": sites,
    }
    if commune is not None:
        code_commune, nom, departement, region = commune
        record |= {
            "code_insee_commune": code_commune,
            "commune": nom,
            "code_insee_departement": departement,
            "code_insee_region": region,
        }
    return record


LYON = ("69123", "Lyon", "69", "84")
VILLEURBANNE = ("69266", "Villeurbanne", "69", "84")
AJACCIO = ("2A004", "Ajaccio", "2A", "94")
SAINT_MAURICE = ("01376", "Saint-Maurice-de-Beynost", "01", "84")


# MWh par an
def conso_iris() -> list[dict[str, Any]]:
    return [
        # 2021, millésime complet
        _iris(2021, "693830101", 40_000.0, 1.0, commune=LYON),
        _iris(2021, "692660000", 60_000.0, 2.0, commune=VILLEURBANNE),
        _iris(2021, "20030000", 10_000.0, 1.0),
        _iris(2021, "751010101", 5_000.0, 1.0),
        _iris(2021, "2A0040000", 3_000.0, 1.0, commune=AJACCIO),
        # 2022, plus récent mais partiellement masqué
        _iris(2022, "693830101", None, None, commune=LYON),
        _iris(2022, "692660000", 99_000.0, 2.0, commune=VILLEURBANNE),
        _iris(2022, "013760000", 7_000.0, 1.0, commune=SAINT_MAURICE),
        _iris(2022, "13760000", None, None),
    ]


# 13 mois, de décembre 2024 à décembre 2025 : la fenêtre n'en retient que 12
def equilibre_regional() -> list[dict[str, Any]]:
    regions = {"84": 200.0, "11": 10.0, "94": 70.0}
    mois = ["2024-12"] + [f"2025-{m:02d}" for m in range(1, 13)]
    return [
        {
            "mois": m,
            "code_insee_region": code_region,
            "production_totale": 1e9 if m == "2024-12" else production,
            "consommation_brute": 100.0,
        }
        for m in mois
        for code_region, production in regions.items()
    ]


def contraintes_regionales() -> list[dict[str, Any]]:
    return [
        {
            "region": "AUVERGNE-RHÔNE-ALPES",
            "puissance_enr_installee": 14_575.0,
            "puissance_totale_a_compenser": 187.0,
            "energie_non_evacuee_moyenne_printemps": 222.0,
            "energie_non_evacuee_moyenne_ete": 2_150.0,
            "energie_non_evacuee_moyenne_automne": 155.0,
            "energie_non_evacuee_moyenne_hiver": 250.0,
        },
        {
            "region": "ÎLE-DE-FRANCE",
            "puissance_enr_installee": 786.0,
            "puissance_totale_a_compenser": 0.0,
            "energie_non_evacuee_moyenne_printemps": 0.0,
            "energie_non_evacuee_moyenne_ete": 0.0,
            "energie_non_evacuee_moyenne_automne": 0.0,
            "energie_non_evacuee_moyenne_hiver": 0.0,
        },
    ]


# Tranche IB : 20 semestres de 2016-S1 à 2025-S2
PRIX_IB_DEPART = 0.10
PRIX_IB_PAS = 0.005
TAXES_IB = 0.02
# Tranche IA (moins de 20 MWh par an), prix constant sur 2 ans
PRIX_IA = 0.25


# 40 semestres de 2006-S1 à 2025-S2 : +2 % par semestre, un bruit déterministe de ±10 % au plus
def _serie_avec_crise(debut_crise: int) -> list[float]:
    return [
        0.10
        * 1.02**i
        * (1.3 if debut_crise <= i < debut_crise + 4 else 1.0)
        * (1 + 0.05 * ((i * 7) % 5 - 2))
        for i in range(40)
    ]


# Tranche ID : crise tardive (2021-2022)
PRIX_ID = _serie_avec_crise(30)
# Tranche IE : même série, crise ancienne (2016-2017)
PRIX_IE = _serie_avec_crise(20)


# `semestres(2024, 2025)` -> 2024-S1, 2024-S2, 2025-S1, 2025-S2
def semestres(premier: int, dernier: int) -> list[str]:
    return [f"{annee}-S{n}" for annee in range(premier, dernier + 1) for n in (1, 2)]


# IB sur 10 ans, IA et IG sur 2 ans : trop peu pour des scénarios
def prix_entreprises() -> list[dict[str, Any]]:
    series = {
        "MWH_LT20": [(semestre, PRIX_IA, 0.03) for semestre in semestres(2024, 2025)],
        "MWH20-499": [
            (semestre, PRIX_IB_DEPART + PRIX_IB_PAS * i, TAXES_IB)
            for i, semestre in enumerate(semestres(2016, 2025))
        ],
        "MWH_GE150000": [(semestre, 0.07, 0.001) for semestre in semestres(2024, 2025)],
        "MWH2000-19999": [
            (semestre, prix, 0.01)
            for semestre, prix in zip(semestres(2006, 2025), PRIX_ID, strict=True)
        ],
        "MWH20000-69999": [
            (semestre, prix, 0.005)
            for semestre, prix in zip(semestres(2006, 2025), PRIX_IE, strict=True)
        ],
    }
    return [
        {
            "semestre": semestre,
            "tranche": tranche,
            "fiscalite": fiscalite,
            "libelle_tranche": tranche,
            "statut": None,
            "prix_eur_kwh": prix if fiscalite == "X_VAT" else prix - taxes,
        }
        for tranche, lignes in series.items()
        for semestre, prix, taxes in lignes
        for fiscalite in ("X_TAX", "X_VAT")
    ]


def _poste(code: str, tension: str, departement: str, etat: str = "EN EXPLOITATION") -> dict:
    return {
        "code_poste": code,
        "nom_poste": code,
        "fonction": "Poste de transformation",
        "etat": etat,
        "tension": tension,
        "departement": departement,
    }


# Rhône : 3 postes en exploitation (HTB1, HTB2, HTB3) et 1 en projet ; Ain : 1 poste
def postes_rte() -> list[dict[str, Any]]:
    return [
        _poste("LYON1", "63kV", "Rhône"),
        _poste("LYON2", "225kV", "Rhône"),
        _poste("LYON3", "400kV", "RHONE"),
        _poste("LYON4", "90kV", "Rhône", etat="ACCORD ADMINISTRATIF"),
        _poste("BOURG", "63kV", "Ain"),
        _poste("PIQUA", "<45kV", ""),
    ]


def _contrainte(
    ouvrage: str,
    postes: tuple[str | None, str | None, str | None],
    occurrence: str,
    perennite: str,
    puissance: float,
    *,
    en_journee: bool = False,
) -> dict[str, Any]:
    return {
        "ouvrage": ouvrage,
        "poste_1": postes[0],
        "poste_2": postes[1],
        "poste_3": postes[2],
        "region": "AUVERGNE-RHÔNE-ALPES",
        "nom_de_l_ouvrage": f"LIAISON {ouvrage}",
        "puissance_max_de_l_ouvrage": puissance,
        "pourcentage_1": -50.0,
        "pourcentage_2": None,
        "pourcentage_3": None,
        "specificite": "Contrainte en journée" if en_journee else None,
        "occurrence": occurrence,
        "duree": "]4h-6h]",
        "perennite": perennite,
    }


TRES_FORTE = "Très forte : plus de 150 fois par an"
FORTE = "Forte : entre 75 et 150 fois par an"
FAIBLE = "Faible : entre 5 et 25 fois par an"


# Rhône : 3 contraintes, dont une liaison vers l'Ain et un ouvrage à deux contraintes
def contraintes_ouvrages() -> list[dict[str, Any]]:
    return [
        # Liaison Ain-Rhône : comptée dans les deux départements
        _contrainte("BOURGLYON1", ("BOURG", "LYON1", None), FORTE, "ELEVEE", 80.0),
        # Même ouvrage, deux contraintes qui diffèrent par leurs postes
        _contrainte("LYON2LYON3", ("LYON2", "LYON3", None), FAIBLE, "FAIBLE", 40.0),
        _contrainte(
            "LYON2LYON3", ("LYON2", "LYON3", "LYON1"), TRES_FORTE, "MOYENNE", 60.0, en_journee=True
        ),
        # Postes inconnus : contrainte rattachée à aucun département
        _contrainte("ORPHELINE", ("XXXXX", None, None), FAIBLE, "FAIBLE", 10.0),
    ]


# Lyon et Ajaccio ; une ligne d'un export plus ancien, retirée depuis, et un « Autre risque »
def gaspar_risques() -> list[dict[str, Any]]:
    lignes = [
        ("69123", "Lyon", "11", "Inondation", "2026-10-05"),
        ("69123", "Lyon", "112", "Par une crue à débordement lent de cours d'eau", "2026-10-05"),
        ("69123", "Lyon", "24", "Transport de marchandises dangereuses", "2026-10-05"),
        ("69123", "Lyon", "AUTRE", "Autre risque", "2026-10-05"),
        ("69123", "Lyon", "13", "Séisme", "2025-01-02"),
        ("2A004", "Ajaccio", "16", "Feu de forêt", "2026-10-05"),
    ]
    return [
        {
            "code_commune": commune,
            "libelle_commune": nom,
            "num_risque": numero,
            "libelle_risque": libelle,
            "millesime": millesime,
        }
        for commune, nom, numero, libelle, millesime in lignes
    ]


# Lyon (4,85 ; 45,76) dans un carré en zone 2, dont l'emprise touche aussi un triangle en zone 5 qui
# ne le contient pas ; Ajaccio (8,7 ; 41,9) dans un carré en zone 4 percé d'un trou en zone 1
def zonage_sismique() -> list[dict[str, Any]]:
    carre_lyon = [[[4, 45], [6, 45], [6, 47], [4, 47], [4, 45]]]
    triangle = [[[4.5, 45.5], [5.5, 46.5], [4.5, 46.5], [4.5, 45.5]]]
    trou = [[9.0, 42.1], [9.2, 42.1], [9.2, 42.3], [9.0, 42.3], [9.0, 42.1]]
    carre_corse = [[[8.5, 41.5], [9.5, 41.5], [9.5, 42.5], [8.5, 42.5], [8.5, 41.5]], trou]
    polygones = [
        ("1", "2 - FAIBLE", [carre_lyon]),
        ("2", "5 - FORTE", [triangle]),
        ("3", "4 - MOYENNE", [carre_corse]),
        ("4", "1 - TRES FAIBLE", [[trou]]),
    ]
    return [
        {
            "gid": gid,
            "zone_sismicite": libelle[0],
            "libelle_zone": libelle,
            "geometrie": json.dumps(geometrie),
            "lon_min": min(x for p in geometrie for a in p for x, _ in a),
            "lat_min": min(y for p in geometrie for a in p for _, y in a),
            "lon_max": max(x for p in geometrie for a in p for x, _ in a),
            "lat_max": max(y for p in geometrie for a in p for _, y in a),
        }
        for gid, libelle, geometrie in polygones
    ]


# Lyon : un PPRI approuvé (deux aléas), un PPRT approuvé, un plan caduc, un plan d'un ancien
# export ; Ajaccio : un plan seulement prescrit ; Paris, Lille et l'outre-mer : aucun plan
def gaspar_plans() -> list[dict[str, Any]]:
    lignes = [
        ("69123", "PPRI1", "112", "PPRN", "PPRI du Grand Lyon", "Inondation", "Opposable",
         "2004-01-07", "2009-03-02", "2026-10-05"),
        ("69123", "PPRI1", "115", "PPRN", "PPRI du Grand Lyon", "Inondation", "Opposable",
         "2004-01-07", "2009-03-02", "2026-10-05"),
        ("69123", "PPRT1", "211", "PPRT", "Vallée de la chimie", "Risque industriel", "Opposable",
         "2015-04-21", "2016-10-19", "2026-10-05"),
        ("69123", "ABROGE", "112", "PPRN", "Ancien PPRI", "Inondation", "Caduque",
         "1990-01-01", "1995-01-01", "2026-10-05"),
        ("69123", "RETIRE", "13", "PPRN", "Plan retiré depuis", "Séisme", "Prescrit",
         "2020-01-01", None, "2025-01-02"),
        ("2A004", "PPRIF", "", "PPRN", "PPRIF d'Ajaccio", "Feu de forêt", "Prescrit",
         "2007-01-11", None, "2026-10-05"),
    ]  # fmt: skip
    return [
        {
            "code_commune": commune,
            "code_procedure": procedure,
            "code_risque": risque,
            "type_plan": type_plan,
            "libelle_plan": libelle,
            "libelle_risque": famille,
            "libelle_alea": None,
            "etat": etat,
            "sous_etat": "Approuvé" if etat == "Opposable" else etat,
            "date_prescription": prescription,
            "date_approbation": approbation,
            "millesime": millesime,
        }
        for (commune, procedure, risque, type_plan, libelle, famille, etat, prescription,
             approbation, millesime) in lignes
    ]  # fmt: skip


# Corse, année 2025 complète : thermique triplé de 17 h à 20 h UTC, la pointe du soir
def edf_sei_production() -> list[dict[str, Any]]:
    debut = datetime(2025, 1, 1, tzinfo=UTC)
    lignes = []
    for n in range(365 * 24):
        instant = debut + timedelta(hours=n)
        lignes.append(
            {
                "territoire": "Corse",
                "date_heure": instant.isoformat(),
                "statut": "Estimé",
                "production_totale_mw": 200.0,
                "thermique_mw": 150.0 if 17 <= instant.hour <= 20 else 50.0,
                "importations_mw": 50.0,
            }
        )
    return lignes


# Italie et France, tranche IB : la moyenne des deux derniers semestres vaut 0,26 et 0,20
def prix_ue() -> list[dict[str, Any]]:
    series = {"IT": (0.24, 0.25, 0.27), "FR": (0.18, 0.19, 0.21)}
    return [
        {
            "pays": pays,
            "semestre": semestre,
            "tranche": "MWH20-499",
            "fiscalite": "X_VAT",
            "libelle_tranche": "tranche IB",
            "statut": None,
            "prix_eur_kwh": prix,
        }
        for pays, valeurs in series.items()
        for semestre, prix in zip(("2025-S1", "2025-S2", "2026-S1"), valeurs, strict=True)
    ]


# La dernière année compte : France 40 g en 2025 (50 g en 2024), Italie 300 g
def ember_intensites() -> list[dict[str, Any]]:
    lignes = [("FRA", "2024", 50.0), ("FRA", "2025", 40.0), ("ITA", "2025", 300.0)]
    return [
        {
            "iso3": iso3,
            "annee": annee,
            "pays_ember": iso3,
            "production_twh": 100.0,
            "intensite_g_co2e_kwh": intensite,
        }
        for iso3, annee, intensite in lignes
    ]


# Pôles de production : un gros site par réseau, et ce que le gold doit écarter
def sites_production() -> list[dict[str, Any]]:
    lignes = [
        # Arrondissement de Lyon : rattaché à la commune 69123
        ("69383", "Lyon 3e Arrondissement", "69", "Nucléaire", 3600.0),
        ("69123", "Lyon", "69", "Hydraulique", 150.0),
        ("69123", "Lyon", "69", "Hydraulique", 120.0),
        # Continent sous 200 MW, stockage, commune absente, collectivité hors carte : écartés
        ("01001", "L'Abergement", "01", "Eolien", 180.0),
        ("01001", "L'Abergement", "01", "Stockage non hydraulique", 400.0),
        ("", "", "69", "Thermique non renouvelable", 900.0),
        ("97501", "Miquelon", "975", "Thermique non renouvelable", 22.0),
        # Corse : 20 MW suffisent
        ("2A004", "Ajaccio", "2A", "Thermique non renouvelable", 120.0),
    ]
    return [
        {
            "code_commune": commune,
            "commune": nom if i != 2 else "Lyon (bis)",
            "code_departement": departement,
            "filiere": filiere,
            "puissance_mw": mw,
            "energie_gwh": mw * 4,
            "nb_installations": 1,
        }
        for i, (commune, nom, departement, filiere, mw) in enumerate(lignes)
    ]


def communes_centres() -> list[dict[str, Any]]:
    return [
        {
            "code_commune": code,
            "nom": nom,
            "code_departement": dep,
            "longitude": lon,
            "latitude": lat,
        }
        for code, nom, dep, lon, lat in [
            ("69123", "Lyon", "69", 4.8351, 45.758),
            ("01001", "L'Abergement-Clémenciat", "01", 4.9306, 46.1517),
            ("2A004", "Ajaccio", "2A", 8.7369, 41.9267),
            ("97501", "Miquelon-Langlade", "975", -56.38, 47.1),
            ("76351", "Le Havre", "76", 0.1077, 49.4938),
            ("34301", "Sète", "34", 3.6969, 43.4028),
            ("69299", "Colombier-Saugnieu", "69", 5.1036, 45.7175),
            ("44020", "Bouguenais", "44", -1.6232, 47.1777),
        ]
    ]


# Ports : HAROPA retenu sur sa dernière année, Sète sous 5 Mt écarté, Ajaccio retenu dès 0,5 Mt
def ports_fret() -> list[dict[str, Any]]:
    return [
        {
            "locode": locode, "annee": annee, "port": port, "statut": "GPM",
            "marchandises_t": mt * 1e6, "tonnage_total_t": mt * 1.1e6, "conteneurs_evp": evp,
        }
        for locode, annee, port, mt, evp in [
            ("FR001", "2024", "HAROPA", 90.0, 3e6),
            ("FR001", "2025", "HAROPA", 78.0, 3.2e6),
            ("FRSET", "2025", "SETE", 4.9, 0.0),
            ("FRAJA", "2025", "AJACCIO", 0.6, 0.0),
        ]
    ]  # fmt: skip


# Aéroports : 2026 incomplète ignorée ; Nantes sous 20 000 t écarté, Ajaccio retenu dès 3 000 t
def aeroports_fret() -> list[dict[str, Any]]:
    return [
        {
            "code_oaci": oaci, "annee": annee, "aeroport": nom, "zone": zone,
            "fret_t": tonnes, "vols_cargo": 100.0, "nb_mois": mois,
        }
        for oaci, annee, nom, zone, tonnes, mois in [
            ("LFLL", "2025", "LYON-ST-EXUPERY", "MT", 52_523.0, 12.0),
            ("LFLL", "2026", "LYON-ST-EXUPERY", "MT", 90_000.0, 3.0),
            ("LFKJ", "2025", "AJACCIO-NAPOLEON-BONAPARTE", "MT", 6_793.0, 12.0),
            ("LFRS", "2025", "NANTES-ATLANTIQUE", "MT", 11_009.0, 12.0),
            ("ZZMT", "2025", "AUTRES APT (MT)", "MT", 40.0, 12.0),
        ]
    ]  # fmt: skip


# Chantiers rail-route : retenus dès 2 km de voies, Cognac écarté
def chantiers_rail_route() -> list[dict[str, Any]]:
    return [
        {
            "chantier": nom, "ville": ville, "code_postal": cp, "manutention": engins,
            "longueur_m": metres, "latitude": lat, "longitude": lon,
        }
        for nom, ville, cp, engins, metres, lat, lon in [
            ("LYON VENISSIEUX", "VENISSIEUX", "69200", "Grue(s)", 2630.0, 45.697, 4.903),
            ("NOISY-LE-SEC", "NOISY-LE-SEC", "93130", "1 Portique", 3595.0, 48.89, 2.46),
            ("COGNAC", "COGNAC", "16100", "1 Portique", 440.0, 45.69, -0.33),
        ]
    ]  # fmt: skip


# Lyon : deux lignes à la même adresse (casse différente) et une ligne sans adresse ;
# Villeurbanne plus industrielle ; l'année 2024 doit être ignorée par le gold
def conso_entreprises() -> list[dict[str, Any]]:
    return [
        {
            "annee": str(annee),
            "code_commune": commune,
            "tri_des_adresses": tri,
            "code_iris": iris,
            "adresse": adresse,
            "nombre_de_sites": sites,
            "consommation_annuelle_totale_de_ladresse_mwh": mwh,
            "code_grand_secteur": secteur,
            "code_secteur_naf2": naf,
        }
        for annee, commune, tri, iris, adresse, sites, mwh, secteur, naf in [
            (2024, "69123", "1", "691230101", "9 Rue Ampère", 9, 9_000.0, "INDUSTRIE", "24"),
            (2025, "69123", "1", "691230101", "9 Rue Ampère", 2, 300.0, "INDUSTRIE", "24"),
            (2025, "69123", "2", "691230101", "9 RUE AMPERE", 1, 50.0, "TERTIAIRE", "47"),
            (2025, "69123", "3", "691230102", "13 B Rue Jouffroy d'Abbans", 1, 20.0,
             "TERTIAIRE", "62"),
            (2025, "69123", "4", "691230102", None, 1, 5.0, "INCONNU", None),
            (2025, "69266", "1", "692660101", "1 Rue du Test", 4, 1_000.0, "INDUSTRIE", "20"),
        ]
    ]  # fmt: skip


# Rhône sous le seuil malgré une mauvaise année, Ain au-dessus, Isère en retard d'une année
def continuite_alimentation() -> list[dict[str, Any]]:
    return [
        {"code_departement": dep, "annee": str(annee), "part_mal_alimentes_pct": pct}
        for dep, annee, pct in [
            ("69", 2015, 9.0), ("69", 2020, 1.0), ("69", 2021, 2.0), ("69", 2022, 4.0),
            ("69", 2023, 1.5), ("69", 2024, 2.5),
            ("01", 2023, 2.0), ("01", 2024, 4.0),
            ("13", 2024, 0.5),
            ("38", 2023, 8.0),
        ]
    ]  # fmt: skip


# Ventes de locaux : Lyon fiable (arrondissement ramené à la commune), Bourg à deux ventes
# seulement ; écartés : logement dans l'acte, deux dispositions, prix aberrant, vente de plus
# de cinq ans
def ventes_locaux() -> list[dict[str, Any]]:
    def vente(idm, date, commune, dep, valeur, surface, hab=0.0, disp=1.0):
        return {
            "id_mutation": idm,
            "date_mutation": date,
            "nature_mutation": "Vente",
            "code_commune": commune,
            "code_departement": dep,
            "valeur_fonciere": valeur,
            "surface_bati_m2": surface,
            "nb_locaux_activite": 1.0,
            "nb_locaux_habitation": hab,
            "nb_dispositions": disp,
            "latitude": 45.75,
            "longitude": 4.85,
        }

    return [
        vente("L1", "2025-03-01", "69383", "69", 200_000.0, 100.0),
        vente("L2", "2025-04-01", "69123", "69", 250_000.0, 100.0),
        vente("L3", "2025-05-01", "69123", "69", 300_000.0, 100.0),
        vente("L4", "2025-06-01", "69123", "69", 350_000.0, 100.0),
        vente("L5", "2025-07-01", "69123", "69", 400_000.0, 100.0),
        vente("L6", "2024-01-01", "69123", "69", 450_000.0, 100.0),
        vente("L7", "2025-08-01", "69123", "69", 900_000.0, 100.0, hab=1.0),
        vente("L8", "2025-08-02", "69123", "69", 900_000.0, 100.0, disp=2.0),
        vente("L9", "2025-08-03", "69123", "69", 90_000_000.0, 100.0),
        vente("L10", "2018-01-01", "69123", "69", 50_000.0, 100.0),
        vente("B1", "2025-02-01", "01053", "01", 120_000.0, 80.0),
        vente("B2", "2025-09-01", "01053", "01", 160_000.0, 80.0),
    ]


def load_bronze(
    database: Path, tmp_path: Path, spec: DatasetSpec, records: list[dict[str, Any]]
) -> None:
    ingested_at = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)
    table = records_to_table(records, spec=spec, ingested_at=ingested_at)
    path = write_parquet(
        table,
        dataset_dir=spec.bronze_dataset_dir(tmp_path / "bronze"),
        run_id="test",
        ingest_date=ingested_at.date(),
    )
    with DuckDBLoader(database, spec=spec) as loader:
        loader.merge_parquet(path)


def dbt_build(database: Path, tmp_path: Path) -> None:
    assert DBT_BIN is not None, "dbt non installé"
    result = subprocess.run(
        [
            DBT_BIN, "build",
            "--project-dir", str(DBT_DIR),
            "--profiles-dir", str(DBT_DIR),
            "--target-path", str(tmp_path / "target"),
            "--log-path", str(tmp_path / "logs"),
        ],
        env={**os.environ, "ECO2MIX_DUCKDB_PATH": str(database)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, f"dbt build en échec :\n{result.stdout}\n{result.stderr}"


# Bronze chargé par l'ingestion, silver et gold construits par `dbt build`
def build_warehouse(tmp_path: Path, *, with_tempo: bool = True) -> Path:
    database = tmp_path / "eco2mix.duckdb"
    load_bronze(database, tmp_path, NATIONAL_CONS_DEF, consolide())
    load_bronze(database, tmp_path, NATIONAL_TR, temps_reel())
    if with_tempo:
        load_bronze(database, tmp_path, RTE_TEMPO, tempo())
    load_bronze(database, tmp_path, CONSO_IRIS, conso_iris())
    load_bronze(database, tmp_path, EQUILIBRE_REGIONAL, equilibre_regional())
    load_bronze(database, tmp_path, CONTRAINTES_REGIONALES, contraintes_regionales())
    load_bronze(database, tmp_path, EUROSTAT_PRIX_ENTREPRISES, prix_entreprises())
    load_bronze(database, tmp_path, POSTES_RTE, postes_rte())
    load_bronze(database, tmp_path, CONTRAINTES_OUVRAGES, contraintes_ouvrages())
    load_bronze(database, tmp_path, GASPAR_RISQUES, gaspar_risques())
    load_bronze(database, tmp_path, GASPAR_PLANS, gaspar_plans())
    load_bronze(database, tmp_path, ZONAGE_SISMIQUE, zonage_sismique())
    load_bronze(database, tmp_path, EDF_SEI_PRODUCTION, edf_sei_production())
    load_bronze(database, tmp_path, EUROSTAT_PRIX_UE, prix_ue())
    load_bronze(database, tmp_path, EMBER_INTENSITE_CARBONE, ember_intensites())
    load_bronze(database, tmp_path, SITES_PRODUCTION, sites_production())
    load_bronze(database, tmp_path, COMMUNES_CENTRES, communes_centres())
    load_bronze(database, tmp_path, PORTS_FRET, ports_fret())
    load_bronze(database, tmp_path, AEROPORTS_FRET, aeroports_fret())
    load_bronze(database, tmp_path, CHANTIERS_RAIL_ROUTE, chantiers_rail_route())
    load_bronze(database, tmp_path, ENEDIS_CONSO_ENTREPRISES, conso_entreprises())
    load_bronze(database, tmp_path, CONTINUITE_ALIMENTATION, continuite_alimentation())
    load_bronze(database, tmp_path, VENTES_LOCAUX, ventes_locaux())
    ensure_bronze_tables(settings=Settings(_env_file=None, duckdb_path=database))
    dbt_build(database, tmp_path)
    return database
