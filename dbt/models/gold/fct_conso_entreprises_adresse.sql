-- Entreprises déjà raccordées à une adresse, dernière année publiée : signe d'un raccordement existant
with derniere as (
    select max(annee) as annee from {{ ref('conso_entreprises_adresse') }}
),

lignes as (
    select adresses.*
    from {{ ref('conso_entreprises_adresse') }} as adresses
    inner join derniere on derniere.annee = adresses.annee
    where adresses.adresse_normalisee is not null
)

select
    lignes.annee,
    lignes.code_insee_commune,
    lignes.adresse_normalisee,
    min(lignes.adresse) as adresse,
    sum(lignes.nb_sites)::integer as nb_sites,
    sum(lignes.consommation_mwh) as consommation_mwh,
    -- Secteur qui pèse le plus à l'adresse, quand plusieurs activités s'y partagent le compteur
    arg_max(lignes.grand_secteur, lignes.consommation_mwh) as grand_secteur,
    arg_max(lignes.code_naf2, lignes.consommation_mwh) as code_naf2,
    count(*)::integer as nb_lignes
from lignes
group by lignes.annee, lignes.code_insee_commune, lignes.adresse_normalisee
