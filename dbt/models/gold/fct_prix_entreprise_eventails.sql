{#-
    Éventail de prix à 1, 2 et 3 ans, calculé à **chaque** semestre de l'histoire
    d'une tranche avec les seules données connues à ce moment-là.

    Pour un semestre d'origine `t` et un horizon `h` (en années) :

    * les variations retenues sont `prix(s + 2h) / prix(s)` dont le semestre
      d'arrivée `s + 2h` est au plus `t` — même saison au départ et à l'arrivée,
      donc sans biais saisonnier, et aucune information postérieure à `t` ;
    * le 10e centile, la médiane et le 90e centile de ces variations sont
      appliqués au **prix de référence** de `t` : la moyenne des semestres `t - 1`
      et `t`, soit les 12 mois qui s'achèvent en `t`.

    Une seule définition sert deux usages : les scénarios affichés
    (`fct_prix_entreprise_scenarios`, origine = dernier semestre publié) et le
    contrôle de la méthode sur le passé (`fct_prix_entreprise_backtest`).
-#}
with prix as (
    select tranche, semestre, rang_semestre, prix_htva_eur_kwh
    from {{ ref('prix_entreprises') }}
),

horizons as (
    select unnest([1, 2, 3]) as horizon_annees
),

-- Toutes les variations de la tranche, datées par leur semestre d'arrivée.
variations as (
    select
        depart.tranche,
        horizons.horizon_annees,
        arrivee.rang_semestre as rang_arrivee,
        arrivee.prix_htva_eur_kwh / depart.prix_htva_eur_kwh as variation
    from prix as depart
    cross join horizons
    inner join prix as arrivee
        on arrivee.tranche = depart.tranche
        and arrivee.rang_semestre = depart.rang_semestre + 2 * horizons.horizon_annees
),

-- À chaque origine, seules les variations déjà observables à cette date.
quantiles as (
    select
        origine.tranche,
        origine.rang_semestre as rang_origine,
        origine.semestre as semestre_origine,
        variations.horizon_annees,
        count(*) as nb_variations_observees,
        quantile_cont(variations.variation, 0.1) as variation_bas,
        quantile_cont(variations.variation, 0.5) as variation_centrale,
        quantile_cont(variations.variation, 0.9) as variation_haut
    from prix as origine
    inner join variations
        on variations.tranche = origine.tranche
        and variations.rang_arrivee <= origine.rang_semestre
    group by origine.tranche, origine.rang_semestre, origine.semestre, variations.horizon_annees
),

-- Les 12 mois qui s'achèvent à l'origine : semestres t - 1 et t.
reference as (
    select
        origine.tranche,
        origine.rang_semestre as rang_origine,
        avg(publie.prix_htva_eur_kwh) as prix_reference_eur_kwh
    from prix as origine
    inner join prix as publie
        on publie.tranche = origine.tranche
        and publie.rang_semestre between origine.rang_semestre - 1 and origine.rang_semestre
    group by origine.tranche, origine.rang_semestre
)

select
    quantiles.tranche,
    quantiles.rang_origine,
    quantiles.semestre_origine,
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
inner join reference
    on reference.tranche = quantiles.tranche
    and reference.rang_origine = quantiles.rang_origine
