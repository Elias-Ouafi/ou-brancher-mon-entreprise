select
    reseau_id,
    rang,
    coalesce(nom_reseau, reseau_id) as nom_reseau,
    communes,
    gestionnaire,
    a_trace,
    classe,
    taux_enr_pct,
    contenu_co2_kg_kwh,
    livraisons_mwh,
    latitude,
    longitude
from {{ ref('reseaux_chaleur') }}
