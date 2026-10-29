-- Calendrier civil, un jour par ligne ; la plage couvre les séries chargées (eco2mix depuis 2012, DVF, Enedis)
select
    cast(jour as date) as jour,
    year(jour) as annee,
    quarter(jour) as trimestre,
    month(jour) as mois,
    date_trunc('month', jour)::date as debut_mois,
    isodow(jour) as jour_semaine,
    isodow(jour) >= 6 as est_weekend
from generate_series(date '2010-01-01', date '2035-12-31', interval 1 day) as t(jour)
