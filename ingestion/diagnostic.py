from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from ingestion.config import Settings, get_settings
from ingestion.geocode import Territoire
from ingestion.georisques import Exposition, GeorisquesError, Risque, RisquesSite, lire_risques

logger = logging.getLogger(__name__)

# Arrondissements municipaux (3 premiers caractères du code) -> code de la commune
ARRONDISSEMENTS: dict[str, str] = {"751": "75056", "693": "69123", "132": "13055"}

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
select domaine_tension, gestionnaire, commentaire
from gold.seuils_raccordement
where $puissance >= puissance_min_mw
  and (puissance_max_mw is null or $puissance < puissance_max_mw)
"""


PROFIL_DEFAUT = "bureau_36kva"

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


class DiagnosticIndisponibleError(RuntimeError):
    pass


def code_commune_parent(code_insee: str) -> str:
    if len(code_insee) == 5 and code_insee[:3] in ARRONDISSEMENTS:
        return ARRONDISSEMENTS[code_insee[:3]]
    return code_insee


# Code département d'un code commune : 3 caractères outre-mer, 2 ailleurs
def code_departement(code_commune: str) -> str:
    return code_commune[:3] if code_commune.startswith("97") else code_commune[:2]


@dataclass(frozen=True, slots=True)
class Seuil:
    puissance_mw: float
    domaine_tension: str
    gestionnaire: str
    commentaire: str


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

    @property
    def commune_a_des_sites(self) -> bool:
        return bool(self.nb_sites_commune)


def lire_diagnostic(
    territoire: Territoire,
    *,
    puissance_mw: float | None = None,
    conso_mwh: float | None = None,
    settings: Settings | None = None,
    database: Path | None = None,
) -> Diagnostic:
    path = database or (settings or get_settings()).duckdb_path
    if not Path(path).exists():
        raise DiagnosticIndisponibleError(
            f"warehouse introuvable : {path} (charger les donnees puis lancer dbt build)"
        )

    commune = code_commune_parent(territoire.code_insee_commune)
    departement = code_departement(commune)
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            ligne = _une_ligne(
                connection, _DIAGNOSTIC_SQL, {"commune": commune, "departement": departement}
            )
            seuil = _seuil(connection, puissance_mw) if puissance_mw is not None else None
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
            limites = dict(
                connection.execute(
                    "select indicateur, limite from gold.limites_methodologiques"
                ).fetchall()
            )
    except duckdb.CatalogException as error:
        raise DiagnosticIndisponibleError(
            f"couche gold incomplete, lancer dbt build ({error})"
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
        prix=prix,
        contraintes=contraintes,
        zone_rte=zone_rte,
        zone_rte_p3=zone_rte_p3,
        site_cle_en_main=None if site is None else SiteCleEnMain(**site),
        cout=None if cout_ligne is None else CoutProfil(**cout_ligne),
        carbone=None if carbone is None else Carbone(**carbone),
        limites=limites,
        **ligne,
    )


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
    section.elements.append(("texte", f"À savoir : {A_SAVOIR}"))
    return section


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
        "carbone": None
        if c is None
        else {
            "est_decarbone": c.est_decarbone,
            "intensite_acv_g_co2e_kwh": c.intensite_acv_g_co2e_kwh,
            "seuil_decarbone_g_co2e_kwh": c.seuil_decarbone_g_co2e_kwh,
            "systeme_electrique": c.systeme_electrique,
            "source": f"ADEME Base Carbone, {c.validite_source}",
        },
        "a_savoir": A_SAVOIR,
        "limites": {
            code: d.limites[code]
            for code in ("prix_entreprise", "carbone_territoire")
            if code in d.limites
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
            texte(
                s,
                "Prix moyen des 3 prochaines années, d'après l'historique de la tranche et les"
                " erreurs passées de la méthode :"
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

    s = bloc("5. Carbone du réseau et coût horaire d'un profil type")
    if d.carbone is not None:
        texte(s, _verdict_carbone(d.carbone))
        limite(s, "carbone_territoire")
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
    if d.risques_erreur is not None:
        texte(
            s,
            "Géorisques n'a pas répondu, même après plusieurs tentatives : risques non évalués."
            " À consulter sur georisques.gouv.fr avant tout engagement.",
        )
    elif d.risques is None:
        texte(s, "Non consulté (option --sans-risques).")
    elif d.risques.maille == "commune":
        r = d.risques
        texte(
            s,
            "Rapport à l'adresse indisponible (Géorisques n'a pas répondu) : repli sur les"
            " risques recensés dans la commune, sans évaluation à l'adresse.",
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


# Ajoute le rapport Géorisques de l'adresse ; ne fait jamais échouer le diagnostic
def completer_risques(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    t = diagnostic.territoire
    try:
        risques = lire_risques(
            latitude=t.latitude,
            longitude=t.longitude,
            codes_commune={t.code_insee_commune, diagnostic.code_insee_commune},
            settings=settings,
        )
    except GeorisquesError as error:
        logger.warning("risques non evalues : %s", error)
        return replace(diagnostic, risques_erreur=str(error))
    return replace(diagnostic, risques=risques)


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
    return convertir(contenu)


_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip


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
