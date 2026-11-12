select code_naf2, libelle_naf2
from {{ ref('dim_naf2') }}
