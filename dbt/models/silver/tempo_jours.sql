-- Calendrier Tempo : une ligne par jour Tempo, couleur normalisée en français
select
    cast(timezone('Europe/Paris', date_heure) as date) as jour_tempo,
    case couleur
        when 'BLUE' then 'bleu'
        when 'WHITE' then 'blanc'
        when 'RED' then 'rouge'
    end as couleur,
    ingested_at_utc
from {{ source('bronze', 'rte_tempo') }}
where couleur is not null
