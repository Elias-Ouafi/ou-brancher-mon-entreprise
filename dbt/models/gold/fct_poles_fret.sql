-- Grands pôles de fret : ports et aéroports au-delà d'un tonnage, chantiers rail-route au-delà
-- d'une longueur de voies de manutention (aucun tonnage publié)
{%- set seuil_port_t = var('fret_seuil_port_t', 5000000) %}
{%- set seuil_port_zni_t = var('fret_seuil_port_zni_t', 500000) %}
{%- set seuil_aeroport_t = var('fret_seuil_aeroport_t', 20000) %}
{%- set seuil_aeroport_zni_t = var('fret_seuil_aeroport_zni_t', 3000) %}
{%- set seuil_rail_m = var('fret_seuil_rail_m', 2000) %}

with ports as (
    select
        'port' as mode,
        dim.port as nom,
        dim.code_insee_commune,
        cast(p.annee as integer) as annee,
        p.marchandises_t as volume_t,
        p.conteneurs_evp,
        cast(null as double) as vols_cargo,
        cast(null as double) as longueur_voies_m,
        cast(null as varchar) as manutention
    from {{ source('bronze', 'ports_fret') }} as p
    inner join {{ ref('dim_ports_fret') }} as dim
        on dim.locode = p.locode
    -- Dernière année publiée : un port qui n'y figure plus n'est plus un pôle
    where p.annee = (select max(annee) from {{ source('bronze', 'ports_fret') }})
),

-- Dernière année complète : l'archive DGAC peut s'ouvrir sur une année en cours
annee_aeroports as (
    select max(annee) as annee
    from {{ source('bronze', 'aeroports_fret') }}
    where nb_mois = 12
),

aeroports as (
    select
        'aeroport' as mode,
        dim.aeroport as nom,
        dim.code_insee_commune,
        cast(a.annee as integer) as annee,
        a.fret_t as volume_t,
        cast(null as double) as conteneurs_evp,
        a.vols_cargo,
        cast(null as double) as longueur_voies_m,
        cast(null as varchar) as manutention
    from {{ source('bronze', 'aeroports_fret') }} as a
    inner join {{ ref('dim_aeroports_fret') }} as dim
        on dim.code_oaci = a.code_oaci
    where a.annee = (select annee from annee_aeroports)
),

poles_communes as (
    select
        p.*,
        c.nom as commune,
        {{ code_departement('p.code_insee_commune') }} as code_departement,
        c.latitude,
        c.longitude
    from (select * from ports union all select * from aeroports) as p
    inner join {{ source('bronze', 'communes_centres') }} as c
        on c.code_commune = p.code_insee_commune
),

-- Chantiers placés à leurs propres coordonnées ; département tiré du code postal
chantiers as (
    select
        'rail' as mode,
        {{ casse_titre("chantier") }} as nom,
        cast(null as varchar) as code_insee_commune,
        cast(null as integer) as annee,
        cast(null as double) as volume_t,
        cast(null as double) as conteneurs_evp,
        cast(null as double) as vols_cargo,
        longueur_m as longueur_voies_m,
        manutention,
        -- Ville publiée sans accents (« BEGLES ») : le nom du chantier suffit
        cast(null as varchar) as commune,
        {{ code_departement('code_postal') }} as code_departement,
        latitude,
        longitude
    from {{ source('bronze', 'chantiers_rail_route') }}
    where longueur_m >= {{ seuil_rail_m }}
)

select
    p.mode,
    p.nom,
    p.code_insee_commune,
    p.commune,
    p.code_departement,
    p.annee,
    round(p.volume_t) as volume_t,
    round(p.conteneurs_evp) as conteneurs_evp,
    round(p.vols_cargo) as vols_cargo,
    round(p.longueur_voies_m) as longueur_voies_m,
    p.manutention,
    p.latitude,
    p.longitude
from (select * from poles_communes union all select * from chantiers) as p
-- Seuls les départements des cartes : les collectivités d'outre-mer n'y figurent pas
inner join {{ ref('fct_diagnostic_territoire') }} as territoire
    on territoire.code_departement = p.code_departement
where
    p.mode = 'rail'
    or p.volume_t >= case
        when p.mode = 'port' and territoire.est_zni then {{ seuil_port_zni_t }}
        when p.mode = 'port' then {{ seuil_port_t }}
        when territoire.est_zni then {{ seuil_aeroport_zni_t }}
        else {{ seuil_aeroport_t }}
    end
order by p.mode, p.volume_t desc nulls last, p.longueur_voies_m desc
