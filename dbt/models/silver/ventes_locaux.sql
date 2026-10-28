-- Ventes de locaux d'activité seules (ni logement, ni plusieurs dispositions), avec prix au m²
with ventes as (
    select
        id_mutation,
        cast(date_mutation as date) as date_mutation,
        -- DVF cite les arrondissements ; l'adresse géocodée est ramenée à la commune entière
        case
            when code_commune between '75101' and '75120' then '75056'
            when code_commune between '69381' and '69389' then '69123'
            when code_commune between '13201' and '13216' then '13055'
            else code_commune
        end as code_insee_commune,
        code_departement,
        valeur_fonciere,
        surface_bati_m2,
        valeur_fonciere / nullif(surface_bati_m2, 0) as prix_m2
    from {{ source('bronze', 'ventes_locaux') }}
    where nature_mutation = 'Vente'
        and nb_locaux_habitation = 0
        and nb_dispositions = 1
        and valeur_fonciere > 0
)

select *
from ventes
-- Surfaces et prix hors du champ d'une antenne ou de bureaux : actes partiels, erreurs de saisie
where surface_bati_m2 between {{ var('locaux_surface_min_m2', 15) }} and {{ var('locaux_surface_max_m2', 5000) }}
    and prix_m2 between {{ var('locaux_prix_min_m2', 100) }} and {{ var('locaux_prix_max_m2', 15000) }}
