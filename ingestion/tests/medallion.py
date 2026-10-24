from __future__ import annotations

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
    CONSO_IRIS,
    CONTRAINTES_OUVRAGES,
    CONTRAINTES_REGIONALES,
    EDF_SEI_PRODUCTION,
    EQUILIBRE_REGIONAL,
    EUROSTAT_PRIX_ENTREPRISES,
    GASPAR_RISQUES,
    NATIONAL_CONS_DEF,
    NATIONAL_TR,
    POSTES_RTE,
    RTE_TEMPO,
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
    load_bronze(database, tmp_path, EDF_SEI_PRODUCTION, edf_sei_production())
    ensure_bronze_tables(settings=Settings(_env_file=None, duckdb_path=database))
    dbt_build(database, tmp_path)
    return database
