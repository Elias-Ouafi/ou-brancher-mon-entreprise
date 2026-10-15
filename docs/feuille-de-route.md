# Feuille de route

> Mise à jour le 15 octobre 2026. Périmètre : une commande, pas d'application ; chaque
> indicateur avec sa limite. Pourquoi ce périmètre : [cadrage](cadrage-besoins-entreprises.md).

## Fait

| Domaine | Livré |
|---|---|
| Socle | Ingestion ODRÉ, RTE et Eurostat idempotente (MERGE, clés composites et optionnelles), UTC et changements d'heure, quota suivi ; médaillon DuckDB + dbt ; trois DAGs Airflow vérifiés en CI |
| Territoire | Référentiel commune → département → région ; pression industrielle (millésime sans secret statistique) ; tension régionale sur 12 mois |
| Réseau | Seuils de raccordement ; contraintes prospectives et postes RTE par département ; zone prioritaire RTE la plus proche (SDDR 2025) |
| Foncier et risques | Site clé en main France 2030 le plus proche ; risques Géorisques à l'adresse, avec repli communal |
| Prix | Coût horaire d'un profil type (HP/HC, CRE) ; prix Eurostat par tranche ; scénarios à 3 ans calibrés sur les erreurs passées, contrôle sur le passé affiché |
| Restitution | `diagnose` (texte, PDF, Markdown, JSON) ; `compare` (2 à 6 adresses, sans classement) ; limites affichées partout ; exemples réels dans `docs/exemples/` |

## À faire

| Priorité | Étape | Bloqué par |
|---|---|---|
| 1 | Protéger `main` : CI obligatoire, PR obligatoire | réglage GitHub, par le propriétaire du dépôt |
| 2 | Situer l'électro-intensité : facture estimée rapportée au chiffre d'affaires (`--ca`) | — |
| 3 | Importer la vraie courbe de charge (export Enedis) à la place du profil type | — |
| 4 | Lancer Airflow et l'exploiter 7 jours ; alerte en cas d'échec | Docker Desktop |
| 5 | DAG de rafraîchissement des référentiels (Eurostat, contraintes, postes, zones, sites) | 4 |
| 6 | Calendrier Tempo sur données réelles | compte RTE, par l'utilisateur |
| 7 | Repère de prix pour les consommateurs au-delà de 150 000 MWh (contrats CAPN) | décision |
| 8 | Classement pondéré dans `compare` | décision : poids à exposer et justifier |
| 9 | Vitrine : captures dans le README, démonstration de 2 minutes | — |

## Décisions

| Date | Décision |
|---|---|
| 9 oct. | Diagnostic de contexte et de risque par adresse, plutôt que « le prix et le CO₂ à l'adresse » |
| 10 oct. | Coût du profil sur l'option HP/HC, sans compte RTE |
| 10 oct. | `fct_diagnostic_territoire` à la maille du département ; la commune s'y joint à la lecture |
| 12 oct. | Prix fixes et historiques par tranche (Eurostat), pas de prix horaire ; horizon de 3 ans ; `--conso` saisi |
| 15 oct. | Fourchette des scénarios calibrée sur les erreurs passées de la méthode |
| 15 oct. | Contraintes et postes RTE au département : aucun n'est géolocalisé en open data |
| 15 oct. | Comparaison côte à côte, sans classement |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Intensité carbone régionale | Pas de `taux_co2` régional ni de facteur ADEME opposable ; une estimation reste possible si on l'étiquette comme telle (à décider) |
| Capacité de raccordement chiffrée | Absente de l'open data pour la consommation |
| Distance au poste RTE | Postes publiés sans coordonnées |
| Prix spot horaire | Hors besoin (décision du 12 oct.) |
| Mix régional au pas 15 min | 2,86 M lignes pour un apport faible au diagnostic |
| Migration BigQuery | `BigQueryLoader` à écrire derrière l'interface existante, sans intérêt tant que le local suffit |
| Site web, dashboard | Hors périmètre |
