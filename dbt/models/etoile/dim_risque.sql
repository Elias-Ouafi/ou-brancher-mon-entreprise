select distinct
    num_risque,
    libelle_risque,
    famille
from {{ ref('fct_risques_commune') }}
