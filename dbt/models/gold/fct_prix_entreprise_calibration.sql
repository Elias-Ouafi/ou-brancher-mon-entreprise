with periodes as (
    select
        tranche,
        rang_origine,
        semestre_origine,
        avg(prix_bas_eur_kwh) as prix_bas_eur_kwh,
        avg(prix_central_eur_kwh) as prix_central_eur_kwh,
        avg(prix_haut_eur_kwh) as prix_haut_eur_kwh,
        avg(prix_realise_eur_kwh) as prix_realise_eur_kwh
    from {{ ref('fct_prix_entreprise_backtest') }}
    group by tranche, rang_origine, semestre_origine
    having count(*) = 3
)

select
    tranche,
    count(*) as nb_periodes_controlees,
    count(*) filter (
        where prix_realise_eur_kwh between prix_bas_eur_kwh and prix_haut_eur_kwh
    ) as nb_dans_eventail,
    count(*) filter (where prix_realise_eur_kwh > prix_haut_eur_kwh) as nb_au_dessus,
    count(*) filter (where prix_realise_eur_kwh < prix_bas_eur_kwh) as nb_en_dessous,
    arg_min(semestre_origine, rang_origine) as premiere_origine_controlee,
    arg_max(semestre_origine, rang_origine) as derniere_origine_controlee,
    median(prix_realise_eur_kwh / prix_central_eur_kwh - 1) as ecart_median_au_central
from periodes
group by tranche
