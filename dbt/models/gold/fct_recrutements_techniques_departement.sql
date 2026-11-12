-- Projets de recrutement des métiers techniques d'un site de 1 à 50 MW, par département
select
    recrutements.code_departement,
    max(recrutements.annee) as annee,
    sum(recrutements.projets) as projets,
    sum(recrutements.projets_difficiles) as projets_difficiles,
    sum(recrutements.projets_difficiles) / nullif(sum(recrutements.projets), 0) as part_difficiles,
    sum(recrutements.nb_bassins_secrets) as nb_lignes_secretes,
    sum(recrutements.nb_bassins) as nb_lignes
from {{ ref('recrutements_departement') }} as recrutements
inner join {{ ref('metiers_techniques_bmo') }} as metiers
    on metiers.code_metier = recrutements.code_metier
group by recrutements.code_departement
