-- Consommation électrique annuelle des sites raccordés au transport, par IRIS
with source as (
    select
        cast(annee as integer) as annee,
        lpad(code_iris, 9, '0') as code_iris,
        code_insee_commune as code_insee_commune_publie,
        commune,
        consommation_electricite_rte,
        pdl_electricite_rte
    from {{ source('bronze', 'consommation_annuelle_par_iris') }}
    qualify row_number() over (
        partition by annee, lpad(code_iris, 9, '0')
        order by length(code_iris) desc
    ) = 1
),

communes as (
    select
        *,
        coalesce(
            code_insee_commune_publie,
            case
                when left(code_iris, 3) = '751' then '75056'
                when left(code_iris, 3) = '693' then '69123'
                when left(code_iris, 3) = '132' then '13055'
                else left(code_iris, 5)
            end
        ) as code_insee_commune
    from source
)

select
    communes.annee,
    communes.code_iris,
    communes.code_insee_commune,
    communes.commune,
    communes.code_insee_commune_publie is null as est_commune_reconstituee,
    {{ code_departement('communes.code_insee_commune') }} as code_departement,
    departement.code_insee_region,
    communes.consommation_electricite_rte as consommation_electricite_mwh,
    communes.pdl_electricite_rte as nb_sites_electricite,
    communes.consommation_electricite_rte is null as est_secret
from communes
left join {{ ref('dim_departement') }} as departement
    on departement.code_departement = {{ code_departement('communes.code_insee_commune') }}
