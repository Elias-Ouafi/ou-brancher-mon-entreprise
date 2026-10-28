select
    departement.code_departement,
    count(postes.code_poste) filter (where postes.est_en_exploitation) as nb_postes,
    count(postes.code_poste) filter (
        where postes.est_en_exploitation and postes.domaine_tension = 'HTB1'
    ) as nb_postes_htb1,
    count(postes.code_poste) filter (
        where postes.est_en_exploitation and postes.domaine_tension = 'HTB2'
    ) as nb_postes_htb2,
    count(postes.code_poste) filter (
        where postes.est_en_exploitation and postes.domaine_tension = 'HTB3'
    ) as nb_postes_htb3,
    count(postes.code_poste) filter (where not postes.est_en_exploitation)
        as nb_postes_en_projet
from {{ ref('dim_departement') }} as departement
left join {{ ref('postes_rte') }} as postes
    on postes.code_departement = departement.code_departement
group by departement.code_departement
