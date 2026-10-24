-- Principaux pôles de production : filière d'une commune, au-delà d'un seuil propre à chaque réseau
with sites as (
    select
        -- Arrondissements de Paris, Lyon et Marseille rattachés à leur commune
        case
            when code_commune between '75101' and '75120' then '75056'
            when code_commune between '69381' and '69389' then '69123'
            when code_commune between '13201' and '13216' then '13055'
            else code_commune
        end as code_insee_commune,
        filiere,
        puissance_mw,
        energie_gwh,
        nb_installations
    from {{ source('bronze', 'sites_production') }}
    where code_commune <> ''
        -- Batteries et volants d'inertie stockent, ils ne produisent pas
        and filiere <> 'Stockage non hydraulique'
),

par_commune as (
    select
        code_insee_commune,
        filiere,
        sum(puissance_mw) as puissance_mw,
        sum(energie_gwh) as energie_gwh,
        cast(sum(nb_installations) as integer) as nb_installations
    from sites
    group by code_insee_commune, filiere
)

select
    p.code_insee_commune,
    c.nom as commune,
    territoire.code_departement,
    p.filiere,
    round(p.puissance_mw, 1) as puissance_mw,
    round(p.energie_gwh, 1) as energie_gwh,
    p.nb_installations,
    c.latitude,
    c.longitude
from par_commune as p
inner join {{ source('bronze', 'communes_centres') }} as c
    on c.code_commune = p.code_insee_commune
-- Seuls les départements des cartes : les collectivités d'outre-mer n'y figurent pas
inner join {{ ref('fct_diagnostic_territoire') }} as territoire
    on territoire.code_departement = {{ code_departement('p.code_insee_commune') }}
where p.puissance_mw >= case when territoire.est_zni then 20 else 200 end
