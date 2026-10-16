-- Pression industrielle locale : une ligne par commune ayant au moins un IRIS publié
with iris as (
    select * from {{ ref('conso_industrielle_iris') }}
),

reference as (
    {{ annee_reference_pression(ref('conso_industrielle_iris')) }}
)

select
    iris.annee,
    iris.code_insee_commune,
    min(iris.commune) as commune,
    min(iris.code_departement) as code_departement,
    min(iris.code_insee_region) as code_insee_region,
    coalesce(sum(iris.consommation_electricite_mwh), 0) as consommation_electricite_mwh,
    coalesce(sum(iris.nb_sites_electricite), 0)::integer as nb_sites_electricite,
    count(*)::integer as nb_iris,
    count(*) filter (where iris.est_secret)::integer as nb_iris_secret,
    bool_or(iris.est_secret) as est_minorant
from iris
inner join reference on reference.annee = iris.annee
group by iris.annee, iris.code_insee_commune
