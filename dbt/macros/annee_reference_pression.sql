-- Millésime de référence de la pression industrielle
{% macro annee_reference_pression(iris_relation) -%}
    select coalesce(
        max(annee) filter (where not a_du_secret),
        max(annee)
    ) as annee
    from (
        select annee, bool_or(est_secret) as a_du_secret
        from {{ iris_relation }}
        group by annee
    )
{%- endmacro %}
