# Plan du projet

> Mis à jour le 24 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en ligne de
> commande, en local et à la demande.

## État

Le MVP est atteint depuis le 16 octobre. `essentiel` donne en 1,5 s environ le prix moyen et le
verdict carbone ; il répond juste sur 11 adresses de recette, de Paris à Mamoudzou (test
`test_recette_mvp.py`).

| Brique | Où |
|---|---|
| Ingestion PySpark (typage, UTC, clés du lot), écriture Arrow vers le bronze | `ingestion/transform.py`, `ingestion/spark.py` |
| Adresse → commune, département, région ; adresse ambiguë ou approximative refusée | `ingestion/geocode.py` (BAN) |
| Prix par tranche Eurostat, scénarios à 3 ans calibrés sur les erreurs passées | `fct_prix_entreprise_tranche` |
| Carbone par système électrique, seuil de 100 gCO₂e/kWh | `fct_carbone_territoire` |
| Carbone heure par heure en Corse et outre-mer (production EDF SEI, profil calé sur l'ADEME) | `fct_carbone_horaire_zni` |
| Part de la facture dans le chiffre d'affaires (`--ca`) et électro-intensité fiscale dans la valeur ajoutée (`--va`) | `essentiel`, `diagnose` |
| Courbe de charge Enedis (`--courbe`) : consommation, pointe, heures creuses, coût et carbone réels | `ingestion/courbe.py` |
| Diagnostic complet : industrie, réseau, zone RTE, site clé en main, risques | `diagnose`, `compare` |
| Verdict « brancher ici ? » en cinq critères (raccordement, risques, carbone, facture, foncier) | `verdict`, `phrase_verdict` |
| Carte 3D épurée autour du verdict : vue « Raccorder » par défaut, adresse reliée à la zone RTE utile et au site clé en main ; épinglée par `essentiel` et `diagnose` (seule l'épingle bouge si les données n'ont pas changé) | `carte`, `reporting/carte.py` |
| Risques sans dépendre de l'API Géorisques : export GASPAR chargé par `refresh` | `fct_risques_commune` |
| Date de chargement de chaque source dans toutes les sorties ; pied de page des rapports avec la commande pour les refaire | `ligne_chargements`, `pied_de_page` |
| Pays de l'UE-27 : prix Eurostat et carbone Ember du pays, face à la France à tranche égale ; pays trouvé par Nominatim | `europe`, `fct_pays_ue` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |

**À surveiller** :
- l'API Géorisques coupe toute connexion vers `/api/…` depuis le 24 octobre au moins (la page
  d'accueil répond) ; le diagnostic passe alors sur l'export GASPAR, à la maille commune, sans le
  zonage sismique (aucun fichier national officiel à jour) ;
- les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 (Base Carbone fondue
  dans la Base Empreinte : vérifier la version) ;
- la fourchette de prix à 3 ans n'est pas validée : le contrôle sur le passé ne compte que 8
  périodes chevauchantes, toutes marquées par la crise de 2022 ;
- le calendrier Tempo (`part-manual`, 31 jours) n'a tourné que sur des réponses simulées ;
- l'accise au tarif normal (26,58 €/MWh) est une constante de `diagnostic.py` : à revoir à chaque
  indexation du 1er février ;
- EDF SEI publie jusqu'à fin 2024 seulement (2021-2024 « estimé »), sans Mayotte ; le jeu a
  quitté `opendata-corse-outremer.edf.fr` pour `opendata.edf.fr` (API data-fair).

## Suite

| Priorité | Étape | Bloqué par |
|---|---|---|
| 1 | Protéger `main` : CI et PR obligatoires | réglage GitHub, par le propriétaire |
| 6 | Extension internationale, voir ci-dessous | contours des zones de prix à sourcer |
| 7 | Calendrier Tempo sur données réelles | compte RTE |
| 8 | Repère de prix au-delà de 150 000 MWh/an (contrats CAPN) ; classement pondéré dans `compare` | décision |

**Extension internationale**, dans cet ordre (l'UE-27 à la maille pays est faite) :
1. Zones de prix (Italie, pays nordiques) : ENTSO-E et point-dans-polygone avec l'extension
   `spatial` de DuckDB.
2. Royaume-Uni (intensité régionale officielle) et États-Unis (EIA, eGRID), où prix et carbone
   dépendent vraiment de l'adresse.

## Ce que les études ont établi (9 et 15 octobre)

- **Les besoins des entreprises**, dans l'ordre : de la puissance disponible à une date connue
  (premier critère des gros projets ; zones RTE P1 dès fin 2028, P2 dès 2029) ; du foncier
  viabilisé ; une électricité bas-carbone ; un prix prévisible, qui ne décide que pour les
  électro-intensifs et les data centers ; la connaissance des risques du site.
- **En France, ni le prix ni le carbone ne dépendent de l'adresse** : d'où un diagnostic de
  contexte par adresse plutôt qu'un « prix à l'adresse ».
- **Limites de l'open data** : capacité de raccordement non publiée ; postes RTE sans
  coordonnées (maille du département seulement) ; ~40 % des IRIS sous secret statistique
  depuis 2022.
- **Ce qui peut bouger (étude du 24 octobre)** : la CRE impose à RTE de refondre Caparéseau
  d'ici le 1er janvier 2027 et RTE étudie une carte de ses capacités en soutirage (délibération
  CRE 2026-173) : à intégrer dès sa publication. Enedis publie ses postes HTA/BT géolocalisés
  (licence ouverte) ; un jeu des postes sources géolocalisés reste à trouver.

## Décisions en vigueur

| Date | Décision |
|---|---|
| 12 oct. | Prix historiques par tranche Eurostat, pas de prix horaire ; scénarios à 3 ans |
| 15 oct. | Comparaison sans classement |
| 16 oct. | MVP recentré sur deux réponses, prix moyen et verdict carbone ; sans `--conso`, tranche IA |
| 16 oct. | Verdict sur le facteur ADEME en cycle de vie, au seuil de 100 gCO₂e/kWh de la taxonomie |
| 16 oct. | Local et à la demande uniquement : aucune exécution planifiée |
| 18 oct. | Commentaires limités à la logique (le pourquoi) ; documentation en français ; licence MIT |
| 18 oct. | Sécurité : CI en lecture seule, actions figées par commit, HTTPS seulement, identifiants dans `identifiants.local.txt` (ignoré par git) |
| 18 oct. | Carte : page HTML autonome, three.js embarqué, grille hexagonale maison plutôt que H3 |
| 23 oct. | Outils Python et PySpark privilégiés : ingestion en PySpark (Java 17 requis, en local et en CI), silver et gold en dbt-duckdb |
| 23 oct. | `--ca` sans seuil : le statut d'électro-intensif se juge sur la valeur ajoutée ; `--ca` exige `--conso` |
| 23 oct. | Documentation réduite à deux fichiers : README et ce plan |
| 24 oct. | Adresse refusée plutôt que devinée : score BAN sous 0,65, deux communes à moins de 0,05 l'une de l'autre, ou code postal demandé absent des résultats |
| 24 oct. | Risques : rapport Géorisques à l'adresse d'abord, puis export GASPAR local (maille commune), puis API commune |
| 24 oct. | Chaque sortie donne la date de chargement de ses sources ; au-delà de 45 jours, elle demande un `refresh` |
| 24 oct. | Commentaires : une ligne par fonction sur son but et son intérêt métier ; code simple, Python d'abord |
| 24 oct. | Une étape n'est validée qu'après un test complet : ruff, pytest (dont `dbt build`), essai réel |
| 24 oct. | `--va` : électro-intensité = consommation × accise au tarif normal (26,58 €/MWh au 1er février 2026) ÷ valeur ajoutée, paliers 0,5 %, 3,375 % et 6,75 % (CIBS L312-65, L312-71) ; montant du taux réduit non chiffré |
| 24 oct. | `--courbe` : export Enedis lu en Python à la demande, jamais stocké ; horodate = fin du pas ; tient lieu de `--conso` et `--puissance` s'ils manquent ; coût au barème HP/HC en vigueur, seulement jusqu'à 36 kW |
| 24 oct. | Carbone horaire des îles : profil de la part thermique calé sur le facteur ADEME annuel, plutôt que des facteurs par filière non sourcés ; imports de la Corse comptés non thermiques |
| 24 oct. | UE-27 : moyennes nationales seulement (Eurostat depuis 2024-S1, deux derniers semestres ; Ember, dernière année, production sans échanges) ; jointure des trois codes pays par le seed `dim_pays_ue` ; hors UE refusé |
| 24 oct. | Carte : décor (villes, forêts) et lentilles Terrain et Réseau retirés ; prix et compteurs hors de la barre du haut ; la fiche de l'adresse donne le verdict |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Spark pour silver et gold | dbt-duckdb suffit à ce volume |
| Site web, exécution planifiée, Airflow | Hors périmètre : ligne de commande, en local |
