-- Dimension territoire à la maille commune, département et région aplatis pour éviter les jointures en cascade.
-- Les communes des faits absentes du référentiel géographique (fusions, arrondissements) y figurent quand
-- même, nom nul : une clé étrangère ne doit jamais rester sans ligne.
with referentiel as (
    select
        code_commune as code_insee_commune,
        nom as commune,
        code_departement
    from {{ source('bronze', 'communes_centres') }}
),

communes_des_faits as (
    select code_insee_commune from {{ ref('fct_conso_entreprises_commune') }}
    union
    select code_zone from {{ ref('fct_prix_locaux') }} where niveau = 'commune'
    union
    select code_insee_commune from {{ ref('fct_risques_commune') }}
),

toutes as (
    select
        code_insee_commune,
        referentiel.commune,
        coalesce(
            referentiel.code_departement,
            {{ code_departement('code_insee_commune') }}
        ) as code_departement
    from communes_des_faits
    full join referentiel using (code_insee_commune)
)

select
    toutes.code_insee_commune,
    toutes.commune,
    toutes.code_departement,
    departements.departement,
    departements.code_insee_region,
    regions.region,
    regions.est_zni
from toutes
left join {{ ref('dim_departement') }} as departements
    on departements.code_departement = toutes.code_departement
left join {{ ref('dim_region') }} as regions
    on regions.code_insee_region = departements.code_insee_region
