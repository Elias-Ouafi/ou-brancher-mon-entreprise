with demi_heures as (
    select
        secteur,
        nb_points_soutirage,
        puissance_moyenne_kw,
        timezone('Europe/Paris', date_heure) as date_paris
    from {{ ref('profils_charge_sup36') }}
),

jours as (
    select
        secteur,
        case when isodow(date_paris) <= 5 then 'ouvre' else 'week_end' end as type_jour,
        hour(date_paris) as heure_debut,
        nb_points_soutirage,
        puissance_moyenne_kw
    from demi_heures
)

select
    case secteur when 'S2' then 'industrie_sup250kva' else 'tertiaire_sup250kva' end as profil,
    case secteur
        when 'S2' then 'Industrie, point moyen de plus de 250 kVA'
        else 'Tertiaire, point moyen de plus de 250 kVA'
    end as libelle,
    type_jour,
    heure_debut,
    heure_debut + 1 as heure_fin,
    -- Moyenne des régions et des demi-heures, pondérée par le nombre de points de soutirage
    sum(puissance_moyenne_kw * nb_points_soutirage) / sum(nb_points_soutirage) as puissance_kw,
    cast(0 as double) as puissance_flexible_kw,
    count(*) as nb_mesures
from jours
group by secteur, type_jour, heure_debut
