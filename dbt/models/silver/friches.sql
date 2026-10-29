-- Friches assez grandes pour un projet de 1 à 50 MW et encore à reprendre
select
    site_id,
    site_nom,
    coalesce(site_type, 'inconnu') as type_friche,
    site_statut as statut,
    site_reconv_type as reconversion_prevue,
    comm_insee as code_insee_commune,
    comm_nom as commune,
    proprio_type as type_proprietaire,
    sol_pollution_existe as pollution_sol,
    urba_zone_type as type_zone_urbanisme,
    unite_fonciere_surface_m2 as surface_m2,
    bati_surface_m2 as surface_bati_m2,
    try_cast(site_actu_date as date) as date_actualisation,
    latitude,
    longitude
from {{ source('bronze', 'friches') }}
-- Les surfaces absentes sont écartées plutôt que supposées
where unite_fonciere_surface_m2 >= {{ var('friches_surface_min_m2', 10000) }}
    and coalesce(site_statut, '') != 'friche reconvertie'
