from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from ingestion.acces_routier import Acces, AccesRoutierError, lire_acces
from ingestion.config import Settings, get_settings
from ingestion.courbe import BilanCourbe, Courbe, bilan_courbe
from ingestion.eau import LIBELLES_NIVEAU, LIBELLES_TYPE, EauError, SituationEau, lire_situation_eau
from ingestion.extract_sismique import contient
from ingestion.geocode import Territoire, adresse_normalisee, code_commune_parent
from ingestion.georisques import (
    Exposition,
    GeorisquesError,
    Risque,
    RisquesSite,
    avec_zonage,
    lire_risques,
    lire_zonage,
    risques_gaspar,
)
from ingestion.solaire import (
    PERTES_PCT,
    Batiment,
    Productible,
    SolaireError,
    lire_batiment,
    lire_productible_pvgis,
)
from ingestion.turpe import PROFIL_ACTIVITE, PROFIL_COURBE, Acheminement, lire_acheminement
from ingestion.urbanisme import UrbanismeError, ZoneUrbanisme, lire_zone_urbanisme
from ingestion.zones_activite import (
    RAYON_KM as RAYON_ZONES_ACTIVITE_KM,
)
from ingestion.zones_activite import ZoneActivite, ZonesActiviteError, lire_zones_activite

logger = logging.getLogger(__name__)

# Arrondissements municipaux (3 premiers caractères du code) -> code de la commune
_DIAGNOSTIC_SQL = """
select
    territoire.*,
    commune.commune,
    commune.consommation_electricite_mwh / 1000 as conso_industrielle_commune_gwh,
    commune.nb_sites_electricite as nb_sites_commune,
    commune.est_minorant as conso_commune_est_minorant
from gold.fct_diagnostic_territoire as territoire
left join gold.fct_pression_industrielle_commune as commune
    on commune.code_insee_commune = $commune
where territoire.code_departement = $departement
"""

_SEUIL_SQL = """
select domaine_tension, gestionnaire, commentaire, domaine_turpe
from gold.seuils_raccordement
where $puissance >= puissance_min_mw
  and (puissance_max_mw is null or $puissance < puissance_max_mw)
"""


PROFIL_DEFAUT = "bureau_36kva"


@dataclass(frozen=True, slots=True)
class Activite:
    libelle: str
    # Journée type mesurée par Enedis (`profils_charge_reels`) ; `None` : puissance constante
    profil_charge: str | None
    # Régimes d'accise de l'attestation officielle 2040-TIC-ATT-E, sans taux (sources divergentes)
    accise: str


ACTIVITES: dict[str, Activite] = {
    "data-center": Activite(
        "data center",
        None,
        "centres de stockage de données (E16, CIBS L312-64 et L312-70) : tarif réduit sur la"
        " fraction au-delà de 1 GWh par an, sous conditions cumulatives attestées au"
        " fournisseur",
    ),
    "irve": Activite(
        "bornes de recharge",
        None,
        "aucun usage de l'attestation officielle ne vise la recharge de véhicules (seuls"
        " le transport guidé, E15, et le transport collectif routier de personnes, E19, y"
        " figurent)",
    ),
    "industrie": Activite(
        "industrie",
        "industrie_sup250kva",
        "activité industrielle (CIBS L312-65 et L312-71) : grande consommatrice (E23) dès 0,5 %"
        " d'électro-intensité, électro-sensible (E24) dès 2,25 %, électro-intensive (E25) dès"
        " 6,75 %, ou exposée à la concurrence internationale (E30, L312-72) dès 0,5 % : voir"
        " --va",
    ),
    "logistique": Activite(
        "logistique",
        None,
        "aucun usage de l'attestation officielle ne vise l'entrepôt ; seulement la"
        " manutention portuaire (E21, dès 0,5 %), le transport guidé (E15) ou l'alimentation"
        " à quai (E18) si l'activité en relève",
    ),
}

_PROFIL_CHARGE_SQL = """
select type_jour, heure_debut, puissance_kw
from gold.profils_charge_reels
where profil = $profil
"""

# Domaines chiffrés par la grille TURPE HTA-BT ; le tarif bleu inclut déjà l'acheminement
DOMAINES_TURPE = {"bt_sup36": "BT > 36 kVA", "hta": "HTA"}

_COUT_SQL = """
select
    cout.mois,
    profil.libelle,
    cout.conso_kwh,
    cout.part_hc,
    cout.cout_htva_eur,
    cout.prix_moyen_htva_eur_kwh,
    cout.intensite_moyenne_g_kwh,
    cout.kwh_decales,
    cout.gain_decalage_eur,
    cout.co2_evite_kg
from gold.fct_cout_mensuel_profil as cout
inner join (
    select distinct profil, libelle from gold.profils_consommation
) as profil on profil.profil = cout.profil
where cout.profil = $profil
  and cout.est_complet
  and cout.cout_htva_eur is not null
order by cout.mois desc
limit 1
"""


NB_CONTRAINTES_AFFICHEES = 3

_CONTRAINTES_SQL = """
select nom_de_l_ouvrage, occurrence, duree, perennite, puissance_max_mw, est_en_journee
from gold.fct_contraintes_departement
where code_departement = $departement
order by rang_occurrence desc, rang_perennite desc, puissance_max_mw desc, nom_de_l_ouvrage
limit $limite
"""

# Distance à vol d'oiseau (haversine, rayon terrestre de 6 371 km) entre le point de la ligne
_DISTANCE_KM_SQL = """
    2 * 6371 * asin(sqrt(
        power(sin(radians(latitude - $latitude) / 2), 2)
        + cos(radians($latitude)) * cos(radians(latitude))
        * power(sin(radians(longitude - $longitude) / 2), 2)
    ))
"""

_ZONES_SQL = f"""
with distances as (
    select *, {_DISTANCE_KM_SQL} as distance_km
    from gold.zones_raccordement_rte
)
select
    zone,
    priorite,
    annee_capacite,
    ouvrages,
    arg_min(point_reference, distance_km) as point_reference,
    min(distance_km) as distance_km
from distances
group by zone, priorite, annee_capacite, ouvrages
order by distance_km
"""

_ENTREPRISES_COMMUNE_SQL = """
select
    annee,
    nb_sites,
    consommation_mwh / 1000 as consommation_gwh,
    consommation_industrie_mwh / 1000 as consommation_industrie_gwh,
    nb_sites_industrie,
    code_naf2_principal,
    libelle_naf2_principal,
    consommation_naf2_principal_mwh / 1000 as consommation_naf2_principal_gwh,
    rang_industrie_departement,
    nb_communes_departement
from gold.fct_conso_entreprises_commune
where code_insee_commune = $commune
"""

_ENTREPRISES_ADRESSE_SQL = """
select
    adresse.annee,
    adresse.adresse,
    adresse.nb_sites,
    adresse.consommation_mwh,
    adresse.grand_secteur,
    adresse.code_naf2,
    naf.libelle_naf2
from gold.fct_conso_entreprises_adresse as adresse
left join gold.dim_naf2 as naf on naf.code_naf2 = adresse.code_naf2
where adresse.code_insee_commune = $commune and adresse.adresse_normalisee = $adresse
"""

_SITE_SQL = f"""
select
    id_site,
    commune,
    code_insee_commune,
    maturite_site,
    {_DISTANCE_KM_SQL} as distance_km
from gold.sites_cles_en_main
where case
    when $est_zni then code_departement = $departement
    else code_departement not like '97%' and code_departement not in ('2A', '2B')
end
order by distance_km
limit 1
"""

# Pôle de fret le plus proche de chaque mode, dans le même territoire que l'adresse
_POLES_PROCHES_SQL = f"""
select mode, nom, latitude, longitude
from (
    select
        *,
        row_number() over (partition by mode order by {_DISTANCE_KM_SQL}) as rang
    from gold.fct_poles_fret
    where case
        when $est_zni then code_departement = $departement
        else code_departement not like '97%' and code_departement not in ('2A', '2B')
    end
)
where rang = 1
"""

_CARBONE_SQL = """
select
    systeme_electrique,
    intensite_acv_g_co2e_kwh,
    validite_source,
    seuil_decarbone_g_co2e_kwh,
    est_decarbone,
    intensite_directe_rte_g_co2_kwh,
    rte_jour_debut,
    rte_jour_fin
from gold.fct_carbone_territoire
where code_insee_region = $region
"""

_CARBONE_HORAIRE_SQL = """
select annee, heure_locale, intensite_estimee_g_co2e_kwh
from gold.fct_carbone_horaire_zni
where code_insee_region = $region
order by heure_locale
"""

_ACCISE_SQL = """
select tarif_eur_mwh, date_debut
from gold.accise_electricite
where categorie = 'pme_haute_puissance' and date_fin is null
order by date_debut desc
limit 1
"""

_PRIX_SQL = """
select *
from gold.fct_prix_entreprise_tranche
where $conso >= conso_min_mwh
  and (conso_max_mwh is null or $conso < conso_max_mwh)
"""

# Tranche Eurostat affichée sans `--conso` : moins de 20 MWh par an
TRANCHE_DEFAUT = "IA"

_PRIX_DEFAUT_SQL = """
select *
from gold.fct_prix_entreprise_tranche
where code_tranche = $code
"""


# Plans en vigueur, approuvés d'abord, puis du plus récent au plus ancien
_PLANS_SQL = """
select code_procedure, type_plan, libelle_plan, risques, etat, date_prescription, date_approbation
from gold.fct_plans_prevention_commune
where code_insee_commune in (select unnest($codes))
order by etat, coalesce(date_approbation, date_prescription) desc nulls last, libelle_plan
"""


# Qualité de fourniture du département, publiée par Enedis sur son seul territoire
_CONTINUITE_SQL = """
select
    derniere_annee,
    part_mal_alimentes_pct,
    moyenne_5_ans_pct,
    annees_au_dela_seuil_5_ans,
    mediane_nationale_pct,
    depasse_seuil
from gold.fct_continuite_departement
where code_departement = $departement
"""


# Prix de la commune si elle compte assez de ventes, sinon du département
_LOCAUX_SQL = """
select niveau, nb_ventes, prix_m2_median, prix_m2_q1, prix_m2_q3, derniere_vente
from gold.fct_prix_locaux
where (niveau = 'commune' and code_zone = $commune and fiable)
    or (niveau = 'departement' and code_zone = $departement)
order by niveau = 'commune' desc
limit 1
"""


# Friches d'au moins 1 ha à moins de 10 km à vol d'oiseau, les plus proches d'abord
RAYON_FRICHES_KM = 10
NB_FRICHES_AFFICHEES = 3

_FRICHES_SQL = f"""
select *
from (
    select
        site_id,
        coalesce(site_nom, site_id) as nom,
        type_friche,
        statut,
        commune,
        surface_m2,
        type_zone_urbanisme,
        pollution_sol,
        {_DISTANCE_KM_SQL} as distance_km
    from gold.fct_friches
    where abs(latitude - $latitude) < {RAYON_FRICHES_KM} / 111.0
      and abs(longitude - $longitude) < {RAYON_FRICHES_KM} / (111.0 * cos(radians($latitude)))
)
where distance_km <= {RAYON_FRICHES_KM}
order by distance_km
"""


class DiagnosticIndisponibleError(RuntimeError):
    pass


class DonneesAbsentesError(DiagnosticIndisponibleError):
    pass


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


# Code département d'un code commune : 3 caractères outre-mer, 2 ailleurs
def code_departement(code_commune: str) -> str:
    return code_commune[:3] if code_commune.startswith("97") else code_commune[:2]


@dataclass(frozen=True, slots=True)
class Seuil:
    puissance_mw: float
    domaine_tension: str
    gestionnaire: str
    commentaire: str
    # Grille TURPE du domaine : `bt_inf36`, `bt_sup36`, `hta` ou `htb`
    domaine_turpe: str


@dataclass(frozen=True, slots=True)
class Carbone:
    systeme_electrique: str
    intensite_acv_g_co2e_kwh: float
    validite_source: str
    seuil_decarbone_g_co2e_kwh: float
    est_decarbone: bool
    # Émissions directes observées par RTE sur 12 mois ; absentes en ZNI
    intensite_directe_rte_g_co2_kwh: float | None
    rte_jour_debut: date | None
    rte_jour_fin: date | None
    # Corse et outre-mer : (heure locale, gCO₂e/kWh estimés) sur l'année EDF SEI `annee_profil`
    profil_horaire: tuple[tuple[int, float], ...] = ()
    annee_profil: int | None = None


@dataclass(frozen=True, slots=True)
class CoutProfil:
    mois: date
    libelle: str
    conso_kwh: float
    part_hc: float
    cout_htva_eur: float
    prix_moyen_htva_eur_kwh: float
    intensite_moyenne_g_kwh: float
    kwh_decales: float
    gain_decalage_eur: float
    co2_evite_kg: float


@dataclass(frozen=True, slots=True)
class ZoneRte:
    zone: str
    priorite: str
    annee_capacite: int | None
    ouvrages: str
    point_reference: str
    distance_km: float


@dataclass(frozen=True, slots=True)
class EntreprisesCommune:
    annee: int
    nb_sites: int
    consommation_gwh: float
    consommation_industrie_gwh: float
    nb_sites_industrie: int
    code_naf2_principal: str | None
    libelle_naf2_principal: str | None
    consommation_naf2_principal_gwh: float | None
    rang_industrie_departement: int
    nb_communes_departement: int


@dataclass(frozen=True, slots=True)
class EntreprisesAdresse:
    annee: int
    adresse: str
    nb_sites: int
    consommation_mwh: float
    grand_secteur: str
    code_naf2: str | None
    libelle_naf2: str | None


@dataclass(frozen=True, slots=True)
class SiteCleEnMain:
    id_site: str
    commune: str
    code_insee_commune: str
    maturite_site: str
    distance_km: float


@dataclass(frozen=True, slots=True)
class ContrainteOuvrage:
    nom_de_l_ouvrage: str
    occurrence: str
    duree: str
    perennite: str
    puissance_max_mw: float | None
    est_en_journee: bool


@dataclass(frozen=True, slots=True)
class PrixEntreprise:
    # Consommation déclarée ; `None` pour la tranche par défaut, sans facture
    conso_mwh: float | None
    tranche: str
    code_tranche: str
    libelle_tranche: str
    conso_min_mwh: float
    conso_max_mwh: float | None
    premier_semestre: str
    dernier_semestre: str
    prix_dernier_semestre_eur_kwh: float
    prix_reference_eur_kwh: float
    taxes_reference_eur_kwh: float | None
    prix_moyen_5_ans_eur_kwh: float
    prix_min_eur_kwh: float
    semestre_min: str
    prix_max_eur_kwh: float
    semestre_max: str
    prix_3_ans_bas_eur_kwh: float | None
    prix_3_ans_central_eur_kwh: float | None
    prix_3_ans_haut_eur_kwh: float | None
    nb_erreurs_observees: int | None
    nb_periodes_controlees: int
    est_fourchette_validee: bool
    nb_dans_eventail: int | None
    nb_au_dessus: int | None
    nb_en_dessous: int | None
    premiere_origine_controlee: str | None
    derniere_origine_controlee: str | None
    ecart_median_au_central: float | None

    def facture_eur(self, prix_eur_kwh: float) -> float:
        if self.conso_mwh is None:
            raise ValueError("facture inconnue sans consommation annuelle")
        return self.conso_mwh * 1000 * prix_eur_kwh

    @property
    def est_tranche_par_defaut(self) -> bool:
        return self.conso_mwh is None


@dataclass(frozen=True, slots=True)
class Accise:
    tarif_eur_mwh: float
    date_debut: date

    # L'accise est indexée chaque 1er février : passé cette date, le barème est peut-être périmé
    def a_reverifier(self, aujourd_hui: date | None = None) -> bool:
        annee = self.date_debut.year + (self.date_debut >= date(self.date_debut.year, 2, 1))
        return (aujourd_hui or date.today()) >= date(annee, 2, 1)


# Paliers d'électro-intensité ouvrant les taux réduits d'accise (CIBS L312-65 et L312-71)
PALIERS_ELECTRO_INTENSITE = (0.0675, 0.03375, 0.005)


@dataclass(frozen=True, slots=True)
class PartValeurAjoutee:
    valeur_ajoutee_eur: float
    facture_eur: float
    part_facture: float
    # Accise au tarif normal rapportée à la valeur ajoutée : le critère légal
    electro_intensite: float
    accise: Accise
    # Plus haut palier atteint ; `None` sous 0,5 %
    palier: float | None


@dataclass(frozen=True, slots=True)
class Friche:
    site_id: str
    nom: str
    type_friche: str
    statut: str | None
    commune: str | None
    surface_m2: float
    type_zone_urbanisme: str | None
    pollution_sol: str | None
    distance_km: float


@dataclass(frozen=True, slots=True)
class PartChiffreAffaires:
    chiffre_affaires_eur: float
    facture_eur: float
    part: float
    # Bornes des scénarios à 3 ans ; `None` si l'historique de la tranche est trop court
    part_bas: float | None
    part_haut: float | None


@dataclass(frozen=True, slots=True)
class PrixLocaux:
    # `commune`, ou `departement` quand la commune compte trop peu de ventes
    niveau: str
    nb_ventes: int
    prix_m2_median: float
    prix_m2_q1: float
    prix_m2_q3: float
    derniere_vente: date


@dataclass(frozen=True, slots=True)
class Continuite:
    derniere_annee: int
    part_mal_alimentes_pct: float
    moyenne_5_ans_pct: float | None
    annees_au_dela_seuil_5_ans: int
    # Médiane des départements publiés la même année
    mediane_nationale_pct: float | None
    # Plus de 3 % de clients mal alimentés : le département sort de la norme réglementaire
    depasse_seuil: bool


@dataclass(frozen=True, slots=True)
class PlanPrevention:
    code_procedure: str
    # PPRN (naturels), PPRT (technologiques) ou PPRM (miniers)
    type_plan: str
    libelle_plan: str
    # Familles d'aléas couvertes (« Inondation, Mouvement de terrain »)
    risques: str | None
    # `approuvé` (ou appliqué par anticipation) ou `prescrit` (en cours d'élaboration)
    etat: str
    date_prescription: date | None
    date_approbation: date | None


@dataclass(frozen=True, slots=True)
class Diagnostic:
    territoire: Territoire
    code_insee_commune: str
    code_departement: str
    departement: str
    code_insee_region: str
    region: str
    est_zni: bool
    annee_pression: int | None
    commune: str | None
    conso_industrielle_commune_gwh: float | None
    nb_sites_commune: int | None
    conso_commune_est_minorant: bool | None
    conso_industrielle_departement_gwh: float
    nb_sites_departement: int
    nb_communes_avec_site: int
    conso_departement_est_minorant: bool
    rang_pression_departement: int | None
    nb_departements_classes: int
    equilibre_mois_debut: date | None
    equilibre_mois_fin: date | None
    equilibre_nb_mois: int
    production_region_gwh: float | None
    consommation_region_gwh: float | None
    solde_region_gwh: float | None
    taux_couverture_region: float | None
    statut_equilibre: str | None
    rang_dependance_region: int | None
    nb_regions_classees: int
    puissance_enr_installee_mw: float | None
    puissance_a_compenser_mw: float | None
    energie_non_evacuee_mwh: float | None
    nb_postes_rte: int | None
    nb_postes_htb1: int | None
    nb_postes_htb2: int | None
    nb_postes_htb3: int | None
    nb_postes_en_projet: int | None
    nb_contraintes_departement: int
    nb_contraintes_frequentes_departement: int | None
    contraintes: list[ContrainteOuvrage]
    # Zone prioritaire RTE (P1 ou P2) la plus proche de l'adresse
    zone_rte: ZoneRte | None
    # Exemple de zone P3 cité par RTE, seulement s'il est plus proche que `zone_rte`
    zone_rte_p3: ZoneRte | None
    # Site industriel clé en main le plus proche, dans le même territoire
    site_cle_en_main: SiteCleEnMain | None
    seuil: Seuil | None
    # Consommation annuelle déclarée (`--conso`), même sans prix publié pour sa tranche
    conso_mwh: float | None
    prix: PrixEntreprise | None
    cout: CoutProfil | None
    carbone: Carbone | None
    limites: dict[str, str]
    risques: RisquesSite | None = None
    risques_erreur: str | None = None
    ca_eur: float | None = None
    va_eur: float | None = None
    # Dernier chargement de chaque source par `refresh` (voir `SOURCES_CHARGEES`)
    chargements: dict[str, date] = field(default_factory=dict)
    # Courbe de charge Enedis fournie par `--courbe`
    courbe: BilanCourbe | None = None
    # Plans de prévention en vigueur dans la commune ; `None` si l'export GASPAR n'est pas chargé
    plans_prevention: list[PlanPrevention] | None = None
    # Barème d'accise en vigueur, lu seulement si `--va` est fourni
    accise: Accise | None = None
    # Entreprises raccordées à Enedis (dernière année publiée), dans la commune et à l'adresse
    entreprises_commune: EntreprisesCommune | None = None
    entreprises_adresse: EntreprisesAdresse | None = None
    # Part acheminement (TURPE HTA-BT), avec `--puissance` et `--conso` au-delà de 36 kVA
    acheminement: Acheminement | None = None
    # Qualité de fourniture du département ; `None` hors Enedis ou si la source n'est pas chargée
    continuite: Continuite | None = None
    # Prix de vente des locaux d'activité (DVF) et surface cherchée avec `--surface`
    prix_locaux: PrixLocaux | None = None
    surface_m2: float | None = None
    # Clé de `ACTIVITES` donnée par `--activite`
    activite: str | None = None
    # Zone d'urbanisme de l'adresse (Géoportail de l'urbanisme) ; `urbanisme_consulte` distingue
    # « aucun document numérisé » de « non consulté »
    urbanisme: ZoneUrbanisme | None = None
    urbanisme_consulte: bool = False
    urbanisme_erreur: str | None = None
    # Arrêtés de restriction des usages de l'eau en vigueur à l'adresse (VigiEau)
    eau: SituationEau | None = None
    eau_erreur: str | None = None
    # Zones d'activité de la BD TOPO à moins de `RAYON_ZONES_ACTIVITE_KM` (nombre et plus proches)
    nb_zones_activite: int | None = None
    zones_activite: list[ZoneActivite] = field(default_factory=list)
    zones_activite_erreur: str | None = None
    # Pôle de fret le plus proche de chaque mode (type, nom, latitude, longitude) ; l'accès routier
    # (`completer_acces_routier`) en calcule les temps de trajet
    poles_proches: list[tuple[str, str, float, float]] = field(default_factory=list)
    acces_routier: list[Acces] | None = None
    acces_routier_erreur: str | None = None
    # Bâtiment de l'adresse (BD TOPO) et productible photovoltaïque d'un kWc (PVGIS)
    batiment: Batiment | None = None
    productible: Productible | None = None
    solaire_consulte: bool = False
    solaire_erreur: str | None = None
    # Nombre de friches d'au moins 1 ha à moins de `RAYON_FRICHES_KM`, et les plus proches ;
    # `None` si Cartofriches n'est pas chargé
    nb_friches: int | None = None
    friches: list[Friche] = field(default_factory=list)

    @property
    def commune_a_des_sites(self) -> bool:
        return bool(self.nb_sites_commune)

    @property
    def part_valeur_ajoutee(self) -> PartValeurAjoutee | None:
        p = self.prix
        va = self.va_eur
        if va is None or p is None or p.conso_mwh is None or self.accise is None:
            return None
        intensite = p.conso_mwh * self.accise.tarif_eur_mwh / va
        facture = p.facture_eur(p.prix_reference_eur_kwh)
        return PartValeurAjoutee(
            valeur_ajoutee_eur=va,
            facture_eur=facture,
            part_facture=facture / va,
            electro_intensite=intensite,
            accise=self.accise,
            palier=next((s for s in PALIERS_ELECTRO_INTENSITE if intensite >= s), None),
        )

    @property
    def part_chiffre_affaires(self) -> PartChiffreAffaires | None:
        p = self.prix
        ca = self.ca_eur
        if ca is None or p is None or p.conso_mwh is None:
            return None
        bas, haut = p.prix_3_ans_bas_eur_kwh, p.prix_3_ans_haut_eur_kwh
        facture = p.facture_eur(p.prix_reference_eur_kwh)
        return PartChiffreAffaires(
            chiffre_affaires_eur=ca,
            facture_eur=facture,
            part=facture / ca,
            part_bas=None if bas is None else p.facture_eur(bas) / ca,
            part_haut=None if haut is None else p.facture_eur(haut) / ca,
        )


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
                connection, _DIAGNOSTIC_SQL, {"commune": commune, "departement": departement}
            )
            seuil = _seuil(connection, puissance_mw) if puissance_mw is not None else None
            profil_charge = ACTIVITES[activite].profil_charge if activite else None
            journee_type = (
                {
                    (type_jour, heure): kw
                    for type_jour, heure, kw in connection.execute(
                        _PROFIL_CHARGE_SQL, {"profil": profil_charge}
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
                _SITE_SQL,
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
                    _CONTRAINTES_SQL,
                    {"departement": departement, "limite": NB_CONTRAINTES_AFFICHEES},
                ).fetchall()
            ]
            cout_ligne = _une_ligne(connection, _COUT_SQL, {"profil": PROFIL_DEFAUT})
            carbone = (
                None
                if ligne is None
                else _une_ligne(connection, _CARBONE_SQL, {"region": ligne["code_insee_region"]})
            )
            if carbone is not None:
                heures = connection.execute(
                    _CARBONE_HORAIRE_SQL, {"region": ligne["code_insee_region"]}
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
            accise = None if va_eur is None else _une_ligne(connection, _ACCISE_SQL, {})
            entreprises_commune = _une_ligne(
                connection, _ENTREPRISES_COMMUNE_SQL, {"commune": commune}
            )
            # Seule une adresse au numéro près se compare à celles d'Enedis
            entreprises_adresse = (
                _une_ligne(
                    connection,
                    _ENTREPRISES_ADRESSE_SQL,
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
                _une_ligne(connection, _CONTINUITE_SQL, {"departement": departement})
                if "enedis_continuite_alimentation" in chargements
                else None
            )
            prix_locaux = (
                _une_ligne(
                    connection, _LOCAUX_SQL, {"commune": commune, "departement": departement}
                )
                if "ventes_locaux" in chargements
                else None
            )
            poles_proches = connection.execute(
                _POLES_PROCHES_SQL,
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
                        _FRICHES_SQL,
                        {"latitude": territoire.latitude, "longitude": territoire.longitude},
                    ).fetchall()
                ]
                if "friches" in chargements
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
        **ligne,
    )


def _plans(connection: duckdb.DuckDBPyConnection, codes: set[str]) -> list[PlanPrevention]:
    resultat = connection.execute(_PLANS_SQL, {"codes": sorted(codes)})
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


def _seuil(connection: duckdb.DuckDBPyConnection, puissance_mw: float) -> Seuil | None:
    if puissance_mw < 0:
        raise ValueError("la puissance du projet doit etre positive")
    ligne = _une_ligne(connection, _SEUIL_SQL, {"puissance": puissance_mw})
    return None if ligne is None else Seuil(puissance_mw=puissance_mw, **ligne)


def _prix(connection: duckdb.DuckDBPyConnection, conso_mwh: float) -> PrixEntreprise | None:
    if conso_mwh <= 0:
        raise ValueError("la consommation annuelle doit etre strictement positive")
    ligne = _une_ligne(connection, _PRIX_SQL, {"conso": conso_mwh})
    return None if ligne is None else PrixEntreprise(conso_mwh=conso_mwh, **ligne)


def _prix_defaut(connection: duckdb.DuckDBPyConnection) -> PrixEntreprise | None:
    ligne = _une_ligne(connection, _PRIX_DEFAUT_SQL, {"code": TRANCHE_DEFAUT})
    return None if ligne is None else PrixEntreprise(conso_mwh=None, **ligne)


def _nombre(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def _gwh(valeur: float) -> str:
    return f"{_nombre(valeur, 1 if valeur < 100 else 0)} GWh"


@dataclass(frozen=True, slots=True)
class Section:
    titre: str
    # Couples (`"texte"` ou `"limite"`, contenu), dans l'ordre d'affichage
    elements: list[tuple[str, str]]


def entete(diagnostic: Diagnostic) -> list[str]:
    d = diagnostic
    t = d.territoire
    lignes = [
        f"Commune {t.commune} ({d.code_insee_commune}) · {d.departement} ({d.code_departement})"
        f" · {d.region} ({d.code_insee_region})",
    ]
    if not t.est_precis:
        lignes.append(f"Attention : adresse résolue à la maille « {t.precision} » seulement.")
    lignes.append(ligne_chargements(d.chargements))
    return lignes


A_SAVOIR = (
    "Le prix ne dépend pas de l'adresse mais de la consommation (péréquation, Corse et"
    " outre-mer compris). Le carbone est la moyenne annuelle du réseau de ce territoire, pas"
    " celle d'un contrat de fourniture."
)


def essentiel(diagnostic: Diagnostic) -> Section:
    d = diagnostic
    section = Section("L'essentiel : prix et carbone à cette adresse", [])
    section.elements.append(("texte", f"Prix moyen : {_prix_court(d)}"))
    if d.acheminement is not None:
        section.elements.append(("texte", f"Acheminement : {ligne_acheminement(d.acheminement)}"))
    if d.carbone is None:
        carbone = "non disponible (lancer dbt build)"
    else:
        c = d.carbone
        position = "sous le" if c.est_decarbone else "au-dessus du"
        carbone = (
            f"{'oui' if c.est_decarbone else 'non'} ({c.systeme_electrique}),"
            f" {_nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh en cycle de vie, {position}"
            f" seuil de {_nombre_court(c.seuil_decarbone_g_co2e_kwh)} gCO₂e/kWh"
        )
    section.elements.append(("texte", f"Électricité décarbonée : {carbone}"))
    if d.carbone is not None and (heures := heures_carbone(d.carbone)):
        section.elements.append(("texte", f"Heure par heure : {heures}"))
    if (deja := d.entreprises_adresse) is not None:
        section.elements.append(("texte", f"Déjà raccordé ici : {ligne_entreprises_adresse(deja)}"))
    if (part := d.part_chiffre_affaires) is not None:
        section.elements.append(("texte", f"Part du chiffre d'affaires : {_part_ca(part)}"))
    if (va := d.part_valeur_ajoutee) is not None:
        section.elements.append(("texte", f"Part de la valeur ajoutée : {_part_va(va)}"))
    if d.activite is not None:
        section.elements.append(("texte", f"Activité : {ligne_activite(d.activite)}"))
    if d.courbe is not None:
        section.elements.append(("texte", f"Votre courbe de charge : {ligne_courbe(d.courbe)}"))
    if (locaux := d.prix_locaux) is not None:
        ligne = ligne_locaux(locaux, d.surface_m2, d.territoire.commune)
        section.elements.append(("texte", f"Locaux d'activité : {ligne}"))
    section.elements.append(("texte", f"À savoir : {A_SAVOIR}"))
    section.elements.append(("texte", f"Brancher ici ? {phrase_verdict(verdict(d))}"))
    return section


_GRANDS_SECTEURS = {
    "INDUSTRIE": "industrie",
    "TERTIAIRE": "tertiaire",
    "AGRICULTURE": "agriculture",
    "INCONNU": "secteur inconnu",
}


def ligne_entreprises_adresse(e: EntreprisesAdresse) -> str:
    secteur = _GRANDS_SECTEURS.get(e.grand_secteur, e.grand_secteur.lower())
    activite = f", {e.libelle_naf2}" if e.libelle_naf2 else ""
    return (
        f"{e.nb_sites} site(s) d'entreprise consommant {_nombre(e.consommation_mwh, 1)} MWh"
        f" en {e.annee} ({secteur}{activite} ; Enedis)"
    )


def ligne_entreprises_commune(e: EntreprisesCommune) -> str:
    phrase = (
        f"{e.nb_sites} site(s), {_gwh(e.consommation_gwh)}/an en {e.annee}, dont industrie"
        f" {_gwh(e.consommation_industrie_gwh)} ({e.nb_sites_industrie} site(s))"
    )
    if e.consommation_industrie_gwh > 0:
        phrase += (
            f" — rang {e.rang_industrie_departement} sur {e.nb_communes_departement} communes"
            " du département"
        )
    if e.libelle_naf2_principal:
        phrase += (
            f" ; premier secteur : {e.libelle_naf2_principal}"
            f" ({_gwh(e.consommation_naf2_principal_gwh or 0)})"
        )
    return phrase


def ligne_courbe(b: BilanCourbe) -> str:
    phrase = (
        f"du {b.debut:%d/%m/%Y} au {b.fin:%d/%m/%Y}, {_nombre(b.conso_annuelle_mwh, 1)} MWh"
        f" par an, pointe de {_nombre(b.puissance_max_kw)} kW,"
        f" {_nombre(b.part_hc * 100)} % en heures creuses (22 h – 6 h)"
    )
    if b.cout_htva_eur_an is not None:
        cout = _euros_arrondis(b.cout_htva_eur_an)
        phrase += f" ; environ {cout} HTVA par an au tarif HP/HC actuel"
    if b.intensite_courbe_g_kwh is not None and b.intensite_reseau_g_kwh:
        ecart = b.intensite_courbe_g_kwh / b.intensite_reseau_g_kwh - 1
        sens = "de plus" if ecart >= 0 else "de moins"
        phrase += (
            f" ; cette consommation émet {_nombre(abs(ecart) * 100)} % {sens} que le réseau aux"
            f" mêmes heures ({b.source_carbone})"
        )
    return phrase


def ligne_acheminement(a: Acheminement) -> str:
    source = {
        PROFIL_COURBE: "votre courbe de charge",
        PROFIL_ACTIVITE: "journée type mesurée par Enedis pour l'industrie",
    }.get(a.profil, "puissance constante")
    phrase = (
        f"environ {_euros_arrondis(a.total_eur)} HTVA par an, soit"
        f" {_nombre(a.prix_eur_mwh, 1)} €/MWh, déjà compris dans le prix moyen (TURPE 7"
        f" {DOMAINES_TURPE[a.domaine_turpe]}, {a.utilisation} utilisation, la moins chère ici ;"
        f" {_nombre(a.puissance_kva)} kVA souscrits, {source})"
    )
    if a.a_reverifier():
        phrase += (
            ". Grille TURPE peut-être périmée (nouvelle chaque 1er août) : mettre à jour les"
            " seeds turpe_soutirage et turpe_gestion_comptage"
        )
    return phrase


def ligne_activite(code: str) -> str:
    a = ACTIVITES[code]
    charge = (
        "journée type mesurée par Enedis (points de plus de 250 kVA)"
        if a.profil_charge
        else "puissance constante, faute de courbe publiée"
    )
    return f"{a.libelle}, profil de charge : {charge}. Accise : {a.accise}. Taux non chiffrés."


def ligne_locaux(p: PrixLocaux, surface_m2: float | None, commune: str) -> str:
    lieu = commune if p.niveau == "commune" else "le département (peu de ventes dans la commune)"
    ligne = (
        f"{_nombre(p.prix_m2_median)} €/m² à la vente"
        f"{' à ' if p.niveau == 'commune' else ' dans '}{lieu}"
        f" (médiane de {_nombre(p.nb_ventes)} ventes, quartiles {_nombre(p.prix_m2_q1)} à"
        f" {_nombre(p.prix_m2_q3)} ; dernière vente en {p.derniere_vente:%m/%Y} ; DVF)"
    )
    if surface_m2:
        bas, haut = p.prix_m2_q1 * surface_m2, p.prix_m2_q3 * surface_m2
        ligne += (
            f". Pour {_nombre(surface_m2)} m² : environ"
            f" {_euros_arrondis(p.prix_m2_median * surface_m2)} à l'achat"
            f" (de {_euros_arrondis(bas)} à {_euros_arrondis(haut)})"
        )
    return ligne


def ligne_continuite(c: Continuite) -> str:
    def pct(valeur: float) -> str:
        return f"{_nombre(valeur, 1)} %"

    ligne = (
        f"Qualité de fourniture (Enedis, {c.derniere_annee}) : {pct(c.part_mal_alimentes_pct)}"
        " des clients du département au-delà des seuils de coupure"
    )
    if c.mediane_nationale_pct is not None:
        ligne += f" (médiane des départements : {pct(c.mediane_nationale_pct)})"
    ligne += (
        ", au-dessus du seuil réglementaire de 3 %"
        if c.depasse_seuil
        else ", sous le seuil réglementaire de 3 %"
    )
    if c.moyenne_5_ans_pct is not None:
        ligne += f" ; {pct(c.moyenne_5_ans_pct)} en moyenne sur cinq ans"
        if c.annees_au_dela_seuil_5_ans:
            ligne += f", seuil dépassé {c.annees_au_dela_seuil_5_ans} année(s) sur cinq"
    return ligne + "."


def _part_va(va: PartValeurAjoutee) -> str:
    tarif = _nombre(va.accise.tarif_eur_mwh, 2)
    phrase = (
        f"facture {_pourcentage(va.part_facture)} ; électro-intensité"
        f" {_pourcentage(va.electro_intensite)} (accise au tarif normal de {tarif} €/MWh"
        f" depuis le {va.accise.date_debut:%d/%m/%Y}, rapportée à la valeur ajoutée)"
    )
    if va.palier is None:
        phrase += ", sous le palier de 0,5 % : pas de taux réduit d'accise à ce titre"
    else:
        palier = _nombre(va.palier * 100, 3).rstrip("0")
        phrase += (
            f", au-dessus du palier de {palier} % : taux réduit d'accise possible"
            f" (de 0,5 à 12 €/MWh au lieu de {tarif}) si l'activité principale relève de"
            " l'industrie, de l'énergie ou de l'eau (sections B à E de la NACE)"
        )
    if va.accise.a_reverifier():
        phrase += (
            ". Barème d'accise peut-être périmé (indexé chaque 1er février) : mettre à jour le"
            " seed accise_electricite"
        )
    return phrase


def _part_ca(part: PartChiffreAffaires) -> str:
    phrase = (
        f"{_pourcentage(part.part)} ({_euros_arrondis(part.facture_eur)} sur"
        f" {_euros_arrondis(part.chiffre_affaires_eur)})"
    )
    if part.part_bas is not None and part.part_haut is not None:
        phrase += (
            f", de {_pourcentage(part.part_bas)} à {_pourcentage(part.part_haut)}"
            " avec les scénarios à 3 ans"
        )
    # Une facture au-delà du chiffre d'affaires trahit presque toujours une erreur d'unité
    if part.part > 1:
        phrase += ". Facture supérieure au chiffre d'affaires : --ca est en euros, --conso en MWh"
    return phrase


def _pourcentage(part: float) -> str:
    return f"{_nombre(part * 100, 2 if part < 0.01 else 1)} %"


def _prix_court(d: Diagnostic) -> str:
    p = d.prix
    if p is None and d.conso_mwh is not None:
        return f"non publié par Eurostat pour {_nombre_court(d.conso_mwh)} MWh/an"
    if p is None:
        return "non disponible (lancer la commande prix, puis dbt build)"
    periode = f"{_semestre(p.dernier_semestre, precedent=True)} – {_semestre(p.dernier_semestre)}"
    prix = f"{_nombre(p.prix_reference_eur_kwh, 4)} €/kWh HTVA"
    if p.conso_mwh is None:
        return (
            f"{prix} pour une petite entreprise (tranche {p.code_tranche}, {p.libelle_tranche} ;"
            f" Eurostat, {periode}). Préciser --conso pour la tranche et la facture de"
            " l'entreprise"
        )
    return (
        f"{prix}, soit environ {_euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))} par an"
        f" pour {_nombre_court(p.conso_mwh)} MWh (tranche {p.code_tranche} ; Eurostat, {periode})"
    )


def formater_essentiel(diagnostic: Diagnostic) -> str:
    lignes = [diagnostic.territoire.adresse, *entete(diagnostic), ""]
    lignes += [contenu for _, contenu in essentiel(diagnostic).elements]
    return "\n".join(lignes)


# Au-delà, une zone prioritaire RTE n'aide plus un projet à cette adresse
DISTANCE_ZONE_UTILE_KM = 50


@dataclass(frozen=True, slots=True)
class Critere:
    nom: str
    # `ok`, `attention` (à vérifier avant de s'engager), `inconnu` (donnée manquante), `info`
    etat: str
    valeur: str
    detail: str


ETATS_CRITERE = {
    "ok": "favorable",
    "attention": "à vérifier",
    "inconnu": "non évalué",
    "info": "pour information",
}


def verdict(diagnostic: Diagnostic) -> list[Critere]:
    return [
        _critere_raccordement(diagnostic),
        _critere_risques(diagnostic),
        _critere_carbone(diagnostic),
        _critere_facture(diagnostic),
        _critere_foncier(diagnostic),
    ]


def _critere_raccordement(d: Diagnostic) -> Critere:
    if d.seuil is None:
        return Critere(
            "Raccordement",
            "inconnu",
            "puissance non précisée",
            "Préciser --puissance (en MW) pour savoir qui raccorde : Enedis ou RTE.",
        )
    valeur = f"{d.seuil.domaine_tension}, {d.seuil.gestionnaire}"
    if "RTE" not in d.seuil.gestionnaire:
        return Critere(
            "Raccordement",
            "ok",
            valeur,
            "Raccordement courant au réseau de distribution ; délai et coût fixés par l'étude"
            " du gestionnaire.",
        )
    zone = d.zone_rte
    if zone is not None and zone.distance_km <= DISTANCE_ZONE_UTILE_KM:
        annee = f" dès {zone.annee_capacite}" if zone.annee_capacite else ""
        detail = (
            f"Zone prioritaire RTE {zone.zone} ({zone.priorite}) {_distance(zone.distance_km)} :"
            f" capacité supplémentaire prévue{annee}. Étude RTE indispensable."
        )
    else:
        detail = (
            f"Aucune zone prioritaire RTE à moins de {DISTANCE_ZONE_UTILE_KM} km : délais de 2 à"
            " 7 ans annoncés, étude RTE indispensable."
        )
    return Critere("Raccordement", "attention", valeur, detail)


# Plans cités dans le verdict ; le diagnostic complet les donne tous
NB_PLANS_CITES = 3


# Risques : à vérifier seulement quand un plan de prévention en vigueur couvre la commune, car son
# règlement peut interdire ou conditionner une construction ; un risque seulement recensé informe
def _critere_risques(d: Diagnostic) -> Critere:
    plans = d.plans_prevention
    if plans is None:
        return Critere(
            "Risques",
            "inconnu",
            "plans de prévention non chargés",
            "Lancer refresh pour charger l'export GASPAR.",
        )
    if plans:
        return Critere(
            "Risques",
            "attention",
            f"{len(plans)} plan{'s' if len(plans) > 1 else ''} de prévention dans la commune",
            f"{liste_plans(plans, NB_PLANS_CITES)}. Le règlement du plan peut interdire ou"
            " conditionner la construction selon la zone : le consulter en mairie avant de"
            " s'engager.",
        )
    return Critere(
        "Risques", "info", "aucun plan de prévention dans la commune", _risques_recenses(d.risques)
    )


def _risques_recenses(r: RisquesSite | None) -> str:
    if r is None:
        return "Risques recensés non consultés : à vérifier sur georisques.gouv.fr."
    if r.maille == "adresse":
        exposes = r.selon(Exposition.EXPOSE)
        if not exposes:
            return "Aucun risque recensé à l'adresse (rapport Géorisques)."
        return f"Recensés à l'adresse : {_liste_risques(exposes)}."
    recenses = r.selon(Exposition.INCONNU)
    if not recenses:
        return "Aucun risque recensé dans la commune (export GASPAR)."
    return f"Recensés dans la commune, sans plan de prévention : {_liste_risques(recenses)}."


# « PPRI de Paris (inondation, approuvé en 2007) », les premiers seulement si `limite`
def liste_plans(plans: list[PlanPrevention], limite: int | None = None) -> str:
    cites = plans if limite is None else plans[:limite]
    texte = ", ".join(_plan_court(p) for p in cites)
    if (reste := len(plans) - len(cites)) > 0:
        texte += f" et {reste} autre{'s' if reste > 1 else ''}"
    return texte


def _plan_court(p: PlanPrevention) -> str:
    date_etat = p.date_approbation if p.etat == "approuvé" else p.date_prescription
    etat = f"{p.etat} en {date_etat.year}" if date_etat else p.etat
    familles = f"{p.risques.lower()}, " if p.risques else ""
    return f"{p.libelle_plan} ({familles}{etat})"


def _critere_carbone(d: Diagnostic) -> Critere:
    c = d.carbone
    if c is None:
        return Critere("Carbone", "inconnu", "non disponible", "Lancer refresh.")
    detail = (
        f"{c.systeme_electrique} : {'sous le' if c.est_decarbone else 'au-dessus du'} seuil de"
        f" {_nombre_court(c.seuil_decarbone_g_co2e_kwh)} gCO₂e/kWh de la taxonomie européenne."
    )
    if heures := heures_carbone(c):
        detail += f" {heures[0].upper()}{heures[1:]}."
    return Critere(
        "Carbone",
        "ok" if c.est_decarbone else "attention",
        f"{_nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh",
        detail,
    )


def _critere_facture(d: Diagnostic) -> Critere:
    p = d.prix
    if p is None:
        return Critere("Facture", "inconnu", "prix non disponible", _prix_court(d))
    prix = f"{_nombre(p.prix_reference_eur_kwh, 4)} €/kWh HTVA ({p.code_tranche})"
    if p.conso_mwh is None:
        return Critere(
            "Facture", "info", prix, "Préciser --conso (MWh par an) pour la facture annuelle."
        )
    detail = f"{prix}, identique partout en France."
    if (a := d.acheminement) is not None:
        detail += f" Dont acheminement {_nombre(a.prix_eur_mwh, 1)} €/MWh (TURPE 7)."
    if (part := d.part_chiffre_affaires) is not None:
        detail += f" {_pourcentage(part.part)} du chiffre d'affaires."
    if (va := d.part_valeur_ajoutee) is not None and va.palier is not None:
        detail += (
            f" Électro-intensité {_pourcentage(va.electro_intensite)} : taux réduit d'accise"
            " possible pour une activité industrielle."
        )
    return Critere(
        "Facture",
        "info",
        f"{_euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))} HTVA par an",
        detail,
    )


def ligne_urbanisme(zone: ZoneUrbanisme) -> str:
    nom = f"{zone.nom_type} {zone.libelle}".strip()
    if zone.libelle_long and zone.libelle_long != zone.libelle:
        nom += f" ({zone.libelle_long})"
    phrase = f"{nom}, document {zone.document or 'non identifié'}"
    if zone.date_validite:
        phrase += f" validé le {zone.date_validite}"
    if zone.est_inconstructible_activite:
        phrase += (
            " : hors exception locale (STECAL), l'activité industrielle ou logistique n'y est"
            " pas permise"
        )
    else:
        phrase += " : destination des constructions à confirmer au règlement de la zone"
    if zone.url_reglement:
        phrase += f" ({zone.url_reglement})"
    return phrase


def ligne_eau(eau: SituationEau) -> str:
    if not eau.zones:
        return "aucun arrêté de restriction des usages de l'eau en vigueur à cette adresse"
    zones = ", ".join(
        f"{LIBELLES_TYPE.get(z.type_zone, z.type_zone)} ({LIBELLES_NIVEAU[z.niveau]})"
        for z in sorted(eau.zones, key=lambda z: -z.rang)
    )
    fins = [z.date_fin for z in eau.zones if z.date_fin]
    phrase = f"niveau {LIBELLES_NIVEAU[eau.niveau_max]} en vigueur : {zones}"
    if fins:
        phrase += f", jusqu'au {max(fins):%d/%m/%Y}"
    if eau.est_contraignante:
        phrase += (
            " ; à ce niveau, les usages de l'eau des entreprises peuvent être très réduits"
            " ou suspendus selon l'arrêté"
        )
    else:
        phrase += (
            " ; à ce niveau, l'arrêté appelle surtout à la sobriété : en lire les usages encadrés"
        )
    return phrase


LIBELLES_ACCES = {"port": "port", "aeroport": "aéroport", "rail": "rail-route"}


def ligne_acces(acces: list[Acces]) -> str:
    morceaux = []
    for a in acces:
        if a.type_acces == "autoroute":
            morceaux.append(f"autoroute {a.nom} {_distance(a.distance_vol_km)} à vol d'oiseau")
            continue
        nom = f"{LIBELLES_ACCES[a.type_acces]} {a.nom}"
        if a.duree_min is None or a.distance_route_km is None:
            morceaux.append(
                f"{nom} {_distance(a.distance_vol_km)} à vol d'oiseau (trajet non calculé)"
            )
        else:
            morceaux.append(
                f"{nom} à {_nombre(a.duree_min)} min ({_nombre(a.distance_route_km)} km)"
            )
    if not any(a.type_acces == "autoroute" for a in acces):
        morceaux.insert(0, "aucune autoroute à moins de 10 km")
    return " ; ".join(morceaux)


def ligne_solaire(batiment: Batiment | None, productible: Productible) -> str:
    if batiment is None:
        lieu = "aucun bâtiment de la BD TOPO à moins de 60 m de l'adresse"
    else:
        qui = batiment.usage.lower() if batiment.usage else "usage inconnu"
        taille = f"{_nombre(batiment.emprise_m2)} m² au sol"
        if batiment.hauteur_m:
            taille += f", {_nombre(batiment.hauteur_m, 1)} m de haut"
        position = (
            "sur l'adresse" if batiment.contient_adresse else f"à {_nombre(batiment.distance_m)} m"
        )
        lieu = f"bâtiment {qui} {position} ({taille})"
    angles = ""
    if productible.inclinaison_deg is not None:
        angles = f", inclinaison {_nombre(productible.inclinaison_deg)}° optimale"
    return (
        f"{lieu} ; productible de {_nombre(productible.kwh_par_kwc_an)} kWh par kWc et par an"
        f" (panneaux fixes{angles}, pertes de {PERTES_PCT} %, PVGIS)"
    )


def ligne_zones_activite(nb: int, zones: list[ZoneActivite]) -> str:
    if not nb:
        return (
            f"aucune zone d'activité ou industrielle à moins de {RAYON_ZONES_ACTIVITE_KM} km"
            " (BD TOPO)"
        )
    details = [
        f"{z.nom} ({_nombre(z.surface_ha, 1 if z.surface_ha < 10 else 0)} ha, "
        + ("l'adresse est dedans" if z.contient_adresse else _distance(z.distance_km))
        + ")"
        for z in zones
    ]
    pluriel = "s" if nb > 1 else ""
    return (
        f"{nb} zone{pluriel} d'activité ou industrielle{pluriel} à moins de"
        f" {RAYON_ZONES_ACTIVITE_KM} km ; les plus proches : {' ; '.join(details)}"
    )


def ligne_friches(nb: int, friches: list[Friche]) -> str:
    if not nb:
        return (
            f"aucune friche de 1 ha ou plus référencée à moins de {RAYON_FRICHES_KM} km"
            " (inventaire déclaratif : une friche non référencée n'y figure pas)"
        )
    pluriel = "s" if nb > 1 else ""
    details = []
    for f in friches:
        zone = f", zone {f.type_zone_urbanisme}" if f.type_zone_urbanisme else ""
        statut = f", {f.statut}" if f.statut else ""
        details.append(
            f"{f.nom} à {f.commune or 'commune inconnue'} ({f.type_friche}, "
            f"{_nombre(f.surface_m2 / 10_000, 1)} ha{zone}{statut}) {_distance(f.distance_km)}"
        )
    return (
        f"{nb} friche{pluriel} de 1 ha ou plus à moins de {RAYON_FRICHES_KM} km ; les plus"
        f" proches : {' ; '.join(details)}"
    )


def _critere_foncier(d: Diagnostic) -> Critere:
    site = d.site_cle_en_main
    if site is None:
        valeur, detail = "aucun site clé en main", "Dans ce territoire."
    else:
        valeur = f"site clé en main {_distance(site.distance_km)}"
        detail = f"{site.commune}, annoncé prêt en {site.maturite_site}."
    etat = "info"
    if d.zones_activite and d.zones_activite[0].contient_adresse:
        detail += f" Adresse dans la zone d'activité {d.zones_activite[0].nom}."
    if d.nb_friches is not None:
        detail += f" {d.nb_friches} friche(s) de 1 ha ou plus à moins de {RAYON_FRICHES_KM} km."
    if (zone := d.urbanisme) is not None:
        detail += f" Zonage : {ligne_urbanisme(zone)}."
        if zone.est_inconstructible_activite:
            etat = "attention"
    return Critere("Foncier", etat, valeur, detail)


def phrase_verdict(criteres: list[Critere]) -> str:
    a_verifier = [c.nom.lower() for c in criteres if c.etat == "attention"]
    inconnus = [c.nom.lower() for c in criteres if c.etat == "inconnu"]
    if a_verifier:
        phrase = "À vérifier avant de s'engager : " + ", ".join(a_verifier)
    else:
        phrase = "Rien de bloquant dans les données publiques"
    if inconnus:
        phrase += " (non évalué : " + ", ".join(inconnus) + ")"
    return phrase + "."


def essentiel_en_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    d = diagnostic
    t = d.territoire
    p = d.prix
    c = d.carbone
    return {
        "adresse": t.adresse,
        "code_insee_commune": d.code_insee_commune,
        "commune": t.commune,
        "code_departement": d.code_departement,
        "code_insee_region": d.code_insee_region,
        "region": d.region,
        "est_zni": d.est_zni,
        "precision_geocodage": t.precision,
        "donnees_chargees": {table: jour.isoformat() for table, jour in d.chargements.items()},
        "prix": None
        if p is None
        else {
            "prix_htva_eur_kwh": p.prix_reference_eur_kwh,
            "code_tranche": p.code_tranche,
            "libelle_tranche": p.libelle_tranche,
            "est_tranche_par_defaut": p.est_tranche_par_defaut,
            "conso_mwh": p.conso_mwh,
            "facture_htva_eur_an": None
            if p.conso_mwh is None
            else p.facture_eur(p.prix_reference_eur_kwh),
            "dernier_semestre": p.dernier_semestre,
            "source": "Eurostat nrg_pc_205",
        },
        "acheminement": None if d.acheminement is None else asdict(d.acheminement),
        "part_chiffre_affaires": None
        if d.part_chiffre_affaires is None
        else asdict(d.part_chiffre_affaires),
        "part_valeur_ajoutee": None
        if d.part_valeur_ajoutee is None
        else asdict(d.part_valeur_ajoutee),
        "activite": None
        if d.activite is None
        else {"code": d.activite, **asdict(ACTIVITES[d.activite])},
        "carbone": None
        if c is None
        else {
            "est_decarbone": c.est_decarbone,
            "intensite_acv_g_co2e_kwh": c.intensite_acv_g_co2e_kwh,
            "seuil_decarbone_g_co2e_kwh": c.seuil_decarbone_g_co2e_kwh,
            "systeme_electrique": c.systeme_electrique,
            "source": f"ADEME Base Carbone, {c.validite_source}",
            "profil_horaire": [
                {"heure_locale": h, "intensite_estimee_g_co2e_kwh": round(i, 1)}
                for h, i in c.profil_horaire
            ],
            "annee_profil": c.annee_profil,
        },
        "a_savoir": A_SAVOIR,
        "entreprises_adresse": None
        if d.entreprises_adresse is None
        else asdict(d.entreprises_adresse),
        "courbe": None if d.courbe is None else asdict(d.courbe),
        "urbanisme": None if d.urbanisme is None else asdict(d.urbanisme),
        "eau": None
        if d.eau is None
        else {
            "niveau_max": d.eau.niveau_max,
            "est_contraignante": d.eau.est_contraignante,
            "zones": [asdict(z) for z in d.eau.zones],
            "source": "VigiEau",
        },
        "solaire": None
        if not d.solaire_consulte or d.productible is None
        else {
            "batiment": None if d.batiment is None else asdict(d.batiment),
            "productible": asdict(d.productible),
            "source": "BD TOPO (IGN) et PVGIS (Commission européenne)",
        },
        "acces_routier": None
        if d.acces_routier is None
        else {
            "acces": [asdict(a) for a in d.acces_routier],
            "source": "BD TOPO et Géoplateforme (IGN)",
        },
        "zones_activite": None
        if d.nb_zones_activite is None
        else {
            "nb_dans_le_rayon": d.nb_zones_activite,
            "rayon_km": RAYON_ZONES_ACTIVITE_KM,
            "plus_proches": [asdict(z) for z in d.zones_activite],
            "source": "BD TOPO (IGN)",
        },
        "friches": None
        if d.nb_friches is None
        else {
            "nb_dans_le_rayon": d.nb_friches,
            "rayon_km": RAYON_FRICHES_KM,
            "plus_proches": [asdict(f) for f in d.friches],
            "source": "Cartofriches (Cerema)",
        },
        "locaux": None
        if d.prix_locaux is None
        else {**asdict(d.prix_locaux), "surface_m2": d.surface_m2, "source": "DVF (DGFiP)"},
        "plans_prevention": None
        if d.plans_prevention is None
        else [asdict(p) for p in d.plans_prevention],
        "verdict": {
            "phrase": phrase_verdict(criteres := verdict(d)),
            "criteres": [asdict(c) for c in criteres],
        },
        "limites": {
            code: d.limites[code]
            for code, present in (
                ("prix_entreprise", True),
                ("carbone_territoire", True),
                ("part_chiffre_affaires", d.part_chiffre_affaires is not None),
                ("acheminement_turpe", d.acheminement is not None),
                ("part_valeur_ajoutee", d.part_valeur_ajoutee is not None),
                ("accise_activite", d.activite is not None),
                ("entreprises_enedis", d.entreprises_adresse is not None),
                ("prix_locaux", d.prix_locaux is not None),
                ("friches", d.nb_friches is not None),
                ("acces_routier", d.acces_routier is not None),
                ("solaire", d.solaire_consulte and d.productible is not None),
                ("zones_activite", d.nb_zones_activite is not None),
                ("restrictions_eau", d.eau is not None),
                ("zonage_urbanisme", d.urbanisme_consulte and d.urbanisme_erreur is None),
            )
            if present and code in d.limites
        },
    }


def sections(diagnostic: Diagnostic) -> list[Section]:
    d = diagnostic
    resultat: list[Section] = []

    def bloc(titre: str) -> Section:
        resultat.append(Section(titre, []))
        return resultat[-1]

    def texte(section: Section, contenu: str) -> None:
        section.elements.append(("texte", contenu))

    def limite(section: Section, indicateur: str) -> None:
        if contenu := d.limites.get(indicateur):
            section.elements.append(("limite", contenu))

    s = bloc(
        "1. Pression industrielle locale"
        f" (sites raccordés au réseau de transport, {d.annee_pression or '?'})"
    )
    if d.commune_a_des_sites:
        minorant = " (minorant : secret statistique)" if d.conso_commune_est_minorant else ""
        texte(
            s,
            f"Commune     : {d.nb_sites_commune} site(s),"
            f" {_gwh(d.conso_industrielle_commune_gwh or 0)}/an{minorant}",
        )
    else:
        texte(s, "Commune     : aucun site raccordé au transport → repli sur le département")
    if d.nb_sites_departement:
        rang = (
            f" — rang {d.rang_pression_departement} sur {d.nb_departements_classes} départements"
            if d.rang_pression_departement
            else ""
        )
        texte(
            s,
            f"Département : {d.nb_sites_departement} site(s) dans {d.nb_communes_avec_site}"
            f" commune(s), {_gwh(d.conso_industrielle_departement_gwh)}/an{rang}",
        )
    else:
        texte(s, "Département : aucun site raccordé au transport")
    limite(s, "pression_industrielle")
    if (entreprises := d.entreprises_commune) is not None:
        texte(s, f"Entreprises raccordées à Enedis : {ligne_entreprises_commune(entreprises)}")
    if (deja := d.entreprises_adresse) is not None:
        texte(s, f"À cette adresse : {ligne_entreprises_adresse(deja)}")
    if entreprises is not None or deja is not None:
        limite(s, "entreprises_enedis")

    if d.taux_couverture_region is None:
        zni = " (zone non interconnectée)" if d.est_zni else ""
        s = bloc("2. Tension du réseau régional")
        texte(s, f"Aucun équilibre régional publié par RTE pour cette région{zni}.")
    else:
        debut = f"{d.equilibre_mois_debut:%m/%Y}" if d.equilibre_mois_debut else "?"
        fin = f"{d.equilibre_mois_fin:%m/%Y}" if d.equilibre_mois_fin else "?"
        s = bloc(f"2. Tension du réseau régional ({debut} – {fin}, {d.equilibre_nb_mois} mois)")
        texte(
            s,
            f"Production {_gwh(d.production_region_gwh or 0)},"
            f" consommation {_gwh(d.consommation_region_gwh or 0)}"
            f" : couverture {_nombre(d.taux_couverture_region * 100)} %"
            f" → région {_STATUTS.get(d.statut_equilibre or '', '?')}",
        )
        texte(
            s,
            f"Rang de dépendance : {d.rang_dependance_region} sur {d.nb_regions_classees}"
            " (1 = la plus déficitaire)",
        )
        if d.est_zni:
            texte(
                s,
                "Zone non interconnectée : le réseau n'est relié au continent que par des"
                " liaisons limitées, le déficit y pèse bien davantage qu'en métropole.",
            )
    limite(s, "equilibre_regional")
    if d.puissance_a_compenser_mw is not None:
        texte(
            s,
            f"Contraintes d'évacuation (RTE) : {_nombre(d.puissance_enr_installee_mw or 0)} MW"
            f" d'EnR installés ou en projet, {_nombre(d.puissance_a_compenser_mw)} MW à"
            f" compenser, {_nombre(d.energie_non_evacuee_mwh or 0)} MWh non évacués par an",
        )
        limite(s, "contraintes_reseau")
    if d.est_zni and not d.nb_postes_rte:
        texte(
            s,
            "Contraintes prospectives du réseau de transport : non publiées, le réseau de ce"
            " territoire n'est pas exploité par RTE.",
        )
    else:
        if d.nb_contraintes_departement == 0:
            texte(
                s,
                "Contraintes prospectives (RTE, d'ici 3 à 5 ans) : aucune sur les ouvrages reliés"
                " aux postes du département.",
            )
        else:
            frequentes = d.nb_contraintes_frequentes_departement or 0
            texte(
                s,
                f"Contraintes prospectives (RTE, d'ici 3 à 5 ans) : {d.nb_contraintes_departement}"
                " sur des ouvrages reliés aux postes du département, dont"
                f" {frequentes} plus de 75 fois par an.",
            )
            texte(
                s,
                "Les plus fréquentes : " + " ; ".join(_contrainte(c) for c in d.contraintes),
            )
        limite(s, "contraintes_prospectives")

    s = bloc("3. Raccordement")
    if d.seuil is not None:
        texte(
            s,
            f"Puissance {_nombre_court(d.seuil.puissance_mw)} MW → {d.seuil.domaine_tension},"
            f" {d.seuil.gestionnaire} : {d.seuil.commentaire}",
        )
        limite(s, "seuil_raccordement")
        if (a := d.acheminement) is not None:
            texte(
                s,
                f"Acheminement, détail : {_euros_arrondis(a.puissance_eur)} pour la puissance"
                f" souscrite, {_euros_arrondis(a.energie_eur)} pour l'énergie,"
                f" {_euros_arrondis(a.gestion_comptage_eur)} de gestion et comptage par an",
            )
        elif d.seuil.domaine_turpe == "htb":
            texte(s, "Acheminement : grille TURPE HTB de RTE, non chiffrée ici.")
        elif d.seuil.domaine_turpe in DOMAINES_TURPE:
            texte(s, "Acheminement : préciser --conso (MWh par an) pour le chiffrer.")
        if d.seuil.domaine_turpe != "bt_inf36":
            limite(s, "acheminement_turpe")
    else:
        texte(s, "Préciser --puissance (en MW) pour connaître le domaine de tension.")
    if (c := d.continuite) is not None:
        texte(s, ligne_continuite(c))
        limite(s, "continuite_alimentation")
    elif "enedis_continuite_alimentation" in d.chargements:
        texte(
            s,
            "Qualité de fourniture : non publiée pour ce département (réseau de distribution"
            " hors Enedis).",
        )
    if not d.nb_postes_rte:
        texte(
            s,
            "Postes RTE dans le département : aucun"
            + (" (réseau de ce territoire non exploité par RTE)." if d.est_zni else "."),
        )
    else:
        projet = f" ; {d.nb_postes_en_projet} en projet" if d.nb_postes_en_projet else ""
        texte(
            s,
            f"Postes RTE en exploitation dans le département : {d.nb_postes_rte}, dont"
            f" {d.nb_postes_htb1 or 0} en HTB1 (63 à 150 kV), {d.nb_postes_htb2 or 0} en HTB2"
            f" (225 kV) et {d.nb_postes_htb3 or 0} en HTB3 (400 kV){projet}",
        )
        limite(s, "postes_rte")
    if d.est_zni:
        texte(
            s,
            "Zones industrielles prioritaires de RTE : aucune, le réseau de ce territoire n'est"
            " pas exploité par RTE.",
        )
    elif d.zone_rte is not None:
        z = d.zone_rte
        texte(
            s,
            f"Zone industrielle prioritaire de RTE la plus proche : {z.zone} ({z.priorite}),"
            f" {_distance(z.distance_km)} ({z.point_reference}) : {_calendrier_zone(z)} ;"
            f" ouvrages prévus : {z.ouvrages}",
        )
        if (p3 := d.zone_rte_p3) is not None:
            texte(
                s,
                f"Plus proche encore, {p3.zone} ({_distance(p3.distance_km)}) : exemple de zone"
                " où RTE pourrait accélérer le réseau (P3), sans date et sous engagement"
                " financier des industriels.",
            )
        limite(s, "zones_raccordement_rte")
    if d.site_cle_en_main is not None:
        site = d.site_cle_en_main
        texte(
            s,
            f"Site industriel clé en main (France 2030) le plus proche : {site.commune},"
            f" {_distance(site.distance_km)} ; annoncé prêt en {site.maturite_site}",
        )
        limite(s, "sites_cles_en_main")
    else:
        texte(s, "Site industriel clé en main (France 2030) : aucun dans ce territoire.")
    limite(s, "capacite_raccordement")

    s = bloc("4. Prix de l'électricité pour l'entreprise (identique sur tout le territoire)")
    if d.prix is None and d.conso_mwh is not None:
        texte(
            s,
            f"Aucun prix publié par Eurostat pour la tranche de {_nombre_court(d.conso_mwh)}"
            " MWh/an.",
        )
    elif d.prix is None:
        texte(s, "Aucun prix Eurostat chargé : lancer la commande prix, puis dbt build.")
    else:
        p = d.prix
        if p.conso_mwh is None:
            texte(
                s,
                f"Consommation non précisée → tranche {p.code_tranche} ({p.libelle_tranche}),"
                " celle des TPE et des commerces, prix moyen constaté par Eurostat. Préciser"
                " --conso (consommation annuelle en MWh, lue sur la facture) pour la tranche de"
                " l'entreprise et sa facture.",
            )
        else:
            texte(
                s,
                f"Consommation {_nombre_court(p.conso_mwh)} MWh/an → tranche {p.code_tranche}"
                f" ({p.libelle_tranche}), prix moyen constaté par Eurostat",
            )
        taxes = (
            f", dont {_nombre(p.taxes_reference_eur_kwh, 4)} €/kWh de taxes non récupérables"
            if p.taxes_reference_eur_kwh is not None
            else ""
        )
        facture = (
            ""
            if p.conso_mwh is None
            else f" → facture d'environ {_euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))}"
            " par an"
        )
        texte(
            s,
            f"12 derniers mois ({_semestre(p.dernier_semestre, precedent=True)} –"
            f" {_semestre(p.dernier_semestre)}) : {_nombre(p.prix_reference_eur_kwh, 4)} €/kWh"
            f" HTVA{taxes}{facture}",
        )
        texte(
            s,
            f"Repères depuis {_semestre(p.premier_semestre)} : moyenne sur 5 ans"
            f" {_nombre(p.prix_moyen_5_ans_eur_kwh, 4)} €/kWh, plus bas"
            f" {_nombre(p.prix_min_eur_kwh, 4)} ({_semestre(p.semestre_min)}), plus haut"
            f" {_nombre(p.prix_max_eur_kwh, 4)} ({_semestre(p.semestre_max)})",
        )
        bas, centre, haut = (
            p.prix_3_ans_bas_eur_kwh,
            p.prix_3_ans_central_eur_kwh,
            p.prix_3_ans_haut_eur_kwh,
        )
        if bas is not None and centre is not None and haut is not None:
            factures = (
                ""
                if p.conso_mwh is None
                else f" → facture de {_euros_arrondis(p.facture_eur(bas))} à"
                f" {_euros_arrondis(p.facture_eur(haut))} par an"
                f" ({_euros_arrondis(p.facture_eur(centre))} au centre)"
            )
            statut = "" if p.est_fourchette_validee else ", fourchette indicative"
            texte(
                s,
                f"Prix moyen des 3 prochaines années{statut}, d'après l'historique de la tranche"
                " et les erreurs passées de la méthode :"
                f" de {_nombre(bas, 4)} à {_nombre(haut, 4)} €/kWh, {_nombre(centre, 4)} au"
                f" centre{factures}",
            )
            texte(s, _controle_sur_le_passe(p))
        else:
            texte(
                s,
                "Historique trop court pour cette tranche : pas de scénario à 3 ans.",
            )
        if d.est_zni:
            texte(
                s,
                "En Corse et outre-mer, les tarifs réglementés sont alignés sur ceux du continent"
                " (péréquation) : ce prix moyen national reste le repère.",
            )
        limite(s, "prix_entreprise")
        if (part := d.part_chiffre_affaires) is not None:
            texte(s, f"Facture rapportée au chiffre d'affaires : {_part_ca(part)}")
            limite(s, "part_chiffre_affaires")
        if (va := d.part_valeur_ajoutee) is not None:
            texte(s, f"Part de la valeur ajoutée : {_part_va(va)}")
            limite(s, "part_valeur_ajoutee")
        if d.activite is not None:
            texte(s, f"Activité : {ligne_activite(d.activite)}")
            limite(s, "accise_activite")

    s = bloc("5. Carbone du réseau et coût horaire d'un profil type")
    if d.carbone is not None:
        texte(s, _verdict_carbone(d.carbone))
        if heures := heures_carbone(d.carbone):
            texte(s, f"Heure par heure : {heures}")
        limite(s, "carbone_territoire")
    if d.courbe is not None:
        texte(s, f"Votre courbe de charge : {ligne_courbe(d.courbe)}")
    if d.cout is None:
        texte(s, "Aucun mois complet et tarifé : charger les données nationales récentes.")
    else:
        c = d.cout
        texte(
            s,
            f"{c.libelle}, {_MOIS[c.mois.month - 1]} {c.mois.year},"
            " option Heures Creuses du tarif bleu non résidentiel",
        )
        texte(
            s,
            f"{_nombre(c.conso_kwh)} kWh, dont {_nombre(c.part_hc * 100)} % en heures creuses :"
            f" {_nombre(c.cout_htva_eur)} € HTVA, soit {_nombre(c.prix_moyen_htva_eur_kwh, 4)}"
            f" €/kWh ; {_nombre(c.intensite_moyenne_g_kwh)} gCO₂/kWh en moyenne",
        )
        texte(
            s,
            f"Décaler {_nombre(c.kwh_decales)} kWh flexibles vers les heures creuses :"
            f" {_nombre(c.gain_decalage_eur)} € économisés, {_co2_evite(c.co2_evite_kg)}",
        )
        if d.est_zni:
            texte(
                s,
                "Barème de métropole continentale : la Corse et l'outre-mer ont leurs propres"
                " barèmes réglementés.",
            )
    limite(s, "cout_profil")

    s = bloc("6. Risques et contraintes du site : Géorisques et eau (arrêtés de restriction)")
    if d.plans_prevention is not None:
        texte(
            s,
            "Plans de prévention en vigueur dans la commune : "
            + (liste_plans(d.plans_prevention) or "aucun"),
        )
        limite(s, "plans_prevention")
    if d.risques_erreur is not None:
        texte(
            s,
            "Géorisques n'a pas répondu, même après plusieurs tentatives, et l'export GASPAR"
            " n'est pas chargé (lancer refresh) : risques non évalués. À consulter sur"
            " georisques.gouv.fr avant tout engagement.",
        )
    elif d.risques is None:
        texte(s, "Non consulté (option --sans-risques).")
    elif d.risques.maille == "commune":
        r = d.risques
        source = f"l'export GASPAR du {r.millesime:%d/%m/%Y}" if r.millesime else "l'API Géorisques"
        texte(
            s,
            "Rapport à l'adresse indisponible (Géorisques n'a pas répondu) : risques recensés"
            f" dans la commune d'après {source}, sans évaluation à l'adresse.",
        )
        for famille, nom in (("naturel", "Naturels"), ("technologique", "Technologiques")):
            recenses = r.selon(Exposition.INCONNU, famille)
            texte(
                s,
                f"{nom}, recensés dans la commune : {_liste_risques(recenses) or 'aucun'}",
            )
        limite(s, "risques_site")
    else:
        r = d.risques
        if not d.territoire.est_precis:
            texte(
                s,
                "Adresse résolue à la commune seulement : le statut « à l'adresse » ne vaut que"
                " pour le point retenu par le géocodage.",
            )
        for famille, nom in (("naturel", "Naturels"), ("technologique", "Technologiques")):
            exposes = r.selon(Exposition.EXPOSE, famille)
            texte(s, f"{nom}, à l'adresse : {_liste_risques(exposes) or 'aucun recensé'}")
        if inconnus := r.selon(Exposition.INCONNU):
            texte(
                s,
                "Présents dans la commune, non évalués à l'adresse : " + _liste_risques(inconnus),
            )
        if ecartes := r.selon(Exposition.NON_EXPOSE):
            texte(
                s,
                "Présents dans la commune, mais l'adresse n'est pas concernée : "
                + _liste_risques(ecartes),
            )
        if r.url:
            texte(s, f"Rapport officiel : {r.url}")
        limite(s, "risques_site")
    if d.risques is not None and any(r.code == "seisme" for r in d.risques.risques):
        limite(s, "zonage_sismique")
    if d.eau is not None:
        texte(s, f"Eau : {ligne_eau(d.eau)}")
        limite(s, "restrictions_eau")
    elif d.eau_erreur is not None:
        texte(s, f"Restrictions d'eau non consultées : {d.eau_erreur}")
    s = bloc("7. Foncier, desserte et prix des locaux d'activité (DVF)")
    if d.urbanisme is not None:
        texte(s, f"Zonage d'urbanisme : {ligne_urbanisme(d.urbanisme)}")
        limite(s, "zonage_urbanisme")
    elif d.urbanisme_erreur is not None:
        texte(s, f"Zonage d'urbanisme non consulté : {d.urbanisme_erreur}")
    elif d.urbanisme_consulte:
        texte(
            s,
            "Zonage d'urbanisme : aucune zone numérisée au Géoportail de l'urbanisme à cette"
            " adresse (document non publié ou non numérisé).",
        )
        limite(s, "zonage_urbanisme")
    if d.solaire_consulte and d.productible is not None:
        texte(s, f"Bâtiment et solaire : {ligne_solaire(d.batiment, d.productible)}")
        limite(s, "solaire")
    elif d.solaire_erreur is not None:
        texte(s, f"Bâtiment et solaire non évalués : {d.solaire_erreur}")
    if d.acces_routier is not None:
        texte(
            s,
            f"Accès routier (voiture, pas de profil poids lourd) : {ligne_acces(d.acces_routier)}",
        )
        limite(s, "acces_routier")
    elif d.acces_routier_erreur is not None:
        texte(s, f"Accès routier non évalué : {d.acces_routier_erreur}")
    if d.nb_zones_activite is not None:
        texte(
            s, f"Zones d'activité : {ligne_zones_activite(d.nb_zones_activite, d.zones_activite)}"
        )
        limite(s, "zones_activite")
    elif d.zones_activite_erreur is not None:
        texte(s, f"Zones d'activité non consultées : {d.zones_activite_erreur}")
    if d.nb_friches is not None:
        texte(s, f"Friches : {ligne_friches(d.nb_friches, d.friches)}")
        limite(s, "friches")
    if d.prix_locaux is not None:
        texte(s, ligne_locaux(d.prix_locaux, d.surface_m2, d.territoire.commune))
        limite(s, "prix_locaux")
    elif "ventes_locaux" in d.chargements:
        texte(s, "Aucune vente de local d'activité recensée dans la commune ni son département.")
    else:
        texte(s, "Non chargé : lancer refresh pour récupérer les ventes DVF.")
    s = bloc("8. Brancher ici ? Les cinq critères")
    criteres = verdict(d)
    for c in criteres:
        texte(s, f"{c.nom} ({ETATS_CRITERE[c.etat]}) : {c.valeur}. {c.detail}")
    texte(s, phrase_verdict(criteres))
    return resultat


def _liste_risques(risques: list[Risque]) -> str:
    return ", ".join(f"{r.libelle} ({r.niveau})" if r.niveau else r.libelle for r in risques)


_GASPAR_SQL = """
select code_insee_commune, num_risque, libelle_risque, millesime
from gold.fct_risques_commune
where code_insee_commune in (select unnest(?))
order by code_insee_commune, num_risque
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


# Ajoute le rapport Géorisques de l'adresse ; ne fait jamais échouer le diagnostic
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


# Ajoute la zone d'urbanisme de l'adresse ; ne fait jamais échouer le diagnostic
def completer_urbanisme(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        zone = lire_zone_urbanisme(latitude=t.latitude, longitude=t.longitude, settings=settings)
    except UrbanismeError as error:
        logger.warning("zonage d'urbanisme non consulte : %s", error)
        return replace(diagnostic, urbanisme_erreur=str(error))
    return replace(diagnostic, urbanisme=zone, urbanisme_consulte=True)


# Ajoute le bâtiment de l'adresse et le productible solaire ; ne fait jamais échouer le diagnostic
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


# Ajoute les temps d'accès à l'autoroute et aux pôles de fret ; ne fait jamais échouer le diagnostic
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


# Ajoute les zones d'activité proches de l'adresse ; ne fait jamais échouer le diagnostic
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


# Ajoute les arrêtés sécheresse de l'adresse ; ne fait jamais échouer le diagnostic
def completer_eau(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        eau = lire_situation_eau(latitude=t.latitude, longitude=t.longitude, settings=settings)
    except EauError as error:
        logger.warning("restrictions d'eau non consultees : %s", error)
        return replace(diagnostic, eau_erreur=str(error))
    return replace(diagnostic, eau=eau)


def formater_diagnostic(diagnostic: Diagnostic) -> str:
    lignes = [f"DIAGNOSTIC D'IMPLANTATION — {diagnostic.territoire.adresse}"]
    lignes += entete(diagnostic)
    for section in [essentiel(diagnostic), *sections(diagnostic)]:
        lignes += ["", section.titre]
        lignes += [
            f"   ↳ Limite : {contenu}" if nature == "limite" else f"   {contenu}"
            for nature, contenu in section.elements
        ]
    return "\n".join(lignes)


def diagnostic_en_dict(diagnostic: Diagnostic) -> dict[str, Any]:

    def convertir(valeur: Any) -> Any:
        if isinstance(valeur, date):
            return valeur.isoformat()
        if isinstance(valeur, dict):
            return {cle: convertir(v) for cle, v in valeur.items()}
        if isinstance(valeur, list):
            return [convertir(v) for v in valeur]
        return valeur

    contenu = asdict(diagnostic)
    contenu["commune_a_des_sites"] = diagnostic.commune_a_des_sites
    part = diagnostic.part_chiffre_affaires
    contenu["part_chiffre_affaires"] = None if part is None else asdict(part)
    va = diagnostic.part_valeur_ajoutee
    contenu["part_valeur_ajoutee"] = None if va is None else asdict(va)
    criteres = verdict(diagnostic)
    contenu["verdict"] = {
        "phrase": phrase_verdict(criteres),
        "criteres": [asdict(c) for c in criteres],
    }
    return convertir(contenu)


_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip


def heures_carbone(c: Carbone) -> str | None:
    if not c.profil_horaire:
        return None
    bas = min(c.profil_horaire, key=lambda heure: heure[1])
    haut = max(c.profil_horaire, key=lambda heure: heure[1])
    return (
        f"le moins carboné vers {bas[0]} h ({_nombre(bas[1])} gCO₂e/kWh), le plus vers"
        f" {haut[0]} h ({_nombre(haut[1])}), heure locale ; profil EDF SEI {c.annee_profil}"
    )


def _verdict_carbone(c: Carbone) -> str:
    verdict = "décarbonée" if c.est_decarbone else "non décarbonée"
    phrase = (
        f"Électricité du réseau {verdict} ({c.systeme_electrique}) :"
        f" {_nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh en cycle de vie (ADEME,"
        f" {c.validite_source}), {'sous le' if c.est_decarbone else 'au-dessus du'} seuil de"
        f" {_nombre_court(c.seuil_decarbone_g_co2e_kwh)} gCO₂e/kWh de la taxonomie européenne"
    )
    if c.intensite_directe_rte_g_co2_kwh is not None and c.rte_jour_fin is not None:
        phrase += (
            f" ; émissions directes observées par RTE sur 12 mois, jusqu'au"
            f" {c.rte_jour_fin:%d/%m/%Y} : {_nombre(c.intensite_directe_rte_g_co2_kwh)} gCO₂/kWh"
        )
    return phrase


# Le décalage vers la nuit peut augmenter les émissions en été (solaire de midi)
def _co2_evite(kg: float) -> str:
    if kg >= 0:
        return f"{_nombre(kg, 1)} kgCO₂ évités"
    return (
        f"mais {_nombre(-kg, 1)} kgCO₂ émis en plus : les heures creuses de nuit sont"
        " plus carbonées que la mi-journée solaire"
    )


_STATUTS = {"excedentaire": "excédentaire", "deficitaire": "déficitaire"}


def _controle_sur_le_passe(p: PrixEntreprise) -> str:
    if not p.nb_periodes_controlees or p.premiere_origine_controlee is None:
        return "Historique trop court pour contrôler la méthode sur le passé."
    total = p.nb_periodes_controlees
    dans = p.nb_dans_eventail or 0
    return (
        f"Contrôle sur le passé : appliquée à chaque semestre de"
        f" {_semestre(p.premiere_origine_controlee)} à"
        f" {_semestre(p.derniere_origine_controlee or p.premiere_origine_controlee)},"
        f" la méthode a encadré le prix moyen réel des 3 années suivantes {dans} fois sur"
        f" {total} ({_nombre(dans / total * 100)} %, contre 80 % pour une méthode bien"
        f" calibrée) ; il a dépassé le scénario haut {p.nb_au_dessus or 0} fois et est passé"
        f" sous le scénario bas {p.nb_en_dessous or 0} fois. Ces périodes de 3 ans se"
        " chevauchent : ce ne sont pas des essais indépendants. Leurs écarts font désormais"
        " partie des erreurs qui calibrent la fourchette affichée."
        + (
            ""
            if p.est_fourchette_validee
            else f" Avec {total} périodes seulement, souvent prises dans une même crise, la"
            " méthode n'est pas validée : la fourchette est un ordre de grandeur."
        )
    )


# Zone P1 ou P2 la plus proche, et une zone P3 seulement si elle l'est davantage
def _zones_rte(
    connection: duckdb.DuckDBPyConnection, territoire: Territoire
) -> tuple[ZoneRte | None, ZoneRte | None]:
    zones = [
        ZoneRte(*ligne)
        for ligne in connection.execute(
            _ZONES_SQL, {"latitude": territoire.latitude, "longitude": territoire.longitude}
        ).fetchall()
    ]
    prioritaire = next((z for z in zones if z.priorite in ("P1", "P2")), None)
    p3 = next((z for z in zones if z.priorite == "P3"), None)
    if prioritaire is not None and p3 is not None and p3.distance_km >= prioritaire.distance_km:
        p3 = None
    return prioritaire, p3


def _calendrier_zone(z: ZoneRte) -> str:
    if z.priorite == "P1":
        return (
            f"capacité supplémentaire annoncée à partir de {z.annee_capacite}, travaux lancés"
            " dès l'obtention des autorisations"
        )
    return (
        f"capacité supplémentaire possible à partir de {z.annee_capacite}, si les projets"
        " industriels de la zone se concrétisent"
    )


# « à 9 km » ; en deçà d'un kilomètre, la précision du point de référence ne suit pas
def _distance(km: float) -> str:
    return "à moins d'un km" if km < 1 else f"à {_nombre(km)} km"


def _contrainte(c: ContrainteOuvrage) -> str:
    details = [c.occurrence[:1].lower() + c.occurrence[1:], _duree(c.duree)]
    details.append(f"pérennité {c.perennite}")
    if c.est_en_journee:
        details.append("en journée")
    return f"{c.nom_de_l_ouvrage} ({', '.join(d for d in details if d)})"


# « ]4h-6h] » -> « épisodes de 4 à 6 h » ; « ]0h-2h] » -> « épisodes de moins de 2 h »
def _duree(duree: str) -> str:
    bornes = re.fullmatch(r"\](\d+)h-(\d+)h\]", duree.strip())
    if bornes is None:
        return f"durée {duree}" if duree else ""
    debut, fin = bornes.groups()
    return f"épisodes de moins de {fin} h" if debut == "0" else f"épisodes de {debut} à {fin} h"


# « 2026-S1 » -> « S1 2026 » ; `precedent` donne le semestre d'avant
def _semestre(code: str, *, precedent: bool = False) -> str:
    annee, numero = int(code[:4]), int(code[-1])
    if precedent:
        annee, numero = (annee, 1) if numero == 2 else (annee - 1, 2)
    return f"S{numero} {annee}"


# Trois chiffres significatifs : 67 375 -> 67 400, 5 496 000 -> 5 500 000
def _euros_arrondis(valeur: float) -> str:
    if valeur <= 0:
        return f"{_nombre(valeur)} €"
    chiffres = len(str(int(valeur)))
    return f"{_nombre(round(valeur, min(0, 3 - chiffres)))} €"


def _nombre_court(valeur: float) -> str:
    return _nombre(valeur, 3).rstrip("0").rstrip(",")
