-- Éventail de prix à 1, 2 et 3 ans, calculé à chaque semestre de l'histoire d'une tranche
with prix as (
    select tranche, semestre, rang_semestre, prix_htva_eur_kwh
    from {{ ref('prix_entreprises') }}
),

horizons as (
    select unnest([1, 2, 3]) as horizon_annees
),

-- Toutes les variations de la tranche, datées par leur semestre d'arrivée
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

-- Moyenne des 12 mois qui s'achèvent à chaque semestre
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

-- Central brut à chaque origine, avec les seules variations déjà observables
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

-- Erreurs passées de la méthode, connues à chaque origine
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

-- Éventail publié seulement s'il repose sur assez d'erreurs passées
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
    -- Le statu quo reste toujours dans l'éventail : des erreurs passées toutes de même signe
    -- (crise de 2022) ne doivent pas exclure un prix inchangé
    case
        when est_publiable
            then least(prix_central_brut_eur_kwh * erreur_bas, prix_reference_eur_kwh)
    end as prix_bas_eur_kwh,
    case when est_publiable then prix_central_brut_eur_kwh * erreur_centrale end
        as prix_central_eur_kwh,
    case
        when est_publiable
            then greatest(prix_central_brut_eur_kwh * erreur_haut, prix_reference_eur_kwh)
    end as prix_haut_eur_kwh,
    prix_realise_eur_kwh
from publiables
