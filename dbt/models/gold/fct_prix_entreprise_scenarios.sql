{#-
    Scénarios d'évolution du prix d'une tranche à 1, 2 et 3 ans. **Ce n'est pas
    une prévision.**

    C'est l'éventail de `fct_prix_entreprise_eventails` au dernier semestre
    publié : le scénario central brut (prix des 12 derniers mois × variation
    médiane observée depuis 2007), corrigé par les erreurs passées de la méthode —
    10e centile, médiane et 90e centile de `prix réalisé / central brut`.

    Prix nuls si la tranche n'a pas assez d'erreurs passées pour calibrer
    l'éventail. `fct_prix_entreprise_calibration` dit combien de fois cette
    méthode aurait encadré le prix réel par le passé.
-#}
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
