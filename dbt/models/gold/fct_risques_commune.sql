-- Risques recensés par commune, dernier export GASPAR seulement
with source as (
    select * from {{ source('bronze', 'gaspar_risques') }}
)

select
    code_commune as code_insee_commune,
    libelle_commune,
    num_risque,
    libelle_risque,
    -- Nomenclature GASPAR : 1x naturels ; 2x technologiques et 3x miniers, comme Géorisques
    case when num_risque like '1%' then 'naturel' else 'technologique' end as famille,
    cast(millesime as date) as millesime
from source
where millesime = (select max(millesime) from source)
    -- « Autre risque » ne dit rien du site
    and num_risque <> 'AUTRE'
