# `dbt/` — couches silver et gold

Projet **dbt-duckdb** qui construit silver et gold à partir des tables `bronze` chargées par
`ingestion`. Aucun package externe : il se construit hors ligne.

```bash
uv run dbt build --project-dir dbt --profiles-dir dbt          # depuis la racine du dépôt
uv run dbt docs generate --project-dir dbt --profiles-dir dbt  # catalogue et lignage
```

Base visée : `ECO2MIX_DUCKDB_PATH` (défaut `data/warehouse/eco2mix.duckdb`).

## Modèles

| Domaine | Silver | Gold |
|---|---|---|
| Mix national | `national_tr`, `national_cons_def`, `mix_unifie` | `fct_mix`, `fct_production_filiere`, `fct_mix_journalier` |
| Tempo et créneaux | `tempo_jours` | `fct_creneau_horaire` (base du rapport mensuel) |
| Territoire | `conso_industrielle_iris`, `equilibre_regional_mensuel`, `contraintes_reseau_region` | `fct_pression_industrielle_commune`, `_departement`, `fct_tension_reseau_region`, **`fct_diagnostic_territoire`** (une ligne par département) |
| Réseau de transport | `postes_rte`, `contraintes_ouvrages` | `fct_postes_rte_departement`, `fct_contraintes_departement` |
| Carbone par territoire | — | `fct_carbone_territoire` (une ligne par région : facteur ADEME du système électrique, verdict « décarboné », intensité RTE sur 12 mois pour le continent) |
| Coût d'un profil | — | `fct_cout_horaire_profil`, `fct_cout_mensuel_profil` |
| Prix des entreprises | `prix_entreprises` | `fct_prix_entreprise_eventails` → `_scenarios`, `_backtest`, `_calibration` → `fct_prix_entreprise_tranche` |

**Seeds** (schéma gold) : référentiels `dim_region`, `dim_departement`, `dim_filiere` ;
tarifs `tarifs_tempo`, `tarifs_hphc` ; `profils_consommation`, `tranches_eurostat`,
`seuils_raccordement`, `zones_raccordement_rte` (SDDR 2025), `sites_cles_en_main` (ANCT), `facteurs_carbone_territoire` (ADEME) ;
`limites_methodologiques` (une limite par indicateur). Les seeds datés sont à mettre à jour à
chaque nouvelle source : grilles tarifaires (février et août), SDDR, liste des sites, facteurs ADEME (annuels).

## Décisions de modélisation

- **Consolidé puis temps réel, sans chevauchement** : le temps réel ne démarre qu'après la
  dernière mesure consolidée (test `assert_mix_sans_chevauchement`).
- **Énergie = puissance × durée** : juste avec un pas variable et les jours de 23 ou 25 h.
- **Jours en heure de Paris, session en UTC** : conversions explicites.
- **Jointures par code INSEE, jamais par libellé** : les rares codes retrouvés depuis un nom
  passent par la macro `normalise_libelle`, avec un test `not_null` contre toute perte
  silencieuse ; `assert_hierarchie_territoriale_conforme` vérifie le référentiel.
- **Millésime sans secret statistique** (macro `annee_reference_pression`) : depuis 2022,
  ~40 % des IRIS sont masqués.
- **Scénarios de prix sans regard vers le futur** : chaque éventail n'utilise que les données
  connues à son semestre d'origine, ce qui rend le contrôle sur le passé honnête. Seuils en
  variables : `prix_nb_variations_min` (4), `prix_nb_erreurs_min` (10).
- **Verdict carbone en cycle de vie** : le facteur ADEME (combustion, amont, réseau) est
  comparé au seuil de la taxonomie, lui aussi en cycle de vie (variable
  `seuil_decarbone_g_co2e_kwh`, 100). Le `taux_co2` de RTE, qui ne compte que les émissions
  directes, est affiché à côté mais ne décide pas.
- **Reconstruction complète** à chaque run : quelques secondes à ce volume.

## Tests

Génériques et maison (`accepted_range`, `unique_combination`, `exact_length` dans
`tests/generic/`), plus quatre tests singuliers. Les bornes physiques sont en `severity: warn`.
[`ingestion/tests/test_dbt.py`](../ingestion/tests/test_dbt.py) et les tests du diagnostic
jouent `dbt build` de bout en bout sur une base synthétique.
