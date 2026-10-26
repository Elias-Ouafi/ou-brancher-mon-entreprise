-- Qualité de fourniture du département : dernière année publiée, moyenne des cinq dernières
-- et comparaison au seuil réglementaire de 3 % de clients mal alimentés
with mesures as (
    select
        code_departement,
        cast(annee as integer) as annee,
        part_mal_alimentes_pct
    from {{ source('bronze', 'enedis_continuite_alimentation') }}
),

derniere as (
    select max(annee) as annee from mesures
),

par_departement as (
    select
        mesures.code_departement,
        max(mesures.annee) as derniere_annee,
        arg_max(mesures.part_mal_alimentes_pct, mesures.annee) as part_mal_alimentes_pct,
        avg(mesures.part_mal_alimentes_pct)
            filter (where mesures.annee > derniere.annee - 5) as moyenne_5_ans_pct,
        count(*) filter (
            where mesures.annee > derniere.annee - 5 and mesures.part_mal_alimentes_pct > 3
        ) as annees_au_dela_seuil_5_ans
    from mesures
    cross join derniere
    group by mesures.code_departement
)

-- Médiane des seuls départements publiés la dernière année, pour comparer à date égale
select
    par_departement.*,
    (
        select median(autres.part_mal_alimentes_pct)
        from par_departement as autres
        where autres.derniere_annee = (select annee from derniere)
    ) as mediane_nationale_pct,
    par_departement.part_mal_alimentes_pct > 3 as depasse_seuil
from par_departement
