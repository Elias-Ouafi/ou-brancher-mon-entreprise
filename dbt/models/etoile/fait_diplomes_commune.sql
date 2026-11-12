-- Grain : une commune, au dernier millésime du recensement
select
    code_insee_commune,
    make_date(millesime::integer, 1, 1) as jour_reference,
    nb_superieur,
    nb_cap_bep_bac,
    nb_15_ans_plus
from {{ ref('fct_diplomes_commune') }}
