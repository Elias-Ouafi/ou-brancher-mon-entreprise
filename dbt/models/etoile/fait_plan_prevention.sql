-- Grain : une commune et une procédure de plan de prévention en vigueur (dernier export GASPAR)
select
    code_insee_commune,
    code_procedure,
    type_plan,
    libelle_plan,
    risques,
    etat,
    date_prescription as jour_prescription,
    date_approbation as jour_approbation
from {{ ref('fct_plans_prevention_commune') }}
