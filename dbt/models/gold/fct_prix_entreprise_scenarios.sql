{#-
    Scénarios d'évolution du prix d'une tranche à 1, 2 et 3 ans, tirés de son
    propre historique. **Ce n'est pas une prévision.**

    Pour chaque horizon `h` (en années), on observe toutes les variations
    `prix(t + 2h semestres) / prix(t)` de la tranche depuis 2007 — même saison au
    départ et à l'arrivée, donc sans biais saisonnier. Le 10e centile, la médiane et
    le 90e centile de ces variations sont appliqués au **prix de référence** : la
    moyenne des deux derniers semestres publiés, soit les 12 derniers mois.

    Lecture : « dans 80 % des périodes de 3 ans observées depuis 2007, le prix de
    cette tranche a évolué entre `variation_bas` et `variation_haut` ». La crise de
    2022-2023 fait partie de l'historique, et c'est voulu : elle élargit le
    scénario haut au lieu d'être oubliée.
-#}
with prix as (
    select * from {{ ref('prix_entreprises') }}
),

-- Les deux derniers semestres publiés de chaque tranche : les 12 derniers mois.
reference as (
    select tranche, avg(prix_htva_eur_kwh) as prix_reference_eur_kwh
    from (
        select * from prix
        qualify rang_semestre >= max(rang_semestre) over (partition by tranche) - 1
    )
    group by tranche
),

horizons as (
    select unnest([1, 2, 3]) as horizon_annees
),

variations as (
    select
        depart.tranche,
        horizons.horizon_annees,
        arrivee.prix_htva_eur_kwh / depart.prix_htva_eur_kwh as variation
    from prix as depart
    cross join horizons
    inner join prix as arrivee
        on arrivee.tranche = depart.tranche
        and arrivee.rang_semestre = depart.rang_semestre + 2 * horizons.horizon_annees
),

quantiles as (
    select
        tranche,
        horizon_annees,
        count(*) as nb_variations_observees,
        quantile_cont(variation, 0.1) as variation_bas,
        quantile_cont(variation, 0.5) as variation_centrale,
        quantile_cont(variation, 0.9) as variation_haut
    from variations
    group by tranche, horizon_annees
)

select
    quantiles.tranche,
    quantiles.horizon_annees,
    quantiles.nb_variations_observees,
    reference.prix_reference_eur_kwh,
    quantiles.variation_bas,
    quantiles.variation_centrale,
    quantiles.variation_haut,
    reference.prix_reference_eur_kwh * quantiles.variation_bas as prix_bas_eur_kwh,
    reference.prix_reference_eur_kwh * quantiles.variation_centrale as prix_central_eur_kwh,
    reference.prix_reference_eur_kwh * quantiles.variation_haut as prix_haut_eur_kwh
from quantiles
inner join reference on reference.tranche = quantiles.tranche
