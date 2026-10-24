from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pyarrow as pa

KEY_COLUMN = "date_heure"

INGESTED_AT_COLUMN = "ingested_at_utc"
SOURCE_COLUMN = "source_dataset"


# Nature d'une colonne : fixe son type et sa normalisation à l'ingestion
class ColumnKind(StrEnum):
    # Instant daté, normalisé en UTC à l'extraction
    INSTANT = "instant"
    # Période publiée telle quelle (« 2021 », « 2014-06 »)
    PERIOD = "period"
    # Code d'identification (INSEE, IRIS) — texte, jamais un nombre
    CODE = "code"
    LABEL = "label"
    MEASURE = "measure"


_ARROW_TYPES: dict[ColumnKind, pa.DataType] = {
    ColumnKind.INSTANT: pa.timestamp("us", tz="UTC"),
    ColumnKind.PERIOD: pa.string(),
    ColumnKind.CODE: pa.string(),
    ColumnKind.LABEL: pa.string(),
    ColumnKind.MEASURE: pa.float64(),
}


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    kind: ColumnKind
    # Clé seulement : une valeur absente devient la chaîne vide au lieu d'écarter la ligne
    vide_si_absent: bool = False

    @property
    def arrow_type(self) -> pa.DataType:
        return _ARROW_TYPES[self.kind]


# Constructeurs courts, pour que les specs se lisent comme une déclaration
def instant(name: str) -> Column:
    return Column(name, ColumnKind.INSTANT)


# Raccourcis de déclaration des colonnes d'un jeu de données
def period(name: str) -> Column:
    return Column(name, ColumnKind.PERIOD)


def code(name: str, *, vide_si_absent: bool = False) -> Column:
    return Column(name, ColumnKind.CODE, vide_si_absent=vide_si_absent)


def label(name: str) -> Column:
    return Column(name, ColumnKind.LABEL)


def measures(*names: str) -> tuple[Column, ...]:
    return tuple(Column(name, ColumnKind.MEASURE) for name in names)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dataset_id: str
    keys: tuple[Column, ...]
    attributes: tuple[Column, ...]
    measures: tuple[Column, ...]
    description: str

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError(f"{self.dataset_id} : au moins une colonne de clé est requise")
        if len([c for c in self.keys if c.kind is ColumnKind.INSTANT]) > 1:
            raise ValueError(f"{self.dataset_id} : une seule colonne d'instant est acceptée")
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.dataset_id} : colonnes en double")

    @property
    def table_name(self) -> str:
        return self.dataset_id.removeprefix("eco2mix-").replace("-", "_")

    @property
    def slug(self) -> str:
        return self.dataset_id.replace("-", "_")

    def bronze_dataset_dir(self, bronze_dir: Path) -> Path:
        return bronze_dir / self.slug

    @property
    def columns(self) -> tuple[Column, ...]:
        return self.keys + self.attributes + self.measures

    @property
    def key_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.keys)

    @property
    def instant_column(self) -> str | None:
        return next((c.name for c in self.keys if c.kind is ColumnKind.INSTANT), None)

    @property
    def is_time_series(self) -> bool:
        return self.instant_column is not None

    # Schéma Parquet, dont la DDL du warehouse est dérivée
    @property
    def schema(self) -> pa.Schema:
        fields = [pa.field(c.name, c.arrow_type, nullable=False) for c in self.keys]
        fields += [pa.field(c.name, c.arrow_type) for c in self.attributes + self.measures]
        fields += [
            pa.field(INGESTED_AT_COLUMN, pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field(SOURCE_COLUMN, pa.string(), nullable=False),
        ]
        return pa.schema(fields)


_NATIONAL_LABELS = (label("perimetre"), label("nature"))

_COMMON_MEASURES = measures(
    "consommation",
    "prevision_j1",
    "prevision_j",
    "fioul",
    "charbon",
    "gaz",
    "nucleaire",
    "eolien",
    "solaire",
    "hydraulique",
    "pompage",
    "bioenergies",
    "ech_physiques",
    "taux_co2",
    "ech_comm_angleterre",
    "ech_comm_espagne",
    "ech_comm_italie",
    "ech_comm_suisse",
    "ech_comm_allemagne_belgique",
)

# Détail par filière, publié uniquement dans le dataset consolidé/définitif
_CONS_DEF_DETAIL = measures(
    "fioul_tac",
    "fioul_cogen",
    "fioul_autres",
    "gaz_tac",
    "gaz_cogen",
    "gaz_ccg",
    "gaz_autres",
    "hydraulique_fil_eau_eclusee",
    "hydraulique_lacs",
    "hydraulique_step_turbinage",
    "bioenergies_dechets",
    "bioenergies_biomasse",
    "bioenergies_biogaz",
)

# Temps réel : publié au pas 15 min avec quelques minutes de délai, révisé en continu
NATIONAL_TR = DatasetSpec(
    dataset_id="eco2mix-national-tr",
    keys=(instant(KEY_COLUMN),),
    attributes=_NATIONAL_LABELS,
    measures=_COMMON_MEASURES,
    description="éCO2mix national temps réel",
)

# Consolidé puis définitif : publié avec plusieurs mois de retard
NATIONAL_CONS_DEF = DatasetSpec(
    dataset_id="eco2mix-national-cons-def",
    keys=(instant(KEY_COLUMN),),
    attributes=_NATIONAL_LABELS,
    measures=_COMMON_MEASURES + _CONS_DEF_DETAIL,
    description="éCO2mix national consolidé et définitif",
)


REGIONAL_CONS_DEF = DatasetSpec(
    dataset_id="eco2mix-regional-cons-def",
    keys=(instant(KEY_COLUMN), code("code_insee_region")),
    attributes=(label("libelle_region"), label("nature")),
    measures=measures(
        "consommation",
        "thermique",
        "nucleaire",
        "eolien",
        "eolien_terrestre",
        "eolien_offshore",
        "solaire",
        "hydraulique",
        "pompage",
        "bioenergies",
        "ech_physiques",
        "stockage_batterie",
        "destockage_batterie",
        "tco_thermique",
        "tch_thermique",
        "tco_nucleaire",
        "tch_nucleaire",
        "tco_eolien",
        "tch_eolien",
        "tco_solaire",
        "tch_solaire",
        "tco_hydraulique",
        "tch_hydraulique",
        "tco_bioenergies",
        "tch_bioenergies",
    ),
    description="éCO2mix régional consolidé et définitif",
)

# Pièce maîtresse du diagnostic : consommation annuelle des sites industriels raccordés au réseau
CONSO_IRIS = DatasetSpec(
    dataset_id="consommation-annuelle-par-iris",
    keys=(period("annee"), code("code_iris")),
    attributes=(
        code("code_insee_commune"),
        label("commune"),
        code("code_insee_departement"),
        label("departement"),
        code("code_insee_region"),
        label("region"),
    ),
    measures=measures(
        "consommation_electricite_rte",
        "pdl_electricite_rte",
        "consommation_gaz_grtgaz",
        "pdl_gaz_grtgaz",
        "consommation_gaz_terega",
        "pdl_gaz_terega",
        "consommation_totale",
        "pdl_total",
    ),
    description="Consommation annuelle des sites industriels raccordés au transport, par IRIS",
)

# Région excédentaire ou déficitaire, mois par mois
EQUILIBRE_REGIONAL = DatasetSpec(
    dataset_id="equilibre-regional-mensuel-prod-conso-brute",
    keys=(period("mois"), code("code_insee_region")),
    attributes=(label("region"),),
    measures=measures(
        "production_totale",
        "pompage",
        "solde_echanges_physiques",
        "consommation_brute",
    ),
    description="Équilibre mensuel production / consommation brute par région",
)

# Signal de contrainte du réseau régional : puissance à compenser et énergie non évacuée par saison
CONTRAINTES_REGIONALES = DatasetSpec(
    dataset_id="energies-et-puissances-regionales-liees-au-contraintes",
    keys=(code("region"),),
    attributes=(),
    measures=measures(
        "puissance_enr_installee",
        "puissance_totale_a_compenser",
        "energie_non_evacuee_moyenne_printemps",
        "energie_non_evacuee_moyenne_ete",
        "energie_non_evacuee_moyenne_automne",
        "energie_non_evacuee_moyenne_hiver",
    ),
    description="Énergies et puissances régionales liées aux contraintes réseau",
)


# Contraintes résiduelles prospectives (horizon 3 à 5 ans) sur le réseau de transport
CONTRAINTES_OUVRAGES = DatasetSpec(
    dataset_id="contraintes-region",
    keys=(
        code("ouvrage"),
        code("poste_1"),
        code("poste_2", vide_si_absent=True),
        code("poste_3", vide_si_absent=True),
    ),
    attributes=(
        label("region"),
        label("nom_de_l_ouvrage"),
        label("specificite"),
        label("occurrence"),
        label("duree"),
        label("perennite"),
    ),
    measures=measures(
        "puissance_max_de_l_ouvrage", "pourcentage_1", "pourcentage_2", "pourcentage_3"
    ),
    description="Contraintes résiduelles prospectives sur le réseau de transport, par ouvrage",
)

# Postes électriques RTE (environ 5 000) : tension, état et département
POSTES_RTE = DatasetSpec(
    dataset_id="postes-electriques-rte",
    keys=(code("code_poste"),),
    attributes=(
        label("nom_poste"),
        label("fonction"),
        label("etat"),
        label("tension"),
        label("departement"),
    ),
    measures=(),
    description="Sites électriques RTE et points de piquage",
)


# Calendrier Tempo, extrait de l'API RTE (pas d'ODRÉ) : une couleur par jour
RTE_TEMPO = DatasetSpec(
    dataset_id="rte-tempo",
    keys=(instant(KEY_COLUMN),),
    attributes=(label("couleur"),),
    measures=(),
    description="Calendrier Tempo RTE (couleur de chaque jour)",
)

# Risques recensés par commune (DDRM), extraits de l'export GASPAR ; `millesime` : date du fichier
GASPAR_RISQUES = DatasetSpec(
    dataset_id="gaspar-risques",
    keys=(code("code_commune"), code("num_risque")),
    attributes=(label("libelle_commune"), label("libelle_risque"), period("millesime")),
    measures=(),
    description="Risques majeurs recensés par commune (GASPAR, DDRM)",
)

# Plans de prévention (PPRN, PPRT, PPRM) de l'export GASPAR : une ligne par commune, procédure
# et aléa ; `code_risque` est l'aléa le plus précis publié (vide pour un plan sans aléa)
GASPAR_PLANS = DatasetSpec(
    dataset_id="gaspar-plans",
    keys=(code("code_commune"), code("code_procedure"), code("code_risque", vide_si_absent=True)),
    attributes=(
        label("type_plan"),
        label("libelle_plan"),
        label("libelle_risque"),
        label("libelle_alea"),
        label("etat"),
        label("sous_etat"),
        period("date_prescription"),
        period("date_approbation"),
        period("millesime"),
    ),
    measures=(),
    description="Plans de prévention des risques par commune (GASPAR : PPRN, PPRT, PPRM)",
)

EDF_SEI_PRODUCTION = DatasetSpec(
    dataset_id="edf-sei-production",
    keys=(code("territoire"), instant("date_heure")),
    attributes=(label("statut"),),
    measures=measures(
        "production_totale_mw",
        "thermique_mw",
        "bagasse_charbon_mw",
        "hydraulique_mw",
        "micro_hydraulique_mw",
        "photovoltaique_mw",
        "eolien_mw",
        "bioenergies_mw",
        "geothermie_mw",
        "importations_mw",
        "stockage_mw",
        "cout_moyen_de_production_eur_mwh",
    ),
    description="Production horaire par filière en Corse et outre-mer (EDF SEI)",
)

EUROSTAT_PRIX_UE = DatasetSpec(
    dataset_id="eurostat-nrg-pc-205-ue",
    keys=(code("pays"), period("semestre"), code("tranche"), code("fiscalite")),
    attributes=(label("libelle_tranche"), label("statut")),
    measures=measures("prix_eur_kwh"),
    description="Prix de l'électricité des entreprises, pays de l'UE, par tranche (Eurostat)",
)

EMBER_INTENSITE_CARBONE = DatasetSpec(
    dataset_id="ember-intensite-carbone",
    keys=(code("iso3"), period("annee")),
    attributes=(label("pays_ember"),),
    measures=measures("production_twh", "intensite_g_co2e_kwh"),
    description="Intensité carbone annuelle de la production électrique, pays de l'UE (Ember)",
)

EUROSTAT_PRIX_ENTREPRISES = DatasetSpec(
    dataset_id="eurostat-nrg-pc-205",
    keys=(period("semestre"), code("tranche"), code("fiscalite")),
    attributes=(label("libelle_tranche"), label("statut")),
    measures=measures("prix_eur_kwh"),
    description="Prix de l'électricité des entreprises, France, par tranche (Eurostat)",
)


# Datasets extraits depuis ODRÉ, adressables par `--dataset` en ligne de commande
SPECS: dict[str, DatasetSpec] = {
    spec.dataset_id: spec
    for spec in (
        NATIONAL_TR,
        NATIONAL_CONS_DEF,
        REGIONAL_CONS_DEF,
        CONSO_IRIS,
        EQUILIBRE_REGIONAL,
        CONTRAINTES_REGIONALES,
        CONTRAINTES_OUVRAGES,
        POSTES_RTE,
    )
}

TERRITORIAL_SPECS: tuple[DatasetSpec, ...] = (
    CONSO_IRIS,
    EQUILIBRE_REGIONAL,
    CONTRAINTES_REGIONALES,
    CONTRAINTES_OUVRAGES,
    POSTES_RTE,
)

# Tables bronze créées d'office à chaque chargement, parce que la couche silver
BRONZE_SPECS: tuple[DatasetSpec, ...] = (
    NATIONAL_TR,
    NATIONAL_CONS_DEF,
    RTE_TEMPO,
    EUROSTAT_PRIX_ENTREPRISES,
    GASPAR_RISQUES,
    GASPAR_PLANS,
    EDF_SEI_PRODUCTION,
    EUROSTAT_PRIX_UE,
    EMBER_INTENSITE_CARBONE,
    *TERRITORIAL_SPECS,
)


# Jeu de données par identifiant, avec la liste des jeux connus en cas d'erreur
def get_spec(dataset_id: str) -> DatasetSpec:
    try:
        return SPECS[dataset_id]
    except KeyError:
        known = ", ".join(sorted(SPECS))
        raise KeyError(f"dataset inconnu : {dataset_id} (connus : {known})") from None
