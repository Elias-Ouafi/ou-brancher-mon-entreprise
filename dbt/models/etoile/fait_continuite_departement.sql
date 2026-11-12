-- Grain : un département desservi par Enedis, à sa dernière année publiée
select
    code_departement,
    make_date(derniere_annee, 1, 1) as jour_reference,
    part_mal_alimentes_pct,
    moyenne_5_ans_pct,
    annees_au_dela_seuil_5_ans,
    mediane_nationale_pct,
    depasse_seuil
from {{ ref('fct_continuite_departement') }}
