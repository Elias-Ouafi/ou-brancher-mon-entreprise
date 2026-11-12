select
    code_commune as code_insee_commune,
    millesime,
    nb_cap_bep,
    nb_bac,
    nb_superieur,
    nb_15_ans_plus
from {{ source('bronze', 'diplomes_commune') }}
