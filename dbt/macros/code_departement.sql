-- Code département d'un code commune INSEE : 3 caractères outre-mer (971…976), 2 ailleurs
{% macro code_departement(code_commune) -%}
    case
        when left({{ code_commune }}, 2) = '97' then left({{ code_commune }}, 3)
        else left({{ code_commune }}, 2)
    end
{%- endmacro %}
