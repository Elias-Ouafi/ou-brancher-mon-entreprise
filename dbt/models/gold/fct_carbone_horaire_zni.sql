-- Profil de la part thermique (fioul, gaz, charbon) calé sur le facteur ADEME annuel de l'île :
-- la moyenne pondérée des 24 heures redonne exactement ce facteur.
with iles as (
    select * from (values
        ('Corse', '94', 'Europe/Paris'),
        ('Guadeloupe', '01', 'America/Guadeloupe'),
        ('Martinique', '02', 'America/Martinique'),
        ('Guyane', '03', 'America/Cayenne'),
        ('Réunion', '04', 'Indian/Reunion')
    ) as t (territoire, code_insee_region, fuseau)
),

production as (
    select
        iles.code_insee_region,
        timezone(iles.fuseau, source.date_heure) as heure_locale,
        source.production_totale_mw,
        coalesce(source.thermique_mw, 0) + coalesce(source.bagasse_charbon_mw, 0) as fossile_mw
    from {{ source('bronze', 'edf_sei_production') }} as source
    inner join iles on iles.territoire = source.territoire
    where source.production_totale_mw > 0
),

-- Une année n'est retenue que presque entière, pour ne pas tordre le profil saisonnier
annee_complete as (
    select code_insee_region, max(annee) as annee
    from (
        select code_insee_region, year(heure_locale) as annee, count(*) as nb_heures
        from production
        group by all
    )
    where nb_heures >= 8700
    group by all
),

retenue as (
    select production.*, annee_complete.annee
    from production
    inner join annee_complete
        on annee_complete.code_insee_region = production.code_insee_region
        and year(production.heure_locale) = annee_complete.annee
),

par_heure as (
    select
        code_insee_region,
        annee,
        hour(heure_locale) as heure_locale,
        sum(fossile_mw) / sum(production_totale_mw) as part_fossile,
        sum(production_totale_mw) as production_mwh
    from retenue
    group by all
),

par_an as (
    select code_insee_region, sum(fossile_mw) / sum(production_totale_mw) as part_fossile_an
    from retenue
    group by all
)

select
    par_heure.code_insee_region,
    carbone.systeme_electrique,
    par_heure.annee,
    par_heure.heure_locale,
    par_heure.part_fossile,
    carbone.intensite_acv_g_co2e_kwh * par_heure.part_fossile / par_an.part_fossile_an
        as intensite_estimee_g_co2e_kwh,
    par_heure.production_mwh
from par_heure
inner join par_an on par_an.code_insee_region = par_heure.code_insee_region
inner join {{ ref('fct_carbone_territoire') }} as carbone
    on carbone.code_insee_region = par_heure.code_insee_region
