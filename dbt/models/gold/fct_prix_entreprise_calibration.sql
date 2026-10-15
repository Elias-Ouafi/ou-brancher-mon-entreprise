{#-
    Calibration de la méthode des scénarios, par tranche : combien de fois le
    **prix moyen des 3 années suivantes** — le chiffre affiché par le diagnostic —
    est tombé dans l'éventail bas–haut calculé au départ.

    Une période est contrôlée si ses trois horizons le sont. Les périodes
    commencent chaque semestre et durent 3 ans : elles se chevauchent, et ne
    sont donc pas indépendantes. Avec un éventail du 10e au 90e centile, une
    méthode bien calibrée encadrerait le prix réel environ 8 fois sur 10.
-#}
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
