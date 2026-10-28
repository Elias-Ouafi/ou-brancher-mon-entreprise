with derniere as (
    select max(annee) as annee from {{ ref('conso_entreprises_adresse') }}
),

lignes as (
    select adresses.*
    from {{ ref('conso_entreprises_adresse') }} as adresses
    inner join derniere on derniere.annee = adresses.annee
),

par_secteur as (
    select
        code_insee_commune,
        code_naf2,
        min(grand_secteur) as grand_secteur,
        sum(consommation_mwh) as consommation_mwh
    from lignes
    where code_naf2 is not null
    group by code_insee_commune, code_naf2
),

premier as (
    select
        code_insee_commune,
        arg_max(code_naf2, consommation_mwh) as code_naf2_principal,
        max(consommation_mwh) as consommation_naf2_principal_mwh
    from par_secteur
    group by code_insee_commune
),

communes as (
    select
        annee,
        code_insee_commune,
        min(code_departement) as code_departement,
        min(code_insee_region) as code_insee_region,
        count(*)::integer as nb_lignes,
        coalesce(sum(nb_sites), 0)::integer as nb_sites,
        coalesce(sum(consommation_mwh), 0) as consommation_mwh,
        coalesce(sum(consommation_mwh) filter (where grand_secteur = 'INDUSTRIE'), 0)
            as consommation_industrie_mwh,
        coalesce(sum(consommation_mwh) filter (where grand_secteur = 'TERTIAIRE'), 0)
            as consommation_tertiaire_mwh,
        coalesce(sum(consommation_mwh) filter (where grand_secteur = 'AGRICULTURE'), 0)
            as consommation_agriculture_mwh,
        coalesce(sum(nb_sites) filter (where grand_secteur = 'INDUSTRIE'), 0)::integer
            as nb_sites_industrie
    from lignes
    group by annee, code_insee_commune
)

select
    communes.*,
    premier.code_naf2_principal,
    naf.libelle_naf2 as libelle_naf2_principal,
    premier.consommation_naf2_principal_mwh,
    -- Place de la commune dans son département pour l'industrie raccordée à Enedis
    rank() over (
        partition by communes.code_departement
        order by communes.consommation_industrie_mwh desc
    )::integer as rang_industrie_departement,
    count(*) over (partition by communes.code_departement)::integer as nb_communes_departement
from communes
left join premier on premier.code_insee_commune = communes.code_insee_commune
left join {{ ref('dim_naf2') }} as naf on naf.code_naf2 = premier.code_naf2_principal
