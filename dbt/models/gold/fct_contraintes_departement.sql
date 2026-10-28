with postes_contraintes as (
    select ouvrage, poste_1, poste_2, poste_3, unnest([poste_1, poste_2, poste_3]) as code_poste
    from {{ ref('contraintes_ouvrages') }}
),

rattachements as (
    select distinct
        postes.code_departement,
        postes_contraintes.ouvrage,
        postes_contraintes.poste_1,
        postes_contraintes.poste_2,
        postes_contraintes.poste_3
    from postes_contraintes
    inner join {{ ref('postes_rte') }} as postes
        on postes.code_poste = postes_contraintes.code_poste
    where postes.code_departement is not null
)

select
    rattachements.code_departement,
    contraintes.*
from rattachements
inner join {{ ref('contraintes_ouvrages') }} as contraintes
    on contraintes.ouvrage = rattachements.ouvrage
    and contraintes.poste_1 = rattachements.poste_1
    and contraintes.poste_2 is not distinct from rattachements.poste_2
    and contraintes.poste_3 is not distinct from rattachements.poste_3
