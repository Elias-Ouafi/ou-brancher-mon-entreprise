-- Grain : une commune ; le repli sur le département reste dans fct_prix_locaux
select
    code_zone as code_insee_commune,
    derniere_vente as jour_derniere_vente,
    nb_ventes,
    fiable,
    prix_m2_median,
    prix_m2_q1,
    prix_m2_q3
from {{ ref('fct_prix_locaux') }}
where niveau = 'commune'
