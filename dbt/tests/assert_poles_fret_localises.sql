-- Un port ou un aéroport au-dessus du plus petit seuil doit avoir sa commune dans le seed :
-- sinon le pôle disparaîtrait de la carte sans bruit
with ports as (
    select 'port' as mode, locode as code, port as nom
    from {{ source('bronze', 'ports_fret') }}
    where annee = (select max(annee) from {{ source('bronze', 'ports_fret') }})
        and marchandises_t >= {{ var('fret_seuil_port_zni_t', 500000) }}
),

aeroports as (
    select 'aeroport' as mode, code_oaci as code, aeroport as nom
    from {{ source('bronze', 'aeroports_fret') }}
    where annee = (
        select max(annee) from {{ source('bronze', 'aeroports_fret') }} where nb_mois = 12
    )
        and fret_t >= {{ var('fret_seuil_aeroport_zni_t', 3000) }}
        -- Lignes « autres aéroports » et Pacifique (Tahiti, Nouméa), hors des cartes
        and code_oaci not like 'ZZ%'
        and code_oaci not like 'N%'
)

select p.*
from (select * from ports union all select * from aeroports) as p
left join {{ ref('dim_ports_fret') }} as dp
    on p.mode = 'port' and dp.locode = p.code
left join {{ ref('dim_aeroports_fret') }} as da
    on p.mode = 'aeroport' and da.code_oaci = p.code
where dp.locode is null and da.code_oaci is null
