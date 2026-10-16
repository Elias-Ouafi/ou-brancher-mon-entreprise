-- Contraintes résiduelles prospectives (horizon 3 à 5 ans) sur le réseau de transport
select
    source.ouvrage,
    source.nom_de_l_ouvrage,
    region.code_insee_region,
    source.poste_1,
    nullif(source.poste_2, '') as poste_2,
    nullif(source.poste_3, '') as poste_3,
    source.puissance_max_de_l_ouvrage as puissance_max_mw,
    coalesce(source.specificite = 'Contrainte en journée', false) as est_en_journee,
    source.occurrence,
    case split_part(source.occurrence, ' :', 1)
        when 'Très faible' then 1
        when 'Faible' then 2
        when 'Moyenne' then 3
        when 'Forte' then 4
        when 'Très forte' then 5
    end as rang_occurrence,
    source.duree,
    lower(source.perennite) as perennite,
    case source.perennite when 'FAIBLE' then 1 when 'MOYENNE' then 2 when 'ELEVEE' then 3 end
        as rang_perennite
from {{ source('bronze', 'contraintes_region') }} as source
left join {{ ref('dim_region') }} as region
    on {{ normalise_libelle('region.region') }} = {{ normalise_libelle('source.region') }}
