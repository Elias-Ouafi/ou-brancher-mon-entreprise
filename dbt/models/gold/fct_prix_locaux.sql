-- Prix de vente au m² des locaux d'activité, par commune puis par département en repli
with derniere as (
    select max(date_mutation) as date_mutation from {{ ref('ventes_locaux') }}
),

ventes as (
    select ventes_locaux.*
    from {{ ref('ventes_locaux') }} as ventes_locaux
    cross join derniere
    -- Fenêtre glissante sur les cinq dernières années de ventes
    where ventes_locaux.date_mutation > derniere.date_mutation - interval 5 year
),

communes as (
    select
        'commune' as niveau,
        code_insee_commune as code_zone,
        min(code_departement) as code_departement,
        count(*)::integer as nb_ventes,
        quantile_cont(prix_m2, 0.5) as prix_m2_median,
        quantile_cont(prix_m2, 0.25) as prix_m2_q1,
        quantile_cont(prix_m2, 0.75) as prix_m2_q3,
        max(date_mutation) as derniere_vente
    from ventes
    group by code_insee_commune
),

departements as (
    select
        'departement' as niveau,
        code_departement as code_zone,
        code_departement,
        count(*)::integer as nb_ventes,
        quantile_cont(prix_m2, 0.5) as prix_m2_median,
        quantile_cont(prix_m2, 0.25) as prix_m2_q1,
        quantile_cont(prix_m2, 0.75) as prix_m2_q3,
        max(date_mutation) as derniere_vente
    from ventes
    group by code_departement
)

select
    *,
    nb_ventes >= {{ var('locaux_min_ventes', 5) }} as fiable
from (
    select * from communes
    union all
    select * from departements
)
