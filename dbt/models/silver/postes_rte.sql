{#-
    Postes électriques RTE (postes de transformation et points de piquage).

    La source ne publie ni coordonnées ni code de département, seulement le
    **nom** du département : le code est retrouvé via `normalise_libelle` et le
    référentiel `dim_departement`. Environ 3 % des postes n'ont pas de
    département publié ; ils restent sans code.

    Domaines de tension (arrêté du 17 mars 2003) : HTB1 jusqu'à 150 kV, HTB2 à
    225 kV, HTB3 à 400 kV. Les tensions sous 45 kV, le courant continu et les
    postes hors tension ne sont rangés dans aucun domaine.
-#}
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
