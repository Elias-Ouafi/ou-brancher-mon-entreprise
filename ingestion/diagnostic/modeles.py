"""Types du diagnostic : une ligne de données par indicateur, assemblées dans `Diagnostic`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ingestion.acces_routier import Acces
from ingestion.courbe import BilanCourbe
from ingestion.eau import SituationEau
from ingestion.geocode import Territoire
from ingestion.georisques import (
    RisquesSite,
    Voisinage,
)
from ingestion.image_site import ImageSite
from ingestion.main_oeuvre import (
    MainOeuvre,
    Recrutements,
)
from ingestion.solaire import (
    Batiment,
    Productible,
)
from ingestion.turpe import Acheminement
from ingestion.zones_activite import ZoneActivite

PROFIL_DEFAUT = "bureau_36kva"


@dataclass(frozen=True, slots=True)
class Activite:
    libelle: str
    # Journée type mesurée par Enedis (`profils_charge_reels`) ; `None` : puissance constante
    profil_charge: str | None
    # Régimes d'accise de l'attestation officielle 2040-TIC-ATT-E ; les taux sont dans le seed
    accise: str


ACTIVITES: dict[str, Activite] = {
    "data-center": Activite(
        "data center",
        None,
        "centres de stockage de données (E16, CIBS L312-64 et L312-70) : tarif réduit sur la"
        " fraction au-delà de 1 GWh par an, sous huit conditions cumulatives attestées au"
        " fournisseur, dont l'électro-sensibilité (2,25 % de la valeur ajoutée : voir --va),"
        " la valorisation de la chaleur fatale et la limitation de l'eau de refroidissement",
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
        " 6,75 %, ou exposée à la concurrence internationale (E30, L312-72, secteurs fixés par"
        " arrêté) : palier et taux selon --va",
    ),
    "logistique": Activite(
        "logistique",
        None,
        "aucun usage de l'attestation officielle ne vise l'entrepôt ; seulement la"
        " manutention portuaire (E21, dès 0,5 %), le transport guidé (E15) ou l'alimentation"
        " à quai (E18) si l'activité en relève",
    ),
}

# Domaines chiffrés par les grilles TURPE HTA-BT et HTB ; le tarif bleu inclut déjà l'acheminement
DOMAINES_TURPE = {"bt_sup36": "BT > 36 kVA", "hta": "HTA", "htb1": "HTB 1"}

# Tranche Eurostat affichée sans `--conso` : moins de 20 MWh par an
TRANCHE_DEFAUT = "IA"
# Code de l'environnement, R214-1, rubrique 1.3.1.0 (version du 29 août 2025) : en zone de
# répartition des eaux, autorisation dès 8 m³/h de capacité de prélèvement, déclaration sinon
SEUIL_AUTORISATION_ZRE_M3H = 8


class DiagnosticIndisponibleError(RuntimeError):
    pass


class DonneesAbsentesError(DiagnosticIndisponibleError):
    pass


# Code département d'un code commune : 3 caractères outre-mer, 2 ailleurs
# Les départements d'outre-mer ont un code à trois chiffres (971…), la métropole à deux
def code_departement(code_commune: str) -> str:
    return code_commune[:3] if code_commune.startswith("97") else code_commune[:2]


@dataclass(frozen=True, slots=True)
class Seuil:
    puissance_mw: float
    domaine_tension: str
    gestionnaire: str
    commentaire: str
    # Grille TURPE du domaine : `bt_inf36`, `bt_sup36`, `hta` ou `htb1`
    domaine_turpe: str
    est_zni: bool = False


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
    reduits: dict[str, float] = field(default_factory=dict)

    # L'accise est indexée chaque 1er février : passé cette date, le barème est peut-être périmé
    def a_reverifier(self, aujourd_hui: date | None = None) -> bool:
        annee = self.date_debut.year + (self.date_debut >= date(self.date_debut.year, 2, 1))
        return (aujourd_hui or date.today()) >= date(annee, 2, 1)


# Paliers d'électro-intensité ouvrant les taux réduits d'accise depuis le 1er janvier 2026
# (CIBS L312-65 et L312-71, loi de finances 2026), du plus haut au plus bas
PALIERS_ELECTRO_INTENSITE = (0.0675, 0.0225, 0.005)
CATEGORIES_PALIER = {
    0.0675: "electro_intensive",
    0.0225: "electro_sensible",
    0.005: "grande_consommatrice",
}
LIBELLES_ACCISE = {
    "electro_intensive": "activité électro-intensive",
    "electro_sensible": "activité électro-sensible",
    "grande_consommatrice": "activité grande consommatrice",
    "centre_stockage_donnees": "centre de stockage de données",
}
# Les centres de stockage de données paient le tarif normal sur le premier GWh (CIBS L312-70)
FRANCHISE_CENTRE_DONNEES_MWH = 1000
SEUIL_ELECTRO_SENSIBLE = 0.0225


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
class AcciseReduite:
    categorie: str
    tarif_eur_mwh: float
    # Consommation au tarif réduit (pour un data center, au-delà du premier GWh)
    mwh_reduits: float
    accise_normale_eur: float
    accise_reduite_eur: float

    @property
    def economie_eur(self) -> float:
        return self.accise_normale_eur - self.accise_reduite_eur


@dataclass(frozen=True, slots=True)
class ZoneRepartition:
    nom: str | None
    # « eaux superficielles », « eaux souterraines » ou les deux : la ressource encadrée
    ressource: str


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
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True, slots=True)
class ReseauChaleur:
    nom: str
    communes: str | None
    gestionnaire: str | None
    # Sans tracé publié, le réseau est placé au centre de sa commune
    a_trace: bool
    classe: bool | None
    taux_enr_pct: float | None
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
    chargements: dict[str, date] = field(default_factory=dict)
    courbe: BilanCourbe | None = None
    # Plans de prévention en vigueur dans la commune ; `None` si l'export GASPAR n'est pas chargé
    plans_prevention: list[PlanPrevention] | None = None
    # Barème d'accise en vigueur, lu seulement avec `--va` ou `--activite`
    accise: Accise | None = None
    # Entreprises raccordées à Enedis (dernière année publiée), dans la commune et à l'adresse
    entreprises_commune: EntreprisesCommune | None = None
    entreprises_adresse: EntreprisesAdresse | None = None
    # Part acheminement (TURPE HTA-BT ou HTB), avec `--puissance` et `--conso` au-delà de 36 kVA
    acheminement: Acheminement | None = None
    # Qualité de fourniture du département ; `None` hors Enedis ou si la source n'est pas chargée
    continuite: Continuite | None = None
    # Prix de vente des locaux d'activité (DVF) et surface cherchée avec `--surface`
    prix_locaux: PrixLocaux | None = None
    surface_m2: float | None = None
    activite: str | None = None
    eau: SituationEau | None = None
    eau_erreur: str | None = None
    voisinage: Voisinage | None = None
    voisinage_erreur: str | None = None
    # Zones d'activité de la BD TOPO à moins de `RAYON_ZONES_ACTIVITE_KM` (nombre et plus proches)
    nb_zones_activite: int | None = None
    zones_activite: list[ZoneActivite] = field(default_factory=list)
    zones_activite_erreur: str | None = None
    # Pôle de fret le plus proche de chaque mode (type, nom, latitude, longitude) ; l'accès routier
    # (`completer_acces_routier`) en calcule les temps de trajet
    poles_proches: list[tuple[str, str, float, float]] = field(default_factory=list)
    acces_routier: list[Acces] | None = None
    acces_routier_erreur: str | None = None
    batiment: Batiment | None = None
    productible: Productible | None = None
    solaire_consulte: bool = False
    solaire_erreur: str | None = None
    # Prise de vue aérienne la plus récente à l'adresse (BD ORTHO), lue à la demande
    image_site: ImageSite | None = None
    image_site_erreur: str | None = None
    # Nombre de friches d'au moins 1 ha à moins de `RAYON_FRICHES_KM`, et les plus proches ;
    # `None` si Cartofriches n'est pas chargé
    nb_friches: int | None = None
    friches: list[Friche] = field(default_factory=list)
    # Réseaux de chaleur les plus proches à moins de `RAYON_RESEAUX_CHALEUR_KM` ; `None` s'ils
    # ne sont pas chargés
    reseaux_chaleur: list[ReseauChaleur] | None = None
    # Zones de répartition des eaux qui contiennent l'adresse ; `None` si elles ne sont pas chargées
    zones_repartition: list[ZoneRepartition] | None = None
    # Métiers techniques du département (BMO) ; diplômés à moins de 45 min, calculés à la
    # demande par `completer_main_oeuvre`
    recrutements: Recrutements | None = None
    main_oeuvre: MainOeuvre | None = None
    main_oeuvre_erreur: str | None = None

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
    def accise_reduite(self) -> AcciseReduite | None:
        p, a, va = self.prix, self.accise, self.part_valeur_ajoutee
        if a is None or p is None or p.conso_mwh is None:
            return None
        if self.activite == "data-center":
            # Une électro-intensité connue sous 2,25 % ferme le tarif (condition 8 de L312-70)
            if va is not None and va.electro_intensite < SEUIL_ELECTRO_SENSIBLE:
                return None
            categorie = "centre_stockage_donnees"
            mwh_reduits = max(p.conso_mwh - FRANCHISE_CENTRE_DONNEES_MWH, 0.0)
        elif self.activite in (None, "industrie") and va is not None and va.palier is not None:
            categorie, mwh_reduits = CATEGORIES_PALIER[va.palier], p.conso_mwh
        else:
            return None
        if categorie not in a.reduits or mwh_reduits <= 0:
            return None
        tarif = a.reduits[categorie]
        normale = p.conso_mwh * a.tarif_eur_mwh
        return AcciseReduite(
            categorie=categorie,
            tarif_eur_mwh=tarif,
            mwh_reduits=mwh_reduits,
            accise_normale_eur=normale,
            accise_reduite_eur=normale - mwh_reduits * (a.tarif_eur_mwh - tarif),
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


@dataclass(frozen=True, slots=True)
class Section:
    titre: str
    elements: list[tuple[str, str]]
