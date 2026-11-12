select
    code_departement,
    code_metier,
    libelle_metier,
    annee,
    projets,
    projets_difficiles,
    nb_bassins,
    nb_bassins_secrets
from {{ source('bronze', 'recrutements_bmo') }}
