{#-
    Contrôle de la méthode des scénarios sur le passé : chaque éventail publiable
    à un semestre d'origine est confronté au prix **réellement** observé ensuite,
    la moyenne des 12 mois qui s'achèvent à l'horizon (même définition que le
    prix de référence, pour comparer des moyennes annuelles entre elles).

    L'éventail d'une origine n'utilise que des erreurs déjà connues à cette date :
    le contrôle se fait dans les conditions où la méthode aurait été employée.
-#}
select
    tranche,
    rang_origine,
    semestre_origine,
    horizon_annees,
    nb_erreurs_observees,
    prix_reference_eur_kwh,
    prix_bas_eur_kwh,
    prix_central_eur_kwh,
    prix_haut_eur_kwh,
    prix_realise_eur_kwh,
    case
        when prix_realise_eur_kwh < prix_bas_eur_kwh then 'en_dessous'
        when prix_realise_eur_kwh > prix_haut_eur_kwh then 'au_dessus'
        else 'dans_eventail'
    end as position
from {{ ref('fct_prix_entreprise_eventails') }}
where prix_bas_eur_kwh is not null
    and prix_realise_eur_kwh is not null
