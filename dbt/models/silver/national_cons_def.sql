select
    date_heure,
    {{ qualite_depuis_nature('nature') }} as qualite,
    {{ rang_qualite(qualite_depuis_nature('nature')) }} as rang_qualite,
    30 as pas_minutes,
    {% for mesure in mesures_communes() -%}
    {{ mesure }},
    {% endfor -%}
    fioul_tac,
    fioul_cogen,
    fioul_autres,
    gaz_tac,
    gaz_cogen,
    gaz_ccg,
    gaz_autres,
    hydraulique_fil_eau_eclusee,
    hydraulique_lacs,
    hydraulique_step_turbinage,
    bioenergies_dechets,
    bioenergies_biomasse,
    bioenergies_biogaz,
    source_dataset,
    ingested_at_utc
from {{ source('bronze', 'national_cons_def') }}
where consommation is not null
