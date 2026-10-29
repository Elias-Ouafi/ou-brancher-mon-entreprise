-- Grain : un jour civil (heure de Paris), national
select
    jour,
    est_complet,
    consommation_gwh,
    production_gwh,
    part_bas_carbone,
    part_renouvelable,
    solde_echanges_gwh,
    emissions_tco2,
    taux_co2_moyen_g_kwh
from {{ ref('fct_mix_journalier') }}
