{#-
    Contrôle de la méthode des scénarios sur le passé : chaque éventail calculé à
    un semestre d'origine est confronté au prix **réellement** observé ensuite.

    * Le prix réalisé à l'horizon `h` est la moyenne des 12 mois qui s'achèvent
      `2h` semestres après l'origine — la même définition que le prix de
      référence, pour comparer des moyennes annuelles entre elles. Les deux
      semestres doivent être publiés.
    * Seuls les éventails assez nourris sont contrôlés (au moins
      `prix_nb_variations_min` variations), comme seuls ceux-là sont affichés.
-#}
with eventails as (
    select *
    from {{ ref('fct_prix_entreprise_eventails') }}
    where nb_variations_observees >= {{ var('prix_nb_variations_min', 10) }}
),

prix as (
    select tranche, rang_semestre, prix_htva_eur_kwh
    from {{ ref('prix_entreprises') }}
),

-- Moyenne des 12 mois qui s'achèvent à chaque semestre, si les deux sont publiés.
annuel as (
    select
        fin.tranche,
        fin.rang_semestre as rang_fin,
        avg(publie.prix_htva_eur_kwh) as prix_annuel_eur_kwh
    from prix as fin
    inner join prix as publie
        on publie.tranche = fin.tranche
        and publie.rang_semestre between fin.rang_semestre - 1 and fin.rang_semestre
    group by fin.tranche, fin.rang_semestre
    having count(*) = 2
)

select
    eventails.tranche,
    eventails.rang_origine,
    eventails.semestre_origine,
    eventails.horizon_annees,
    eventails.nb_variations_observees,
    eventails.prix_reference_eur_kwh,
    eventails.prix_bas_eur_kwh,
    eventails.prix_central_eur_kwh,
    eventails.prix_haut_eur_kwh,
    annuel.prix_annuel_eur_kwh as prix_realise_eur_kwh,
    case
        when annuel.prix_annuel_eur_kwh < eventails.prix_bas_eur_kwh then 'en_dessous'
        when annuel.prix_annuel_eur_kwh > eventails.prix_haut_eur_kwh then 'au_dessus'
        else 'dans_eventail'
    end as position
from eventails
inner join annuel
    on annuel.tranche = eventails.tranche
    and annuel.rang_fin = eventails.rang_origine + 2 * eventails.horizon_annees
