"""Requêtes SQL du diagnostic sur le warehouse (gold et étoile) et rayons de recherche."""

from __future__ import annotations

# Contexte industriel : un département très consommateur sature plus vite ses postes
DIAGNOSTIC_SQL = """
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

# Corse et outre-mer : le gestionnaire insulaire tient tous les domaines de tension, RTE aucun
SEUIL_SQL = """
select
    seuil.domaine_tension,
    coalesce(zni.gestionnaire, seuil.gestionnaire) as gestionnaire,
    case
        when zni.gestionnaire is null then seuil.commentaire
        else 'Réseau insulaire géré par ' || zni.gestionnaire || ', sans RTE : domaine de'
            || ' tension, capacité et délai fixés par son étude'
    end as commentaire,
    seuil.domaine_turpe,
    zni.gestionnaire is not null as est_zni
from gold.seuils_raccordement as seuil
left join etoile.dim_departement_region as departement
    on departement.code_departement = $departement
left join gold.gestionnaires_zni as zni
    on zni.code_insee_region = departement.code_insee_region
where $puissance >= seuil.puissance_min_mw
  and (seuil.puissance_max_mw is null or $puissance < seuil.puissance_max_mw)
"""

# Journée type de consommation : l'acheminement (TURPE) dépend des heures de pointe et creuses
PROFIL_CHARGE_SQL = """
select type_jour, heure_debut, puissance_kw
from gold.profils_charge_reels
where profil = $profil
"""

# Coût et carbone de chaque heure de la journée type, pour dire quand consommer
COUT_SQL = """
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

# Ouvrages de transport en contrainte prévue : un projet de forte puissance s'y heurte
CONTRAINTES_SQL = """
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

# Zones prioritaires RTE : la capacité n'étant pas publiée, leur année est le seul repère
ZONES_SQL = f"""
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

# Entreprises déjà raccordées : preuve qu'un réseau dessert le secteur, sans dire sa capacité
ENTREPRISES_COMMUNE_SQL = """
select
    annee,
    nb_sites,
    consommation_mwh / 1000 as consommation_gwh,
    consommation_industrie_mwh / 1000 as consommation_industrie_gwh,
    nb_sites_industrie,
    code_naf2_principal,
    naf.libelle_naf2 as libelle_naf2_principal,
    consommation_naf2_principal_mwh / 1000 as consommation_naf2_principal_gwh,
    rang_industrie_departement,
    nb_communes_departement
from etoile.fait_conso_entreprises as conso
left join etoile.dim_naf as naf on naf.code_naf2 = conso.code_naf2_principal
where conso.code_insee_commune = $commune
"""

ENTREPRISES_ADRESSE_SQL = """
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

# Sites clés en main France 2030 : foncier déjà viabilisé et raccordé
SITE_SQL = f"""
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
# Ports, aéroports et chantiers rail-route : desserte pour la logistique et l'industrie
POLES_PROCHES_SQL = f"""
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

# Facteur d'émission du système électrique, comparé au seuil de 100 g de la taxonomie européenne
CARBONE_SQL = """
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

# Sur une île, l'heure de consommation change le carbone du réseau
CARBONE_HORAIRE_SQL = """
select annee, heure_locale, intensite_estimee_g_co2e_kwh
from gold.fct_carbone_horaire_zni
where code_insee_region = $region
order by heure_locale
"""

# Accise en vigueur : une part de la facture que l'activité peut réduire
ACCISE_SQL = """
select tarif_eur_mwh, date_debut
from gold.accise_electricite
where categorie = 'pme_haute_puissance' and date_fin is null
order by date_debut desc
limit 1
"""

ACCISE_REDUITS_SQL = """
select categorie, tarif_eur_mwh
from gold.accise_electricite
where categorie != 'pme_haute_puissance' and date_fin is null
"""

# Prix moyen par tranche de consommation : identique partout, il dépend du volume et non du site
PRIX_SQL = """
select *
from gold.fct_prix_entreprise_tranche
where $conso >= conso_min_mwh
  and (conso_max_mwh is null or $conso < conso_max_mwh)
"""

PRIX_DEFAUT_SQL = """
select *
from gold.fct_prix_entreprise_tranche
where code_tranche = $code
"""


# Plans en vigueur, approuvés d'abord, puis du plus récent au plus ancien
# Plans de prévention : seuls ces plans rendent un risque « à vérifier »
PLANS_SQL = """
select
    code_procedure, type_plan, libelle_plan, risques, etat,
    jour_prescription as date_prescription, jour_approbation as date_approbation
from etoile.fait_plan_prevention
where code_insee_commune in (select unnest($codes))
order by etat, coalesce(jour_approbation, jour_prescription) desc nulls last, libelle_plan
"""


# Qualité de fourniture du département, publiée par Enedis sur son seul territoire
# Qualité de fourniture : une coupure longue coûte cher à un data center ou à une industrie
CONTINUITE_SQL = """
select
    year(jour_reference) as derniere_annee,
    part_mal_alimentes_pct,
    moyenne_5_ans_pct,
    annees_au_dela_seuil_5_ans,
    mediane_nationale_pct,
    depasse_seuil
from etoile.fait_continuite_departement
where code_departement = $departement
"""


# Prix de la commune si elle compte assez de ventes, sinon du département
# Prix de vente des locaux d'activité, repère du coût foncier (aucun loyer public)
LOCAUX_SQL = """
select niveau, nb_ventes, prix_m2_median, prix_m2_q1, prix_m2_q3, derniere_vente
from gold.fct_prix_locaux
where (niveau = 'commune' and code_zone = $commune and fiable)
    or (niveau = 'departement' and code_zone = $departement)
order by niveau = 'commune' desc
limit 1
"""


# Zone de répartition des eaux : prélèvement soumis à autorisation, enjeu du refroidissement
ZRE_SQL = """
select nom_zone, ressource, geometrie
from gold.fct_zones_repartition_eaux
where $longitude between lon_min and lon_max and $latitude between lat_min and lat_max
order by gid
"""

# Électriciens, frigoristes et techniciens de maintenance : main-d'œuvre d'un site exigeant
RECRUTEMENTS_SQL = """
select
    code_departement, strftime(jour_reference, '%Y') as annee, projets, projets_difficiles,
    part_difficiles, nb_lignes_secretes, nb_lignes
from etoile.fait_recrutements_techniques
where code_departement = $departement
"""

# Communes dans l'emprise de l'isochrone, placées par la dimension commune ; le test point dans
# polygone se fait en Python
# Diplômés du bassin d'emploi, pour recruter autour du site
DIPLOMES_SQL = """
select
    communes.longitude,
    communes.latitude,
    diplomes.nb_superieur,
    diplomes.nb_cap_bep_bac,
    diplomes.nb_15_ans_plus,
    strftime(diplomes.jour_reference, '%Y') as millesime
from etoile.fait_diplomes_commune as diplomes
inner join etoile.dim_commune as communes using (code_insee_commune)
where communes.longitude between $lon_min and $lon_max
    and communes.latitude between $lat_min and $lat_max
"""

# Friches d'au moins 1 ha à moins de 10 km à vol d'oiseau, les plus proches d'abord
RAYON_FRICHES_KM = 10
NB_FRICHES_AFFICHEES = 3

# Friches d'au moins 1 ha : foncier déjà artificialisé, sans conflit avec le zéro artificialisation
FRICHES_SQL = f"""
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
        {_DISTANCE_KM_SQL} as distance_km,
        latitude,
        longitude
    from gold.fct_friches
    where abs(latitude - $latitude) < {RAYON_FRICHES_KM} / 111.0
      and abs(longitude - $longitude) < {RAYON_FRICHES_KM} / (111.0 * cos(radians($latitude)))
)
where distance_km <= {RAYON_FRICHES_KM}
order by distance_km
"""


# Réseaux de chaleur à moins de 10 km : distance au point le plus proche de chaque tracé
RAYON_RESEAUX_CHALEUR_KM = 10
NB_RESEAUX_CHALEUR_AFFICHES = 3

# Réseau de chaleur proche : débouché de la chaleur fatale d'un data center
RESEAUX_CHALEUR_SQL = f"""
select nom_reseau, communes, gestionnaire, a_trace, classe, taux_enr_pct, min(distance_km)
from (
    select *, {_DISTANCE_KM_SQL} as distance_km
    from gold.fct_reseaux_chaleur
    where abs(latitude - $latitude) < {RAYON_RESEAUX_CHALEUR_KM} / 111.0
      and abs(longitude - $longitude)
        < {RAYON_RESEAUX_CHALEUR_KM} / (111.0 * cos(radians($latitude)))
)
where distance_km <= {RAYON_RESEAUX_CHALEUR_KM}
group by all
order by min(distance_km), nom_reseau
limit {NB_RESEAUX_CHALEUR_AFFICHES}
"""
