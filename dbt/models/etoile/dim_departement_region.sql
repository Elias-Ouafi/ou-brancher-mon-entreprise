select
    departements.code_departement,
    departements.departement,
    departements.code_insee_region,
    regions.region,
    regions.est_zni
from {{ ref('dim_departement') }} as departements
left join {{ ref('dim_region') }} as regions
    on regions.code_insee_region = departements.code_insee_region
