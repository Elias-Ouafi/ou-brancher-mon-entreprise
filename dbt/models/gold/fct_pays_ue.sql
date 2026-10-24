-- Prix et carbone moyens de chaque pays de l'UE, par tranche de consommation : le repère d'une
-- implantation hors de France, sur les mêmes définitions que pour la France
with prix as (
    select
        pays as code_eurostat,
        tranche,
        semestre,
        cast(left(semestre, 4) as integer) * 2 + cast(right(semestre, 1) as integer) - 1 as rang,
        prix_eur_kwh
    from {{ source('bronze', 'eurostat_nrg_pc_205_ue') }}
    where fiscalite = 'X_VAT' and prix_eur_kwh is not null
),

dernier as (
    select code_eurostat, tranche, max(rang) as rang
    from prix
    group by all
),

-- Moyenne des deux derniers semestres publiés, comme pour la France
reference as (
    select
        prix.code_eurostat,
        prix.tranche,
        avg(prix.prix_eur_kwh) as prix_reference_eur_kwh,
        min(prix.semestre) as premier_semestre_reference,
        max(prix.semestre) as dernier_semestre
    from prix
    inner join dernier
        on dernier.code_eurostat = prix.code_eurostat and dernier.tranche = prix.tranche
    where prix.rang >= dernier.rang - 1
    group by all
),

carbone as (
    select
        iso3,
        arg_max(intensite_g_co2e_kwh, annee) as intensite_g_co2e_kwh,
        cast(max(annee) as integer) as annee_carbone
    from {{ source('bronze', 'ember_intensite_carbone') }}
    group by all
)

select
    pays.code_eurostat,
    pays.iso2,
    pays.iso3,
    pays.pays,
    tranches.code as code_tranche,
    tranches.libelle as libelle_tranche,
    tranches.conso_min_mwh,
    tranches.conso_max_mwh,
    reference.prix_reference_eur_kwh,
    reference.premier_semestre_reference,
    reference.dernier_semestre,
    carbone.intensite_g_co2e_kwh,
    carbone.annee_carbone
from {{ ref('dim_pays_ue') }} as pays
cross join {{ ref('tranches_eurostat') }} as tranches
left join reference
    on reference.code_eurostat = pays.code_eurostat and reference.tranche = tranches.tranche
left join carbone on carbone.iso3 = pays.iso3
