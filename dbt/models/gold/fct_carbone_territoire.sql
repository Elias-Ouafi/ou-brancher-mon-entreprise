{%- set seuil = var('seuil_decarbone_g_co2e_kwh', 100) %}

with dernier_jour as (
    select max(jour) as jour
    from {{ ref('fct_mix_journalier') }}
    where est_complet
),

rte_12_mois as (
    select
        min(mix.jour) as rte_jour_debut,
        max(mix.jour) as rte_jour_fin,
        count(*) as rte_nb_jours,
        sum(mix.taux_co2_moyen_g_kwh * mix.production_gwh)
            / nullif(sum(mix.production_gwh), 0) as intensite_directe_rte_g_co2_kwh
    from {{ ref('fct_mix_journalier') }} as mix
    cross join dernier_jour
    where mix.est_complet
        and mix.taux_co2_moyen_g_kwh is not null
        and mix.jour > dernier_jour.jour - interval 1 year
        and mix.jour <= dernier_jour.jour
),

regions as (
    select
        region.code_insee_region,
        region.region,
        region.est_zni,
        case when region.est_zni then region.code_insee_region else 'continent' end
            as zone_carbone
    from {{ ref('dim_region') }} as region
)

select
    regions.code_insee_region,
    regions.region,
    regions.est_zni,
    regions.zone_carbone,
    facteurs.territoire as systeme_electrique,
    facteurs.intensite_acv_g_co2e_kwh,
    facteurs.validite_source,
    facteurs.id_base_carbone,
    {{ seuil }}::double as seuil_decarbone_g_co2e_kwh,
    facteurs.intensite_acv_g_co2e_kwh < {{ seuil }} as est_decarbone,
    case when not regions.est_zni then rte.intensite_directe_rte_g_co2_kwh end
        as intensite_directe_rte_g_co2_kwh,
    case when not regions.est_zni then rte.rte_jour_debut end as rte_jour_debut,
    case when not regions.est_zni then rte.rte_jour_fin end as rte_jour_fin,
    case when not regions.est_zni then rte.rte_nb_jours end as rte_nb_jours
from regions
inner join {{ ref('facteurs_carbone_territoire') }} as facteurs
    on facteurs.zone_carbone = regions.zone_carbone
cross join rte_12_mois as rte
