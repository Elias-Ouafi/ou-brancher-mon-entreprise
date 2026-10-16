-- Postes électriques RTE (postes de transformation et points de piquage)
with source as (
    select
        *,
        try_cast(regexp_extract(tension, '^([0-9]+)kV$', 1) as integer) as tension_kv
    from {{ source('bronze', 'postes_electriques_rte') }}
)

select
    source.code_poste,
    source.nom_poste,
    source.fonction,
    source.etat,
    source.etat = 'EN EXPLOITATION' as est_en_exploitation,
    source.tension as tension_publiee,
    source.tension_kv,
    case
        when source.tension_kv between 45 and 150 then 'HTB1'
        when source.tension_kv = 225 then 'HTB2'
        when source.tension_kv = 400 then 'HTB3'
    end as domaine_tension,
    nullif(source.departement, '') as departement_publie,
    departement.code_departement
from source
left join {{ ref('dim_departement') }} as departement
    on {{ normalise_libelle('departement.departement') }} = {{ normalise_libelle('source.departement') }}
