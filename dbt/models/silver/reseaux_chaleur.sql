-- Dernier chargement seulement : un tracé raccourci laisserait sinon ses anciens points au bronze
select
    reseau_id,
    cast(rang as integer) as rang,
    nom_reseau,
    -- La source sépare les communes d'une virgule sans espace
    regexp_replace(communes, '\s*,\s*', ', ', 'g') as communes,
    departements,
    gestionnaire,
    a_trace = 'oui' as a_trace,
    classe = 'oui' as classe,
    taux_enr_pct,
    contenu_co2_kg_kwh,
    livraisons_mwh,
    latitude,
    longitude
from {{ source('bronze', 'reseaux_chaleur') }}
qualify ingested_at_utc = max(ingested_at_utc) over ()
