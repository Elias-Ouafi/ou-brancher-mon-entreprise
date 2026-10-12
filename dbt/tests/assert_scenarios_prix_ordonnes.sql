-- Les scénarios d'une tranche sont ordonnés : bas <= central <= haut, à chaque horizon.
select tranche, horizon_annees
from {{ ref('fct_prix_entreprise_scenarios') }}
where not (prix_bas_eur_kwh <= prix_central_eur_kwh and prix_central_eur_kwh <= prix_haut_eur_kwh)
