-- Plans de prévention en vigueur par commune : approuvés (ou appliqués par anticipation),
-- prescrits (ou prorogés) ; le dernier fichier de chaque type seulement
with source as (
    select * from {{ source('bronze', 'gaspar_plans') }}
),

dernier_export as (
    select *
    from source
    where millesime = (
        select max(millesime) from source as recent where recent.type_plan = source.type_plan
    )
)

select
    code_commune as code_insee_commune,
    code_procedure,
    any_value(type_plan) as type_plan,
    any_value(libelle_plan) as libelle_plan,
    -- Familles d'aléas du plan (« Inondation, Mouvement de terrain »), sans doublon
    string_agg(distinct libelle_risque, ', ' order by libelle_risque) as risques,
    case any_value(etat) when 'Opposable' then 'approuvé' else 'prescrit' end as etat,
    cast(nullif(any_value(date_prescription), '') as date) as date_prescription,
    cast(nullif(any_value(date_approbation), '') as date) as date_approbation,
    cast(any_value(millesime) as date) as millesime
from dernier_export
where etat in ('Opposable', 'Prescrit')
group by code_commune, code_procedure
