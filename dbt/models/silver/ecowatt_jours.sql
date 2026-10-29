select
    cast(timezone('Europe/Paris', date_heure) as date) as jour,
    case cast(niveau as integer)
        when 1 then 'vert'
        when 2 then 'orange'
        when 3 then 'rouge'
    end as niveau,
    cast(heures_en_alerte as integer) as heures_en_alerte
from {{ source('bronze', 'rte_ecowatt') }}
where niveau in (1, 2, 3)
