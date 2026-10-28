with source as (
    select * from {{ source('bronze', 'eurostat_nrg_pc_205') }}
),

semestres as (
    select
        semestre,
        tranche,
        cast(left(semestre, 4) as integer) as annee,
        cast(right(semestre, 1) as integer) as numero_semestre,
        max(prix_eur_kwh) filter (where fiscalite = 'X_TAX') as prix_ht_eur_kwh,
        max(prix_eur_kwh) filter (where fiscalite = 'X_VAT') as prix_htva_eur_kwh,
        max(statut) as statut
    from source
    group by semestre, tranche
)

select
    semestre,
    annee,
    numero_semestre,
    make_date(annee, case numero_semestre when 1 then 1 else 7 end, 1) as debut_semestre,
    annee * 2 + numero_semestre - 1 as rang_semestre,
    tranche,
    prix_ht_eur_kwh,
    prix_htva_eur_kwh,
    prix_htva_eur_kwh - prix_ht_eur_kwh as taxes_non_recuperables_eur_kwh,
    statut
from semestres
where prix_htva_eur_kwh is not null
