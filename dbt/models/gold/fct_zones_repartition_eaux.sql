-- Zone de répartition des eaux par polygone : l'adresse se situe par l'emprise, puis par un
-- test point dans polygone fait en Python, comme pour le zonage sismique
select
    gid,
    code_zone,
    nullif(nom_zone, '') as nom_zone,
    case type_zre
        when 'ZRESup' then 'eaux superficielles'
        when 'ZRESout' then 'eaux souterraines'
        else 'eaux superficielles et souterraines'
    end as ressource,
    geometrie,
    lon_min,
    lat_min,
    lon_max,
    lat_max
from {{ source('bronze', 'zones_repartition_eaux') }}
