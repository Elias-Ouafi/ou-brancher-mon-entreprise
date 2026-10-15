{#-
    Contraintes prospectives du réseau de transport rattachées à un département :
    une ligne par département et par contrainte dont au moins un poste
    (`poste_1` à `poste_3`) est situé dans le département.

    C'est la maille la plus fine possible : ni les ouvrages ni les postes ne sont
    géolocalisés dans les données ouvertes. Une liaison entre deux départements
    apparaît dans les deux.
-#}
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
