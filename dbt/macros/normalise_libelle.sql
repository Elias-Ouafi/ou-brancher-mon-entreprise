-- Normalise un libellé pour comparaison : sans accent, sans casse, sans ponctuation
{% macro normalise_libelle(column) -%}
    regexp_replace(lower(strip_accents({{ column }})), '[^a-z0-9]', '', 'g')
{%- endmacro %}
