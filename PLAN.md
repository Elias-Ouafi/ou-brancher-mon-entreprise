# Plan du projet

> Mis à jour le 25 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en ligne de
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
| Entreprises raccordées à Enedis (2025) : volume, industrie et premier secteur NAF de la commune, rang dans le département ; consommation déjà relevée à l'adresse (« Déjà raccordé ici ») | `fct_conso_entreprises_commune`, `fct_conso_entreprises_adresse`, seed `dim_naf2` |
| Verdict « brancher ici ? » en cinq critères (raccordement, risques, carbone, facture, foncier) | `verdict`, `phrase_verdict` |
| Carte 3D épurée autour du verdict : vue « Raccorder » par défaut, adresse reliée à la zone RTE utile et au site clé en main ; épinglée par `essentiel` et `diagnose` (seule l'épingle bouge si les données n'ont pas changé) | `carte`, `reporting/carte.py` |
| Une carte par système électrique (métropole et Corse, chaque DROM), ouverte sur celle de l'adresse ; deux vues : « Raccorder », cases notées du rouge au vert, et « Territoire », paysage réel (mer bleue, verdure, montagnes grises) avec carbone et prix moyen dans l'encart : 95 pôles de production du registre national, en maquettes par filière (réacteurs et tours, barrage-voûte et retenue, cheminée et cuves), fermées de tous côtés, la plus grosse sur les deux tiers de la case | `ingestion/hexagones.py`, `fct_sites_production` |
| Risques sans dépendre de l'API Géorisques : export GASPAR chargé par `refresh` | `fct_risques_commune` |
| Risques « à vérifier » seulement sous un plan de prévention approuvé ou prescrit (13 919 communes au lieu des 31 706 qui ont un risque recensé) | `fct_plans_prevention_commune`, `_critere_risques` |
| Date de chargement de chaque source dans toutes les sorties ; pied de page des rapports avec la commande pour les refaire | `ligne_chargements`, `pied_de_page` |
| Pays de l'UE-27 : prix Eurostat et carbone Ember du pays, face à la France à tranche égale ; pays trouvé par Nominatim | `europe`, `fct_pays_ue` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |
| Rapport mensuel carbone et créneaux Tempo, sur le calendrier réel de l'API RTE (vérifié le 25 octobre sur septembre 2026 et janvier 2026) | `tempo`, `report` |
| Sortie réduite à la réponse (journal sous `--verbose`) ; avant tout chargement, la commande `refresh` à lancer | `ingestion/cli.py` |

**À surveiller** :
- l'API Géorisques coupe toute connexion vers `/api/…` depuis le 24 octobre au moins (la page
  d'accueil répond) ; le diagnostic passe alors sur l'export GASPAR, à la maille commune, sans le
  zonage sismique (aucun fichier national officiel à jour) ;
- les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 : vérifié le 25
  octobre, c'est toujours le dernier millésime valide de la Base Carbone (jeu mis à jour le 3
  juillet 2025) ;
- la fourchette de prix à 3 ans reste indicative tant que le contrôle sur le passé compte moins
  de 20 périodes (8 aujourd'hui, chevauchantes, toutes marquées par la crise de 2022) ;
- les prix Tempo (seed `tarifs_tempo`) ne remontent qu'au 1er février 2026 : un rapport plus ancien
  classe les créneaux sur le seul carbone ;
- l'accise au tarif normal (26,58 €/MWh) vit dans le seed daté `accise_electricite` : à compléter
  à chaque indexation du 1er février, que `--va` signale une fois la date passée ;
- EDF SEI publie jusqu'à fin 2024 seulement (2021-2024 « estimé »), sans Mayotte ; le jeu a
  quitté `opendata-corse-outremer.edf.fr` pour `opendata.edf.fr` (API data-fair).

## Suite

| Priorité | Étape | Bloqué par |
|---|---|---|
| 8 | Repère de prix au-delà de 150 000 MWh/an (contrats CAPN) ; classement pondéré dans `compare` | décision |
| 10 | Intégrer les sources retenues par l'étude du 25 octobre, dans l'ordre ci-dessous (10.1 faite le 25 octobre) | voir chaque source |
| 11 | Carte : très grandes zones de fret (grands ports, grands aéroports de fret), sans les petites | source et seuil de taille à fixer |
| 6 | Extension internationale, en dernier, voir ci-dessous | jeton ENTSO-E (compte gratuit) et contours des zones de prix à sourcer |

**Sources à intégrer (étude du 25 octobre)**, de la plus utile à « brancher ici ? » à la moins
utile. Toutes sont publiées par leur producteur officiel, sous licence ouverte, et se lisent avec
les clients HTTP déjà en place (data-fair comme EDF SEI, Opendatasoft comme ODRÉ) : aucune
nouvelle dépendance.

| Rang | Source | Producteur, accès | Ce qu'elle apporte | Limite ou blocage |
|---|---|---|---|---|
| ~~10.1~~ | **Fait le 25 octobre.** Consommation d'électricité annuelle des entreprises par adresse : dernière année seulement (2025, 499 125 lignes, 142 TWh, en 18 s) | Enedis, data-fair `opendata.enedis.fr`, jeu `consommation-annuelle-entreprise-par-adresse` | Industrie et tertiaire réels de la commune et de l'adresse (MWh, code NAF), au lieu des seuls sites raccordés à RTE | Réseau Enedis seulement (ni ELD ni RTE) ; 7 043 lignes sans adresse, comptées à la commune seulement |
| 10.2 | Grille TURPE 7 HTA-BT au 1er août 2026 | CRE, délibération n° 2026-105 du 21 mai 2026 (annexes) ; seed daté comme `tarifs_hphc` | Coût d'acheminement selon le domaine de tension (BT > 36 kVA, HTA courte ou longue utilisation) : la seule part de la facture qui dépend du raccordement, donc de la puissance | Valeurs à relever dans le PDF ; mise à jour chaque 1er août |
| 10.3 | Continuité d'alimentation : indicateur réglementaire par département (2009-2024), durée et fréquence moyennes des coupures BT et HTA (nationales) | Agence ORE et Enedis, data-fair `opendata.agenceore.fr` | Repère « qualité de fourniture » dans `diagnose` : part des clients du département hors des seuils réglementaires, comparée à la moyenne nationale | Maille du département ; pas de critère B par commune en open data |
| 10.4 | Zonage sismique réglementaire par commune | Annexe du décret n° 2010-1255 (articles R563-1 à R563-8 du code de l'environnement) et tableur de planseisme.fr (ministère, BRGM) ; seed par code INSEE | Comble le zonage sismique absent quand l'API Géorisques est coupée | Tableur à recaler sur le code officiel géographique actuel (communes fusionnées) |
| 10.5 | Agrégats de consommation au pas d'une demi-heure des points de soutirage de plus de 36 kVA, par secteur d'activité et puissance souscrite (maille régionale) | Enedis, data-fair, jeu `conso-sup36-region` | Profils de charge réels par secteur pour `fct_cout_horaire_profil`, au lieu des profils du seed | Volume (34,6 M lignes) : filtrer à la source par région et secteur |
| 10.6 | Signal Ecowatt (tension du système électrique, J à J+3) | RTE, API Ecowatt v5 (même compte que Tempo) | Jours de tension dans le rapport mensuel, à côté des jours rouges Tempo | Le bac à sable répond ; l'API réelle renvoie 403 tant que l'application n'est pas abonnée à « Ecowatt » sur le portail RTE |

Pour l'étape 6, la bibliothèque `entsoe-py` (EnergieID, MIT, publiée sur PyPI, maintenue en
2026) est le client ENTSO-E de référence, plutôt qu'un client maison.

**Extension internationale**, dans cet ordre (l'UE-27 à la maille pays est faite) :
1. Zones de prix (Italie, pays nordiques) : ENTSO-E et point-dans-polygone avec l'extension
   `spatial` de DuckDB.
2. Royaume-Uni (intensité régionale officielle) et États-Unis (EIA, eGRID), où prix et carbone
   dépendent vraiment de l'adresse.

**Europe de l'Ouest, pays par pays (étude du 24 octobre)** : ordre à suivre pour étendre le
diagnostic par adresse, du pays où les données se récoltent le plus facilement au plus difficile.
Critères : adresses géocodables en open data, codes et contours communaux, prix (Eurostat ou
mieux), carbone (Ember, ou par zone si le pays en a plusieurs), capacité de raccordement publiée,
risques, registre des installations. Pour tous les pays de l'UE, les sites Seveso viennent
d'eSPIRS, l'aléa sismique d'EFEHR (ESHM20) et les zones inondables des cartes de la directive
Inondations.

| Rang | Pays | Ce qui est facile | Ce qui bloque |
|---|---|---|---|
| 1 | Pays-Bas | Adresses BAG et API PDOK (locatieserver) ; carte nationale des capacités avec couche soutirage (dès 3 × 80 A), exportable ; codes CBS | Capacité indicative ; licence de l'export à vérifier |
| 2 | Belgique | Adresses BeST ouvertes ; codes INS ; carte des capacités d'Elia (horizons 2026 et 2031) | Trois régions, donc trois sources pour les risques et les GRD (Fluvius, ORES, Sibelga) |
| 3 | Espagne | API CartoCiudad (CNIG) ; API REData de REE sans jeton, par système électrique (Baléares, Canaries, Ceuta, Melilla : même logique que nos ZNI) ; cartes de capacité d'accès de la demande imposées par la CNMC (février 2026) ; zones inondables SNCZI | Nœuds frontière transport-distribution sans valeur convenue |
| 4 | Autriche | Adresses BEV en open data (instantanés semestriels) ; capacités par poste publiées chaque trimestre sur une plateforme commune (ElWG § 99) ; risques HORA | Pseudo-adresses signalées autour de Vienne |
| 5 | Luxembourg | Adresses ouvertes (data.public.lu) ; un seul GRD (Creos) ; pays minuscule | Capacité de raccordement non publiée |
| 6 | Suisse | Adresses officielles et API geo.admin ; prix par commune et par profil (ElCom, API ouverte) : le prix dépend vraiment de l'adresse ; cartes de dangers fédérales | Hors UE et hors Eurostat : `europe` le refuse aujourd'hui ; capacité non publiée |
| 7 | Portugal | Base nationale des adresses de l'INE (CC BY, continent seulement) ; contours CAOP ; portail open data d'E-REDES | Madère et Açores sans données comparables ; capacité de soutirage peu publiée |
| 8 | Allemagne | Registre MaStR complet et ouvert ; carbone d'une seule zone | Adresses éparses par Land (licence du jeu national GA du BKG à vérifier) ; prix variable selon ~850 gestionnaires de réseau ; capacités publiées GRD par GRD |
| 9 | Royaume-Uni | Intensité carbone régionale (API Carbon Intensity) ; cartes de marge des GRD ouvertes ; zones inondables de l'Environment Agency ; codes postaux géolocalisés ouverts (ONSPD) | Adresses payantes (AddressBase) ; plus d'Eurostat depuis le Brexit (prix DESNZ) ; six GRD aux formats différents ; Écosse et Irlande du Nord à part |
| 10 | Irlande | Prix Eurostat ; zones inondables OPW ; Dublin entièrement saturée (contraintes publiées en 2026) | Adresses payantes (Eircode, GeoDirectory) ; cartes de capacité annoncées, pas encore en open data |
| 11 | Italie | Données Terna ouvertes ; risques ISPRA (IdroGEO) | Seulement ~5 % des numéros ANNCSU géolocalisés ; sept zones de prix ; Econnextion donne les demandes, pas la capacité |
| — | Monaco, Liechtenstein, Andorre | Monaco et le Liechtenstein suivent les réseaux français et suisse | Ni adresses ouvertes ni prix fiables (Eurostat ne donne que des zéros sur une tranche du Liechtenstein) : à traiter avec le pays voisin, en dernier |

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
- **Sources de données (étude du 25 octobre)** : RTE a retiré les coordonnées GPS de ses postes
  et de ses lignes « pour des raisons de sécurité publique » (jeux ODRÉ au 16 juin 2026), et EDF
  a fait de même pour ses postes sources ; le seul jeu de postes sources Enedis géolocalisés est
  local (agglomération du Puy-en-Velay). Toute distance à un poste est donc exclue. Enedis et
  l'Agence ORE ont quitté l'API Opendatasoft pour data-fair (`/data-fair/api/v1/datasets`) :
  l'ancienne API répond 410. La capacité en soutirage d'Enedis reste réservée au portail client
  entreprise ; l'outil Toprac de RTE, cité par la CRE, n'a pas de version publique trouvée.
- **Ce qui peut bouger (étude du 24 octobre)** : la CRE impose à RTE de refondre Caparéseau
  d'ici le 1er janvier 2027 et RTE étudie une carte de ses capacités en soutirage (délibération
  CRE 2026-173) : à intégrer dès sa publication. Enedis publie ses postes HTA/BT géolocalisés
  (licence ouverte) ; pas de jeu national des postes sources géolocalisés (voir ci-dessus).

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
| 24 oct. | Critère risques : « attention » seulement si un plan de prévention (PPRN, PPRT, PPRM) approuvé, anticipé, prescrit ou prorogé couvre la commune (export GASPAR, plans caducs écartés) ; sinon « info » avec les risques recensés ; « inconnu » si les plans ne sont pas chargés |
| 24 oct. | Chaque sortie donne la date de chargement de ses sources ; au-delà de 45 jours, elle demande un `refresh` |
| 24 oct. | Commentaires : une ligne par fonction sur son but et son intérêt métier ; code simple, Python d'abord |
| 24 oct. | Une étape n'est validée qu'après un test complet : ruff, pytest (dont `dbt build`), essai réel |
| 24 oct. | `--va` : électro-intensité = consommation × accise au tarif normal (26,58 €/MWh au 1er février 2026) ÷ valeur ajoutée, paliers 0,5 %, 3,375 % et 6,75 % (CIBS L312-65, L312-71) ; montant du taux réduit non chiffré |
| 24 oct. | `--courbe` : export Enedis lu en Python à la demande, jamais stocké ; horodate = fin du pas ; tient lieu de `--conso` et `--puissance` s'ils manquent ; coût au barème HP/HC en vigueur, seulement jusqu'à 36 kW |
| 24 oct. | Carbone horaire des îles : profil de la part thermique calé sur le facteur ADEME annuel, plutôt que des facteurs par filière non sourcés ; imports de la Corse comptés non thermiques |
| 24 oct. | UE-27 : moyennes nationales seulement (Eurostat depuis 2024-S1, deux derniers semestres ; Ember, dernière année, production sans échanges) ; jointure des trois codes pays par le seed `dim_pays_ue` ; hors UE refusé |
| 24 oct. | Commandes de question : journal masqué (niveau WARNING, message seul), chemins des fichiers écrits sur stderr ; warehouse absent ou gold incomplet → `DonneesAbsentesError` et commande `refresh` à lancer |
| 24 oct. | Carte : décor (villes, forêts) et lentilles Terrain et Réseau retirés ; prix et compteurs hors de la barre du haut ; la fiche de l'adresse donne le verdict |
| 25 oct. | Carte : deux vues au lieu de quatre ; « Raccorder » note chaque case de -1 à +1 (zone RTE à moins de 3 cases, site clé en main à moins de 2, poste 400 kV, contraintes RTE au-delà de la médiane, relief, carbone au-dessus de 100 g, équilibre régional), note indicative ; « Territoire » réunit industrie (hauteur), équilibre (couleur, violet → bleu pour ne pas se confondre avec le rouge et le vert de la note) et production (maquettes) |
| 26 oct. | Carte : fausses couleurs réservées à « Raccorder » (rouge → blanc → vert, sans cônes de montagne) ; « Territoire » revient aux couleurs réelles et au relief IGN, l'encart donne carbone et prix moyen par tranche (`fct_prix_entreprise_tranche`) ; frontières régionales en gris sombre plutôt qu'en couleurs |
| 24 oct. | Carte : une carte par système électrique, placées à 600 unités l'une de l'autre dans la même scène (le brouillard cache les autres) plutôt qu'une page par territoire |
| 24 oct. | Pôles de production : registre ODRÉ agrégé à la source par commune et filière (installations en service d'au moins 10 MW ; les noms « Confidentiel » interdisent une clé par installation), centre de la commune comme position ; seuil 200 MW sur le continent, 20 MW en Corse et outre-mer ; stockage hors barrages exclu |
| 24 oct. | Après le MVP, extension par adresse en Europe de l'Ouest dans l'ordre de facilité des données : Pays-Bas, Belgique, Espagne, Autriche, Luxembourg, Suisse, Portugal, Allemagne, Royaume-Uni, Irlande, Italie, puis les micro-États (détail dans « Suite ») |
| 24 oct. | `main` protégée par un ruleset GitHub : PR obligatoire (sans approbation), job CI `ruff + pytest + dbt build` vert sur une branche à jour, ni force-push ni suppression, aucun contournement |
| 25 oct. | Accise au tarif normal dans le seed daté `accise_electricite` (ligne sans `date_fin` = barème en vigueur) ; passé le 1er février suivant sa date d'effet, `--va` demande de le mettre à jour |
| 25 oct. | Fourchette de prix à 3 ans élargie pour contenir toujours le prix des 12 derniers mois ; dite « indicative » tant que moins de 20 périodes sont contrôlées sur le passé (variable dbt `prix_nb_periodes_validation`) |
| 25 oct. | Bronze : un Parquet n'est jamais écrasé ; un même `run_id` le même jour prend le suffixe `-2`, `-3`… (deux mois Tempo chargés à la suite écrasaient le premier) |
| 25 oct. | Tempo hors `refresh` : il demande un compte RTE, que les autres commandes n'exigent pas ; `tempo --month` se lance avant `report` |
| 25 oct. | L'extension internationale (étape 6) passe en dernier |
| 25 oct. | Nouvelles sources : producteur officiel et licence ouverte seulement, méthode publiée ou bibliothèque maintenue et testée ; une copie tierce sert au mieux de repli signalé |
| 25 oct. | Chaque nouvelle source suit l'architecture médaillon : extraction vers un Parquet bronze immuable puis `MERGE` DuckDB, silver nettoyé et gold en dbt, limite dans `limites_methodologiques` |
| 25 oct. | Enedis par adresse : pages CSV data-fair de 10 000 lignes (le JSON prenait 9 min pour une année), dernière année publiée seulement ; adresse rapprochée sur « numéro + voie » normalisés, seulement si la BAN trouve le numéro ; une consommation à l'adresse prouve un raccordement, pas une puissance disponible |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Distance aux postes sources et aux lignes | Coordonnées retirées par RTE et EDF en 2026 (sécurité publique) ; jeu Enedis seulement local |
| Zonage sismique national publié par wpd (data.gouv.fr, 2018) | Producteur non officiel (un développeur éolien) : le décret et planseisme.fr font foi |
| Spark pour silver et gold | dbt-duckdb suffit à ce volume |
| Site web, exécution planifiée, Airflow | Hors périmètre : ligne de commande, en local |
