-- Diagnostic de contexte d'implantation : une ligne par département
select
    pression.code_departement,
    pression.departement,
    tension.code_insee_region,
    tension.region,
    tension.est_zni,

    -- Pression industrielle du département (dernière année sans secret statistique)
    pression.annee as annee_pression,
    pression.consommation_electricite_mwh / 1000 as conso_industrielle_departement_gwh,
    pression.nb_sites_electricite as nb_sites_departement,
    pression.nb_communes_avec_site,
    pression.est_minorant as conso_departement_est_minorant,
    pression.rang_national as rang_pression_departement,
    pression.nb_departements_classes,

    -- Tension du réseau régional (12 mois glissants + contraintes RTE)
    tension.mois_debut as equilibre_mois_debut,
    tension.mois_fin as equilibre_mois_fin,
    tension.nb_mois as equilibre_nb_mois,
    tension.production_gwh as production_region_gwh,
    tension.consommation_gwh as consommation_region_gwh,
    tension.solde_gwh as solde_region_gwh,
    tension.taux_couverture as taux_couverture_region,
    tension.statut_equilibre,
    tension.rang_dependance as rang_dependance_region,
    tension.nb_regions_classees,
    tension.puissance_enr_installee_mw,
    tension.puissance_a_compenser_mw,
    tension.energie_non_evacuee_mwh,

    -- Réseau de transport dans le département (postes RTE, contraintes prospectives)
    postes.nb_postes as nb_postes_rte,
    postes.nb_postes_htb1,
    postes.nb_postes_htb2,
    postes.nb_postes_htb3,
    postes.nb_postes_en_projet,
    coalesce(contraintes.nb_contraintes, 0) as nb_contraintes_departement,
    contraintes.nb_contraintes_frequentes as nb_contraintes_frequentes_departement
from {{ ref('fct_pression_industrielle_departement') }} as pression
inner join {{ ref('fct_tension_reseau_region') }} as tension
    on tension.code_insee_region = pression.code_insee_region
left join {{ ref('fct_postes_rte_departement') }} as postes
    on postes.code_departement = pression.code_departement
left join (
    select
        code_departement,
        count(*) as nb_contraintes,
        -- « Forte » ou « très forte » : plus de 75 fois par an
        count(*) filter (where rang_occurrence >= 4) as nb_contraintes_frequentes
    from {{ ref('fct_contraintes_departement') }}
    group by code_departement
) as contraintes
    on contraintes.code_departement = pression.code_departement
