-- Grain : un réseau dans un département ; un réseau qui en traverse plusieurs les nomme tous
with reseaux as (
    select distinct
        reseau_id,
        nom_reseau,
        departements,
        a_trace,
        classe,
        taux_enr_pct,
        contenu_co2_kg_kwh,
        livraisons_mwh
    from {{ ref('reseaux_chaleur') }}
),

par_departement as (
    select
        reseaux.*,
        trim(unnest(string_split(departements, ','))) as departement
    from reseaux
)

select
    par_departement.reseau_id,
    departements.code_departement,
    par_departement.nom_reseau,
    par_departement.a_trace,
    par_departement.classe,
    par_departement.taux_enr_pct,
    par_departement.contenu_co2_kg_kwh,
    par_departement.livraisons_mwh
from par_departement
left join {{ ref('dim_departement_region') }} as departements
    on departements.departement = par_departement.departement
