-- Normalise un libellé pour comparaison : sans accent, sans casse, sans ponctuation
{% macro normalise_libelle(column) -%}
    regexp_replace(lower(strip_accents({{ column }})), '[^a-z0-9]', '', 'g')
{%- endmacro %}


-- « NOISY-LE-SEC » → « Noisy-le-Sec » : DuckDB n'a pas initcap, et les articles restent en minuscules
{% macro casse_titre(colonne) -%}
    {%- set titre -%}
    array_to_string(list_transform(string_split(lower({{ colonne }}), ' '), lambda mot:
        array_to_string(list_transform(string_split(mot, '-'), lambda m: upper(m[1]) || m[2:]), '-')
    ), ' ')
    {%- endset -%}
    {%- set ns = namespace(sql=titre) -%}
    {%- for article in ['Le', 'La', 'Les', 'De', 'Des', 'Du', 'Sur', 'Sous', 'En'] -%}
        {%- for gauche, droite in [('-', '-'), (' ', ' ')] -%}
            {%- set ns.sql = "replace(" ~ ns.sql ~ ", '" ~ gauche ~ article ~ droite ~ "', '"
                ~ gauche ~ article | lower ~ droite ~ "')" -%}
        {%- endfor -%}
    {%- endfor -%}
    {{ ns.sql }}
{%- endmacro %}
