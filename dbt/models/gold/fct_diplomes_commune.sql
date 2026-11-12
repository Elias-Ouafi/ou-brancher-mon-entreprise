-- Diplômés par commune, placés au centre de la commune : le diagnostic somme celles dont le
-- centre est dans l'isochrone de l'adresse
select
    diplomes.code_insee_commune,
    diplomes.millesime,
    diplomes.nb_superieur,
    diplomes.nb_cap_bep + diplomes.nb_bac as nb_cap_bep_bac,
    diplomes.nb_15_ans_plus,
    centres.longitude,
    centres.latitude
from {{ ref('diplomes_commune') }} as diplomes
inner join {{ source('bronze', 'communes_centres') }} as centres
    on centres.code_commune = diplomes.code_insee_commune
