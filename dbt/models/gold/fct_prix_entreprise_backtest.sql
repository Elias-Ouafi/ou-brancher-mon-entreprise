select
    tranche,
    rang_origine,
    semestre_origine,
    horizon_annees,
    nb_erreurs_observees,
    prix_reference_eur_kwh,
    prix_bas_eur_kwh,
    prix_central_eur_kwh,
    prix_haut_eur_kwh,
    prix_realise_eur_kwh,
    case
        when prix_realise_eur_kwh < prix_bas_eur_kwh then 'en_dessous'
        when prix_realise_eur_kwh > prix_haut_eur_kwh then 'au_dessus'
        else 'dans_eventail'
    end as position
from {{ ref('fct_prix_entreprise_eventails') }}
where prix_bas_eur_kwh is not null
    and prix_realise_eur_kwh is not null
