-- Chaque période contrôlée tombe dans exactement une des trois positions.
select tranche
from {{ ref('fct_prix_entreprise_calibration') }}
where nb_periodes_controlees != nb_dans_eventail + nb_au_dessus + nb_en_dessous
