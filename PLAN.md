# Plan du projet

Périmètre : la France (métropole, Corse, outre-mer), à la demande, en local. Ce fichier porte
l'état, la suite et les décisions en vigueur ; le [README](README.md) dit comment s'en servir.
Une étape terminée quitte « Prochaines étapes » et devient une ligne de « État » (skill `cloturer-etape`).

## Persona

Le porteur d'un **projet de 1 à 50 MW** qui cherche où s'implanter : data center, hub de recharge,
site industriel ou logistique. Pour lui, l'électricité est le critère d'implantation :

- **Puissance et délai** : jusqu'à 20 MW, Enedis en HTA ; de 20 à 40 MW, zone grise Enedis ou RTE
  selon la saturation du poste source ; au-delà de 40 MW, RTE en HTB, 2 à 7 ans de délai (seed
  `seuils_raccordement`). En Corse et outre-mer, EDF SEI ou Électricité de Mayotte à toute
  puissance, jamais RTE. L'open data ne publie pas la capacité : d'où les zones prioritaires RTE.
- **Foncier** : sites clés en main, zonage d'urbanisme, friches, zones d'activité, prix des locaux.
- **Facture** : identique d'une ville à l'autre (péréquation), mais décisive pour la rentabilité ;
  1 MW constant fait environ 8 800 MWh par an. La part acheminement dépend du domaine de tension.
- **Carbone** : bas sur le continent ; décisif en Corse et outre-mer et pour les data centers.
- **Risques du site** : plans de prévention, sismicité, ICPE voisines, eau, desserte.

Exemples de référence : 12 MW et 70 000 MWh à Dunkerque (HTA, environ 5,3 M€ HTVA par an
dont 1,4 M€ d'acheminement) ; 45 MW et 300 000 MWh (HTB 1, 3,79 M€ d'acheminement).

## État

| Domaine | Ce qui existe | Où |
|---|---|---|
| Réponses | `essentiel` (1,5 s), `diagnose`, `compare` (2 à 6 adresses, sans classement), verdict en cinq critères ; adresse ambiguë ou approximative refusée | `ingestion/diagnostic/`, `ingestion/geocode.py`, `ingestion/comparaison.py` |
| Prix et facture | Tranches Eurostat, scénarios à 3 ans ; TURPE 7 HTA-BT et HTB 1 à 3 selon domaine, puissance et courbe de charge ; accise normale et réduite selon l'activité ; parts du CA et de la valeur ajoutée | `fct_prix_entreprise_tranche`, `ingestion/turpe.py`, seeds `turpe_*`, `accise_electricite` |
| Carbone | Facteur ADEME par système électrique ; profil horaire des îles (EDF SEI) | `fct_carbone_territoire`, `fct_carbone_horaire_zni` |
| Raccordement | Gestionnaire et domaine de tension selon la puissance, zones RTE, qualité de fourniture du département, pièces du dossier RTE | seeds `seuils_raccordement`, `gestionnaires_zni`, `fct_continuite_departement` |
| Risques | GASPAR, plans de prévention, zonage sismique (WFS), voisinage Géorisques (argiles, cavités, Seveso, ICPE, sols pollués) | `fct_risques_commune`, `ingestion/georisques.py` |
| Foncier et site | Urbanisme, friches, zones d'activité, réseaux de chaleur, prix des locaux (DVF), bâtiment et solaire, obligations de solarisation, vue aérienne datée, accès routier | `ingestion/urbanisme.py`, `solaire.py`, `image_site.py`, `acces_routier.py`, `extract_*.py` |
| Eau et main-d'œuvre | Arrêtés VigiEau, zones de répartition des eaux ; diplômés dans l'isochrone de 45 min, recrutements techniques | `ingestion/eau.py`, `main_oeuvre.py` |
| Entreprises voisines | Consommation Enedis par commune et à l'adresse, profils de charge réels (> 250 kVA) | `fct_conso_entreprises_*`, `profils_charge_reels` |
| Données | Médaillon bronze Parquet + `MERGE`, silver et gold dbt, étoile (5 dimensions, 8 faits) ; `refresh` charge tout | `ingestion/`, `dbt/` |
| Carte 3D | Trois vues (Raccorder, Terrain, Territoire), monde en tuiles, relief continu, curseur horaire des îles, comparaison épinglée, 60 images/s, ambiance sonore, bouton « Image » | `reporting/carte.py`, `carte.html`, `ingestion/hexagones.py` |
| Application | Fenêtre d'accueil, une ou deux adresses, courbe de charge, rapport PDF, Markdown ou JSON | `reporting/app.py` |
| PDF et charte | PDF court (3 pages) et visuel ; charte commune (papier clair, vert, Spectral et Inter) | `reporting/implantation_pdf.py`, `charte.py` |
| Démonstration | `demo` construit une base synthétique sans réseau | `ingestion/demo.py` |
| Qualité | 964 tests sans réseau, sept parcours Chromium, couverture 94,7 % (plancher 92,5 %), sorties de référence figées, pannes des sources jouées, CI Ubuntu et Windows | `ingestion/tests/`, `.claude/scripts/verifier.py` |

### À surveiller

- **API Carto (urbanisme)** : 404 depuis le 12 novembre 2026, cause inconnue. Le diagnostic dit
  « zonage d'urbanisme non consulté » et le critère foncier reste « pour information ».
- **Géorisques** : le rapport à l'adresse est instable ; repli sur GASPAR et le zonage WFS.
- **Fourchette de prix à 3 ans** : indicative tant que moins de 20 périodes sont contrôlées (8 aujourd'hui).
- **EDF SEI** : données jusqu'à 2024, sans Mayotte ; facteurs ADEME de décembre 2020.
- **DVF 2026** : pas encore publié.
- **Solarisation** : toiture des bâtiments existants (2028) en attente de décret ; Mayotte sans seuil de parking.
- **Rendu 3D** : vérifié à la main (skill `essai-carte`), pas par les tests.

## Prochaines étapes

Rien à coder maintenant. Dans l'ordre :

1. Clore la branche du relief continu et du curseur horaire (PR vers `main`).
2. **1er janvier 2027, étape 19** : capacité d'accueil en soutirage de RTE (délibération CRE
   2026-173), à intégrer dès publication ; la capacité d'Enedis en HTA reste réservée à son portail.
3. **1er janvier et 1er février 2027** : tarifs d'accise de la loi de finances 2027, à ajouter au seed `accise_electricite`.
4. **1er août 2027** : nouvelle grille TURPE 7, à reporter dans les seeds `turpe_*` (les sorties le signalent).
5. **Avril et octobre** : actualisation de DVF, puis `refresh`.
6. **Chaque printemps** : nouveaux millésimes Diplômes-Formation (Insee) et BMO (France Travail) dans `ingestion/config.py`.
7. **Chaque mois** : `ecowatt`, pour garder l'historique du signal, qui n'est pas rejouable.
8. **Étape 6, extension internationale** (en attente d'un jeton ENTSO-E et des contours des zones
   de prix) : zones de prix (Italie, pays nordiques), Royaume-Uni, États-Unis. Pour l'Europe de
   l'Ouest, l'ordre de facilité des données : Pays-Bas, Belgique, Espagne, Autriche, Luxembourg,
   Suisse, Portugal, Allemagne, Royaume-Uni, Irlande, Italie.

Hors de l'application, à dessein : `europe`, `refresh` (un seul writer DuckDB), `tempo`, `ecowatt`,
`report` (comptes RTE), `demo`.

**Limites gardées**, faute de source : composante d'injection du TURPE et alimentation de secours ;
tarif d'accise des secteurs exposés à la concurrence internationale ; échangeur autoroutier et
profil poids lourd ; structure des toitures et surface des parkings ; niveau de nappe face à un étiage.

## Décisions en vigueur

**Périmètre**
- Local et à la demande : aucune exécution planifiée, aucun hébergement.
- Sources : producteur officiel, licence ouverte, méthode publiée. Tout chiffre a sa source et sa limite ;
  « non disponible » plutôt qu'un chiffre inventé.
- Comparaison sans classement. Adresse refusée plutôt que devinée (score BAN sous 0,65, deux communes
  trop proches, code postal absent).
- Projet incohérent refusé : consommation supérieure à la puissance × 8 784 h.

**Données**
- Bronze : un Parquet n'est jamais écrasé (suffixe `-2`, `-3`…), puis `MERGE` DuckDB. Seeds recréés à chaque `dbt build`.
- Chaque sortie donne la date de chargement de ses sources ; au-delà de 45 jours, elle demande un `refresh`.
- Sources à la demande (urbanisme, eau, voisinage, solaire, itinéraires) : jamais stockées ; une panne
  est dite dans sa section et ne change aucun critère du verdict.
- Distance à un poste RTE ou EDF exclue : coordonnées retirées en 2026 pour sécurité publique.

**Règles métier**
- Carbone : verdict sur le facteur ADEME en cycle de vie, seuil de 100 gCO₂e/kWh (taxonomie).
- Risques « à vérifier » seulement sous un plan de prévention approuvé ou prescrit ; sinon « pour information ».
- Prix : tranches Eurostat historiques, pas de prix horaire ; l'acheminement est une part déjà comprise dans le prix moyen.
- Courbe de charge : lue à la demande, jamais stockée ; coût HP/HC seulement jusqu'à 36 kW.
- TURPE HTB dès 40 MW, HTB 1 par défaut, calendrier propre (délibération CRE 2025-77).
- Accise : tarif normal au seed daté (26,35 €/MWh depuis le 1er août 2026) ; tarif réduit sous conditions que l'outil dit sans les vérifier.
- Prix des locaux : ventes DVF seulement, aucun loyer (aucune source ouverte).
- Corse et outre-mer : gestionnaire insulaire à toute puissance ; seuils de 20 et 40 MW dits indicatifs.
- UE-27 : moyennes nationales seulement ; hors UE refusé.

**Carte, application et PDF**
- Carte : page HTML autonome, three.js embarqué, grille hexagonale maison, fausses couleurs réservées à « Raccorder ».
- Charte unique dans `reporting/charte.py` ; ni Marianne ni identité de l'État.
- Application : `http.server` standard sur 127.0.0.1, deux adresses au plus.
- PDF court : la limite qui pèse sur chaque critère et un conseil ; toutes les limites restent dans les sorties texte, Markdown et JSON.

**Code et tests**
- Code le plus simple possible ; commentaires sur la logique métier seulement (pourquoi on a besoin de la donnée).
- Une étape n'est validée qu'après `verifier.py` (ruff, pytest, essai réel).
- Tests sans réseau ; `main` protégée (PR obligatoire, CI verte sur branche à jour).
- Plancher de couverture relevé à la main, jamais abaissé.

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse », carbone régional en métropole | Péréquation ; un seul réseau continental |
| Capacité de raccordement chiffrée, distance aux postes | Absentes de l'open data |
| Loyer des locaux d'activité | Aucune source officielle et ouverte |
| Classement pondéré des sites | Contraire à la comparaison sans classement |
| Photovoltaïque détecté, température de surface | Sans producteur officiel ou sans méthode sourcée |
| Zonage sismique wpd | Producteur non officiel |
| Ports fluviaux parmi les pôles de fret | Pas de maille port dans les données |
| Spark pour silver et gold | dbt-duckdb suffit à ce volume |
| Exécution planifiée, Airflow, hébergement | Hors périmètre |
