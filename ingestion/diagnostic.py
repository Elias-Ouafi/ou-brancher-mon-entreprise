from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from ingestion.config import Settings, get_settings
from ingestion.courbe import BilanCourbe, Courbe, bilan_courbe
from ingestion.geocode import Territoire, adresse_normalisee, code_commune_parent
from ingestion.georisques import (
    Exposition,
    GeorisquesError,
    Risque,
    RisquesSite,
    lire_risques,
    risques_gaspar,
)
from ingestion.turpe import PROFIL_COURBE, Acheminement, lire_acheminement

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


class DiagnosticIndisponibleError(RuntimeError):
    pass


# Warehouse absent ou couche gold incomplète : seul un `refresh` y remédie
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
}
# Au-delà, une source n'a pas été rechargée depuis plus d'un cycle mensuel de `refresh`
AGE_MAX_JOURS = 45
# Une source rechargée à part (un `refresh` partiel) n'est signalée qu'au-delà
ECART_TOLERE_JOURS = 7


# Dernier chargement de chaque source présente dans le bronze, en heure de Paris
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


# Date des données sous chaque réponse, avec une alerte si une source a pris du retard
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


# Entreprises raccordées à Enedis dans la commune : le tissu économique déjà branché
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


# Consommation déjà relevée à l'adresse demandée : un raccordement existe sans doute
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


# Accise au tarif normal (PME et haute puissance) en vigueur, lue dans le seed daté
# `accise_electricite` : base légale du calcul d'électro-intensité
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
class PartChiffreAffaires:
    chiffre_affaires_eur: float
    facture_eur: float
    part: float
    # Bornes des scénarios à 3 ans ; `None` si l'historique de la tranche est trop court
    part_bas: float | None
    part_haut: float | None


# Plan de prévention en vigueur dans la commune : son règlement s'impose aux constructions
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

    @property
    def commune_a_des_sites(self) -> bool:
        return bool(self.nb_sites_commune)

    # Poids de l'électricité dans la valeur ajoutée : ouvre ou non les taux réduits d'accise
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


# Rassemble tout ce que le warehouse sait d'une adresse : la base de chaque réponse
def lire_diagnostic(
    territoire: Territoire,
    *,
    puissance_mw: float | None = None,
    conso_mwh: float | None = None,
    ca_eur: float | None = None,
    va_eur: float | None = None,
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
            acheminement = (
                lire_acheminement(
                    connection,
                    seuil.domaine_turpe,
                    puissance_kva=seuil.puissance_mw * 1000,
                    conso_mwh=conso_mwh,
                    points=None if courbe is None else courbe.points,
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
        **ligne,
    )


# Plans de la commune (et de son arrondissement), une fois chacun
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


# Commune, département, région et zone : situe l'adresse avant tout chiffre
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


# Les deux réponses attendues d'abord : prix moyen et verdict carbone
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
    if d.courbe is not None:
        section.elements.append(("texte", f"Votre courbe de charge : {ligne_courbe(d.courbe)}"))
    section.elements.append(("texte", f"À savoir : {A_SAVOIR}"))
    return section


_GRANDS_SECTEURS = {
    "INDUSTRIE": "industrie",
    "TERTIAIRE": "tertiaire",
    "AGRICULTURE": "agriculture",
    "INCONNU": "secteur inconnu",
}


# Ce qu'Enedis relève déjà à l'adresse : un raccordement existe, sa puissance reste inconnue
def ligne_entreprises_adresse(e: EntreprisesAdresse) -> str:
    secteur = _GRANDS_SECTEURS.get(e.grand_secteur, e.grand_secteur.lower())
    activite = f", {e.libelle_naf2}" if e.libelle_naf2 else ""
    return (
        f"{e.nb_sites} site(s) d'entreprise consommant {_nombre(e.consommation_mwh, 1)} MWh"
        f" en {e.annee} ({secteur}{activite} ; Enedis)"
    )


# Tissu économique déjà branché sur la distribution dans la commune, et le poids de son industrie
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


# La consommation réelle du site : volume, pointe, heures creuses, coût et carbone
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


# Coût du réseau de distribution selon la puissance souscrite : ce que change le raccordement
def ligne_acheminement(a: Acheminement) -> str:
    source = "votre courbe de charge" if a.profil == PROFIL_COURBE else "puissance constante"
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


# Électro-intensité au sens fiscal : décide d'un taux d'accise réduit, donc de la facture
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


# Réponse courte au terminal, terminée par le verdict « brancher ici ? »
def formater_essentiel(diagnostic: Diagnostic) -> str:
    lignes = [diagnostic.territoire.adresse, *entete(diagnostic), ""]
    lignes += [contenu for _, contenu in essentiel(diagnostic).elements]
    lignes.append(f"Brancher ici ? {phrase_verdict(verdict(diagnostic))}")
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


# Les cinq questions d'un chef d'entreprise avant de signer un bail ou un terrain
def verdict(diagnostic: Diagnostic) -> list[Critere]:
    return [
        _critere_raccordement(diagnostic),
        _critere_risques(diagnostic),
        _critere_carbone(diagnostic),
        _critere_facture(diagnostic),
        _critere_foncier(diagnostic),
    ]


# Qui raccorde (Enedis ou RTE) et, pour RTE, si une zone prioritaire promet de la capacité
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


# Ce que l'on sait des risques sans plan de prévention : à l'adresse, sinon dans la commune
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


# Électricité du réseau sous le seuil de la taxonomie européenne, ou non
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


# Ordre de grandeur de la facture, identique partout en France (péréquation)
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


# Terrain déjà viabilisé et pré-autorisé le plus proche (sites clés en main France 2030)
def _critere_foncier(d: Diagnostic) -> Critere:
    site = d.site_cle_en_main
    if site is None:
        return Critere("Foncier", "info", "aucun site clé en main", "Dans ce territoire.")
    return Critere(
        "Foncier",
        "info",
        f"site clé en main {_distance(site.distance_km)}",
        f"{site.commune}, annoncé prêt en {site.maturite_site}.",
    )


# Phrase de synthèse : ce qu'il reste à vérifier avant de s'engager
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


# Réponse courte pour un autre programme (`--format json`)
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
        "plans_prevention": None
        if d.plans_prevention is None
        else [asdict(p) for p in d.plans_prevention],
        "verdict": {
            "phrase": phrase_verdict(criteres := verdict(d)),
            "criteres": [asdict(c) for c in criteres],
        },
        "limites": {
            code: d.limites[code]
            for code in (
                "prix_entreprise",
                "carbone_territoire",
                "part_chiffre_affaires",
                "acheminement_turpe",
                "part_valeur_ajoutee",
                "entreprises_enedis",
            )
            if code in d.limites
            and (code != "acheminement_turpe" or d.acheminement is not None)
            and (code != "part_chiffre_affaires" or d.part_chiffre_affaires is not None)
            and (code != "part_valeur_ajoutee" or d.part_valeur_ajoutee is not None)
            and (code != "entreprises_enedis" or d.entreprises_adresse is not None)
        },
    }


# Diagnostic complet, section par section, chaque chiffre suivi de sa limite
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

    s = bloc("6. Risques naturels et technologiques à l'adresse (Géorisques)")
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
    return resultat


def _liste_risques(risques: list[Risque]) -> str:
    return ", ".join(f"{r.libelle} ({r.niveau})" if r.niveau else r.libelle for r in risques)


_GASPAR_SQL = """
select code_insee_commune, num_risque, libelle_risque, millesime
from gold.fct_risques_commune
where code_insee_commune in (select unnest(?))
order by code_insee_commune, num_risque
"""


# Export GASPAR chargé par `refresh` ; `None` s'il n'a jamais été chargé
def _gaspar_local(codes_commune: set[str], settings: Settings | None) -> RisquesSite | None:
    path = (settings or get_settings()).duckdb_path
    if not Path(path).exists():
        return None
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            lignes = connection.execute(_GASPAR_SQL, [sorted(codes_commune)]).fetchall()
    except duckdb.CatalogException:
        return None
    return risques_gaspar(lignes)


# Risques de la commune sans appeler Géorisques : assez pour le verdict rapide de `essentiel`
def avec_risques_locaux(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    if diagnostic.risques is not None:
        return diagnostic
    codes = {diagnostic.territoire.code_insee_commune, diagnostic.code_insee_commune}
    return replace(diagnostic, risques=_gaspar_local(codes, settings))


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
            repli=_gaspar_local(codes_commune, settings),
        )
    except GeorisquesError as error:
        logger.warning("risques non evalues : %s", error)
        return replace(diagnostic, risques_erreur=str(error))
    return replace(diagnostic, risques=risques)


# Diagnostic complet au terminal
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


# Diagnostic complet pour un autre programme, dates en ISO
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
    return convertir(contenu)


_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip


# Hors continent, l'heure de consommation change le carbone : à quelle heure décaler l'activité
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
