{#-
    Éventail de prix à 1, 2 et 3 ans, calculé à **chaque** semestre de l'histoire
    d'une tranche avec les seules données connues à ce moment-là. L'éventail est
    **calibré sur les erreurs passées de la méthode**, pas sur les seules
    variations de prix.

    Pour un semestre d'origine `t` et un horizon `h` (en années), en deux temps :

    1. **Scénario central brut** : le prix de référence de `t` (moyenne des
       semestres `t - 1` et `t`, soit les 12 mois qui s'achèvent en `t`) multiplié
       par la variation médiane `prix(s + 2h) / prix(s)` parmi celles dont
       l'arrivée `s + 2h` est au plus `t`. Même saison au départ et à l'arrivée,
       aucune information postérieure à `t`. Il faut au moins
       `prix_nb_variations_min` variations (4 par défaut : une médiane est stable).
    2. **Éventail** : à chaque origine passée `u` dont le résultat est connu en `t`
       (`u + 2h <= t`), l'erreur de la méthode est `prix réalisé / central brut`.
       Le 10e centile, la médiane et le 90e centile de ces erreurs, appliqués au
       central brut de `t`, donnent les prix bas, central et haut. Il faut au moins
       `prix_nb_erreurs_min` erreurs (10 par défaut).

    Pourquoi : un éventail tiré des seules variations de prix n'encadrait le prix
    réel que 3 à 9 fois sur 17 (contrôle du 15 octobre 2026). Calibré sur les
    erreurs, il intègre ce que la méthode a déjà manqué, crise de 2022 comprise.

    Le prix réalisé à l'horizon `h` est la moyenne des 12 mois qui s'achèvent
    `2h` semestres après l'origine : la même définition que le prix de référence.

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

-- Moyenne des 12 mois qui s'achèvent à chaque semestre ; `complet` si les deux
-- semestres sont publiés (exigé pour un prix réalisé, pas pour une référence).
annuel as (
    select
        fin.tranche,
        fin.rang_semestre as rang_fin,
        avg(publie.prix_htva_eur_kwh) as prix_annuel_eur_kwh,
        count(*) = 2 as complet
    from prix as fin
    inner join prix as publie
        on publie.tranche = fin.tranche
        and publie.rang_semestre between fin.rang_semestre - 1 and fin.rang_semestre
    group by fin.tranche, fin.rang_semestre
),

-- 1. Central brut à chaque origine, avec les seules variations déjà observables.
centraux as (
    select
        origine.tranche,
        origine.rang_semestre as rang_origine,
        origine.semestre as semestre_origine,
        variations.horizon_annees,
        count(*) as nb_variations_observees,
        quantile_cont(variations.variation, 0.5) as variation_centrale
    from prix as origine
    inner join variations
        on variations.tranche = origine.tranche
        and variations.rang_arrivee <= origine.rang_semestre
    group by origine.tranche, origine.rang_semestre, origine.semestre, variations.horizon_annees
),

centraux_dates as (
    select
        centraux.*,
        reference.prix_annuel_eur_kwh as prix_reference_eur_kwh,
        case
            when centraux.nb_variations_observees >= {{ var('prix_nb_variations_min', 4) }}
                then reference.prix_annuel_eur_kwh * centraux.variation_centrale
        end as prix_central_brut_eur_kwh,
        realise.prix_annuel_eur_kwh as prix_realise_eur_kwh
    from centraux
    inner join annuel as reference
        on reference.tranche = centraux.tranche
        and reference.rang_fin = centraux.rang_origine
    left join annuel as realise
        on realise.tranche = centraux.tranche
        and realise.rang_fin = centraux.rang_origine + 2 * centraux.horizon_annees
        and realise.complet
),

-- 2. Erreurs passées de la méthode, connues à chaque origine.
erreurs as (
    select
        origine.tranche,
        origine.rang_origine,
        origine.horizon_annees,
        count(*) as nb_erreurs_observees,
        quantile_cont(passe.prix_realise_eur_kwh / passe.prix_central_brut_eur_kwh, 0.1)
            as erreur_bas,
        quantile_cont(passe.prix_realise_eur_kwh / passe.prix_central_brut_eur_kwh, 0.5)
            as erreur_centrale,
        quantile_cont(passe.prix_realise_eur_kwh / passe.prix_central_brut_eur_kwh, 0.9)
            as erreur_haut
    from centraux_dates as origine
    inner join centraux_dates as passe
        on passe.tranche = origine.tranche
        and passe.horizon_annees = origine.horizon_annees
        and passe.rang_origine + 2 * passe.horizon_annees <= origine.rang_origine
        and passe.prix_central_brut_eur_kwh is not null
        and passe.prix_realise_eur_kwh is not null
    group by origine.tranche, origine.rang_origine, origine.horizon_annees
),

-- Éventail publié seulement s'il repose sur assez d'erreurs passées.
publiables as (
    select
        centraux_dates.*,
        coalesce(erreurs.nb_erreurs_observees, 0) as nb_erreurs_observees,
        erreurs.erreur_bas,
        erreurs.erreur_centrale,
        erreurs.erreur_haut,
        coalesce(erreurs.nb_erreurs_observees, 0) >= {{ var('prix_nb_erreurs_min', 10) }}
        and centraux_dates.prix_central_brut_eur_kwh is not null as est_publiable
    from centraux_dates
    left join erreurs
        on erreurs.tranche = centraux_dates.tranche
        and erreurs.rang_origine = centraux_dates.rang_origine
        and erreurs.horizon_annees = centraux_dates.horizon_annees
)

select
    tranche,
    rang_origine,
    semestre_origine,
    horizon_annees,
    nb_variations_observees,
    nb_erreurs_observees,
    prix_reference_eur_kwh,
    variation_centrale,
    prix_central_brut_eur_kwh,
    erreur_bas,
    erreur_centrale,
    erreur_haut,
    case when est_publiable then prix_central_brut_eur_kwh * erreur_bas end as prix_bas_eur_kwh,
    case when est_publiable then prix_central_brut_eur_kwh * erreur_centrale end
        as prix_central_eur_kwh,
    case when est_publiable then prix_central_brut_eur_kwh * erreur_haut end as prix_haut_eur_kwh,
    prix_realise_eur_kwh
from publiables
