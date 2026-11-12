-- Grain : un département, à l'année de l'enquête BMO
select
    code_departement,
    make_date(annee::integer, 1, 1) as jour_reference,
    projets,
    projets_difficiles,
    part_difficiles,
    nb_lignes_secretes,
    nb_lignes
from {{ ref('fct_recrutements_techniques_departement') }}
