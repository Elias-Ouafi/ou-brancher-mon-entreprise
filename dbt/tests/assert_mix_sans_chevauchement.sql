-- Aucune mesure ne doit empiéter sur la suivante : chaque ligne couvre `[date_heure
with mesures as (
    select
        date_heure,
        date_heure + to_minutes(pas_minutes) as fin,
        lead(date_heure) over (order by date_heure) as suivante
    from {{ ref('mix_unifie') }}
)

select *
from mesures
where suivante < fin
