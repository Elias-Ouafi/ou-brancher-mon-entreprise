select
    cast(strptime(mois || '-01', '%Y-%m-%d') as date) as mois,
    code_insee_region,
    production_totale as production_totale_mwh,
    consommation_brute as consommation_brute_mwh,
    pompage as pompage_mwh,
    solde_echanges_physiques as solde_echanges_physiques_mwh
from {{ source('bronze', 'equilibre_regional_mensuel_prod_conso_brute') }}
