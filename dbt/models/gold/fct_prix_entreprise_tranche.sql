{#-
    Prix de l'électricité d'une entreprise selon sa tranche de consommation
    annuelle : une ligne par tranche, tout ce que le diagnostic affiche.

    * **Prix de référence** : moyenne des deux derniers semestres publiés (les
      12 derniers mois), hors TVA et taxes récupérables.
    * **Repères historiques** : moyenne des 5 dernières années (10 semestres),
      plus bas et plus haut depuis 2007 avec leur semestre.
    * **Prix moyen des 3 prochaines années**, par scénario : moyenne des prix à
      1, 2 et 3 ans de `fct_prix_entreprise_scenarios`. Ce n'est pas une prévision.
    * **Contrôle sur le passé** : combien de fois la même méthode, appliquée à
      chaque semestre de l'histoire, a encadré le prix moyen réel des 3 années
      suivantes (`fct_prix_entreprise_calibration`).

    Le prix est national (péréquation) : il ne dépend pas de l'adresse.
-#}
with prix as (
    select
        *,
        max(rang_semestre) over (partition by tranche) as dernier_rang
    from {{ ref('prix_entreprises') }}
),

historique as (
    select
        tranche,
        min(semestre) as premier_semestre,
        max(semestre) as dernier_semestre,
        max(prix_htva_eur_kwh) filter (where rang_semestre = dernier_rang)
            as prix_dernier_semestre_eur_kwh,
        avg(prix_htva_eur_kwh) filter (where rang_semestre >= dernier_rang - 1)
            as prix_reference_eur_kwh,
        avg(taxes_non_recuperables_eur_kwh) filter (where rang_semestre >= dernier_rang - 1)
            as taxes_reference_eur_kwh,
        avg(prix_htva_eur_kwh) filter (where rang_semestre >= dernier_rang - 9)
            as prix_moyen_5_ans_eur_kwh,
        min(prix_htva_eur_kwh) as prix_min_eur_kwh,
        arg_min(semestre, prix_htva_eur_kwh) as semestre_min,
        max(prix_htva_eur_kwh) as prix_max_eur_kwh,
        arg_max(semestre, prix_htva_eur_kwh) as semestre_max
    from prix
    group by tranche
),

-- Un horizon sans éventail publiable (pas assez d'erreurs passées pour le
-- calibrer) : les scénarios restent nuls.
trois_ans as (
    select
        tranche,
        avg(prix_bas_eur_kwh) as prix_3_ans_bas_eur_kwh,
        avg(prix_central_eur_kwh) as prix_3_ans_central_eur_kwh,
        avg(prix_haut_eur_kwh) as prix_3_ans_haut_eur_kwh,
        min(nb_erreurs_observees) as nb_erreurs_observees
    from {{ ref('fct_prix_entreprise_scenarios') }}
    group by tranche
    having count(prix_bas_eur_kwh) = 3
)

select
    tranches.tranche,
    tranches.code as code_tranche,
    tranches.libelle as libelle_tranche,
    tranches.conso_min_mwh,
    tranches.conso_max_mwh,
    historique.premier_semestre,
    historique.dernier_semestre,
    historique.prix_dernier_semestre_eur_kwh,
    historique.prix_reference_eur_kwh,
    historique.taxes_reference_eur_kwh,
    historique.prix_moyen_5_ans_eur_kwh,
    historique.prix_min_eur_kwh,
    historique.semestre_min,
    historique.prix_max_eur_kwh,
    historique.semestre_max,
    trois_ans.prix_3_ans_bas_eur_kwh,
    trois_ans.prix_3_ans_central_eur_kwh,
    trois_ans.prix_3_ans_haut_eur_kwh,
    trois_ans.nb_erreurs_observees,
    coalesce(calibration.nb_periodes_controlees, 0) as nb_periodes_controlees,
    calibration.nb_dans_eventail,
    calibration.nb_au_dessus,
    calibration.nb_en_dessous,
    calibration.premiere_origine_controlee,
    calibration.derniere_origine_controlee,
    calibration.ecart_median_au_central
from {{ ref('tranches_eurostat') }} as tranches
inner join historique on historique.tranche = tranches.tranche
left join trois_ans on trois_ans.tranche = tranches.tranche
left join {{ ref('fct_prix_entreprise_calibration') }} as calibration
    on calibration.tranche = tranches.tranche
