-- Calendrier civil, un jour par ligne ; la plage couvre les séries chargées, dont les plans de
-- prévention prescrits depuis 1935
select
    cast(jour as date) as jour,
    year(jour) as annee,
    quarter(jour) as trimestre,
    month(jour) as mois,
    date_trunc('month', jour)::date as debut_mois,
    isodow(jour) as jour_semaine,
    isodow(jour) >= 6 as est_weekend
from generate_series(date '1930-01-01', date '2035-12-31', interval 1 day) as t(jour)
