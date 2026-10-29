select
    code_region,
    secteur,
    profil,
    date_heure,
    nb_points_soutirage,
    -- Wh sur une demi-heure par point de soutirage -> kW moyens
    courbe_moyenne_wh * 2 / 1000 as puissance_moyenne_kw
from {{ source('bronze', 'enedis_profils_charge_sup36') }}
-- « S » : courbe masquée par le secret statistique
where courbe_moyenne_wh is not null
    and indice_representativite is not null
    and indice_representativite <> 'S'
