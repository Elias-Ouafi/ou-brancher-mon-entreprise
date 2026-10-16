-- Scénarios d'évolution du prix d'une tranche à 1, 2 et 3 ans
select
    tranche,
    horizon_annees,
    nb_variations_observees,
    nb_erreurs_observees,
    prix_reference_eur_kwh,
    variation_centrale,
    prix_central_brut_eur_kwh,
    erreur_bas,
    erreur_centrale,
    erreur_haut,
    prix_bas_eur_kwh,
    prix_central_eur_kwh,
    prix_haut_eur_kwh
from {{ ref('fct_prix_entreprise_eventails') }}
qualify rang_origine = max(rang_origine) over (partition by tranche)
