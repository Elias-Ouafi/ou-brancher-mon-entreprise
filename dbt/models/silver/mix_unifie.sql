-- Série unique du mix électrique, à la meilleure qualité disponible
with borne as (
    select max(date_heure) + interval 30 minute as debut_temps_reel
    from {{ ref('national_cons_def') }}
),

consolide as (
    select
        date_heure, qualite, rang_qualite, pas_minutes,
        {% for mesure in mesures_communes() -%}
        {{ mesure }},
        {% endfor -%}
        source_dataset,
        ingested_at_utc
    from {{ ref('national_cons_def') }}
),

temps_reel as (
    select
        date_heure, qualite, rang_qualite, pas_minutes,
        {% for mesure in mesures_communes() -%}
        {{ mesure }},
        {% endfor -%}
        source_dataset,
        ingested_at_utc
    from {{ ref('national_tr') }}
    where date_heure >= coalesce((select debut_temps_reel from borne), '-infinity'::timestamptz)
)

select * from consolide
union all
select * from temps_reel
