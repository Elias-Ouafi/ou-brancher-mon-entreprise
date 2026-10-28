select
    cast(source.annee as integer) as annee,
    source.code_commune as code_insee_commune,
    source.tri_des_adresses,
    lpad(source.code_iris, 9, '0') as code_iris,
    source.adresse,
    -- Clé de rapprochement avec l'adresse BAN, normalisée de la même façon côté Python
    case
        when source.adresse is not null then {{ normalise_libelle('source.adresse') }}
    end as adresse_normalisee,
    source.code_grand_secteur as grand_secteur,
    source.code_secteur_naf2 as code_naf2,
    cast(source.nombre_de_sites as integer) as nb_sites,
    source.consommation_annuelle_totale_de_ladresse_mwh as consommation_mwh,
    {{ code_departement('source.code_commune') }} as code_departement,
    departement.code_insee_region
from {{ source('bronze', 'enedis_conso_entreprises_adresse') }} as source
left join {{ ref('dim_departement') }} as departement
    on departement.code_departement = {{ code_departement('source.code_commune') }}
