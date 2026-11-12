-- Grain : une commune et une année publiée par Enedis
select
    code_insee_commune,
    make_date(annee::integer, 1, 1) as jour_reference,
    annee,
    nb_sites,
    nb_sites_industrie,
    consommation_mwh,
    consommation_industrie_mwh,
    consommation_tertiaire_mwh,
    consommation_agriculture_mwh,
    code_naf2_principal,
    consommation_naf2_principal_mwh,
    rang_industrie_departement,
    nb_communes_departement
from {{ ref('fct_conso_entreprises_commune') }}
