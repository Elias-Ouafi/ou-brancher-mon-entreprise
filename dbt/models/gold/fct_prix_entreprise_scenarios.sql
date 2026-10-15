{#-
    Scénarios d'évolution du prix d'une tranche à 1, 2 et 3 ans, tirés de son
    propre historique. **Ce n'est pas une prévision.**

    C'est l'éventail de `fct_prix_entreprise_eventails` calculé au dernier
    semestre publié : le 10e centile, la médiane et le 90e centile de toutes les
    variations observées depuis 2007, appliqués au prix des 12 derniers mois.

    Lecture : « dans 80 % des périodes observées depuis 2007, le prix de cette
    tranche a évolué entre `variation_bas` et `variation_haut` ». La crise de
    2022-2023 fait partie de l'historique, et c'est voulu : elle élargit le
    scénario haut au lieu d'être oubliée. `fct_prix_entreprise_calibration` dit
    combien de fois cette méthode aurait encadré le prix réel par le passé.
-#}
select
    tranche,
    horizon_annees,
    nb_variations_observees,
    prix_reference_eur_kwh,
    variation_bas,
    variation_centrale,
    variation_haut,
    prix_bas_eur_kwh,
    prix_central_eur_kwh,
    prix_haut_eur_kwh
from {{ ref('fct_prix_entreprise_eventails') }}
qualify rang_origine = max(rang_origine) over (partition by tranche)
