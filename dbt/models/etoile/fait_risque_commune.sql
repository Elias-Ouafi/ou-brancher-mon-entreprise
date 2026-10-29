-- Grain : une commune et un risque recensé (dernier export GASPAR)
select
    code_insee_commune,
    num_risque,
    millesime as jour_millesime
from {{ ref('fct_risques_commune') }}
