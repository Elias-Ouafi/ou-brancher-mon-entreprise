-- Temps réel nettoyé : une ligne par quart d'heure effectivement mesuré
select
    date_heure,
    {{ qualite_depuis_nature('nature') }} as qualite,
    {{ rang_qualite(qualite_depuis_nature('nature')) }} as rang_qualite,
    15 as pas_minutes,
    {% for mesure in mesures_communes() -%}
    {{ mesure }},
    {% endfor -%}
    source_dataset,
    ingested_at_utc
from {{ source('bronze', 'national_tr') }}
where consommation is not null
