-- Zone de sismicité réglementaire par polygone communal : l'adresse se situe par son emprise,
-- puis par un test point dans polygone fait en Python
select
    gid,
    cast(zone_sismicite as integer) as zone_sismicite,
    libelle_zone,
    geometrie,
    lon_min,
    lat_min,
    lon_max,
    lat_max
from {{ source('bronze', 'zonage_sismique') }}
