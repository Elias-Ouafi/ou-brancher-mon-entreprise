# Plan du projet

> Mis à jour le 28 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en ligne de
> commande, en local et à la demande.

## Persona

**Pivot du 28 octobre** : le porteur d'un **projet de 1 à 50 MW** qui cherche où s'implanter :
data center, hub de bornes de recharge (véhicules légers et poids lourds), site industriel, site
logistique (entrepôt froid, quai électrifié). C'est le modèle de Yottar, cité par le porteur du
projet (aucune source publique sur Yottar trouvée le 28 octobre). Le persona précédent, l'entreprise
qui déménage ses bureaux, est abandonné : pour quelques dizaines de MWh, le prix de l'électricité
est identique partout et ne départage pas les villes ; l'outil n'avait rien de décisif à lui dire.

Pour un projet de 1 à 50 MW, l'électricité **est** le critère d'implantation :
- **puissance disponible et délai** : de 0,25 à 20 MW, Enedis en HTA (départ dédié au-delà de
  quelques MW) ; de 20 à 40 MW, zone grise Enedis ou RTE selon la saturation du poste source ; au-delà
  de 40 MW, RTE en HTB, 2 à 7 ans de délai (seed `seuils_raccordement`). C'est ce qui départage deux
  sites, et ce que l'open data ne publie pas : d'où les zones prioritaires RTE, les contraintes
  prévues et la carte des capacités en soutirage attendue de RTE (1er janvier 2027) ;
- **foncier viabilisé et constructible** : sites clés en main France 2030, zonage d'urbanisme,
  friches et zones d'activité, prix des terrains et locaux d'activité ;
- **coût de l'électricité** : identique d'une ville à l'autre à tranche égale (péréquation), mais
  décisif pour la rentabilité ; 1 MW constant fait environ 8 800 MWh par an (tranche ID), 50 MW
  environ 440 000 MWh (au-delà de la tranche IG d'Eurostat, sans repère public aujourd'hui) ; la
  part acheminement dépend du domaine de tension, donc du site ; l'accise réduite (électro-intensifs,
  data centers) change la facture ;
- **carbone** : bas partout sur le continent ; décisif en Corse et outre-mer et pour les data
  centers qui vendent une électricité bas-carbone à leurs clients ;
- **risques et contraintes du site** : plans de prévention, sismicité, ICPE voisines, eau (le
  refroidissement des data centers), accès poids lourds et fret pour la logistique et l'industrie.

Contexte : RTE dit recevoir des demandes de 100 à 200 MW pour des data centers, et 4,5 GW avaient
déjà une offre de raccordement acceptée en septembre 2024 ; data centers, bornes de recharge et
industrie se disputent la même capacité, concentrée en Île-de-France, à Marseille et dans les
Hauts-de-France. Le projet de plus de 50 MW reste servi (HTB, `diagnose` au complet) mais ne dicte
pas l'ordre des étapes.

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
| Part acheminement de la facture (TURPE 7 HTA-BT au 1er août 2026) selon le domaine de tension, la puissance et la consommation, sur la courbe de charge si elle est fournie | `ingestion/turpe.py`, seeds `turpe_soutirage`, `turpe_gestion_comptage` |
| Part de la facture dans le chiffre d'affaires (`--ca`) et électro-intensité fiscale dans la valeur ajoutée (`--va`) | `essentiel`, `diagnose` |
| Courbe de charge Enedis (`--courbe`) : consommation, pointe, heures creuses, coût et carbone réels | `ingestion/courbe.py` |
| Diagnostic complet : industrie, réseau, zone RTE, site clé en main, risques | `diagnose`, `compare` |
| Entreprises raccordées à Enedis (2025) : volume, industrie et premier secteur NAF de la commune, rang dans le département ; consommation déjà relevée à l'adresse (« Déjà raccordé ici ») | `fct_conso_entreprises_commune`, `fct_conso_entreprises_adresse`, seed `dim_naf2` |
| Verdict « brancher ici ? » en cinq critères (raccordement, risques, carbone, facture, foncier), dans `essentiel`, `diagnose`, `compare` et sur la carte | `verdict`, `phrase_verdict` |
| Carte 3D épurée autour du verdict : vue « Raccorder » par défaut, adresse reliée à la zone RTE utile et au site clé en main ; épinglée par `essentiel` et `diagnose` (seule l'épingle bouge si les données n'ont pas changé) | `carte`, `reporting/carte.py` |
| Une carte par système électrique (métropole et Corse, chaque DROM), ouverte sur celle de l'adresse ; deux vues : « Raccorder », cases notées du rouge au blanc puis au vert, et « Territoire », paysage réel (mer bleue, verdure, relief IGN) avec carbone et prix moyen dans l'encart ; dans les deux vues, 95 pôles de production du registre national en maquettes par filière (réacteurs et tours, barrage-voûte et retenue, cheminée et cuves), la plus grosse sur les deux tiers de la case, et les sites clés en main en maquette (dalle, hangar, bureaux, mât au drapeau doré), nommés au survol | `ingestion/hexagones.py`, `fct_sites_production` |
| Grands pôles de fret dans les deux vues de la carte, en maquettes (port à portique, aéroport et avion cargo, chantier rail-route) : ports d'au moins 5 Mt de marchandises hors tare (0,5 Mt en Corse et outre-mer), aéroports d'au moins 20 000 t de fret (3 000 t), chantiers SNCF Réseau d'au moins 2 km de voies ; 34 pôles en 2025 | `ingestion/extract_fret.py`, `fct_poles_fret`, seeds `dim_ports_fret` et `dim_aeroports_fret` |
| Risques sans dépendre de l'API Géorisques : export GASPAR chargé par `refresh` | `fct_risques_commune` |
| Zone de sismicité réglementaire (1 à 5) à l'adresse, sans l'API : couche WFS de Géorisques chargée par `refresh`, point dans polygone en Python | `ingestion/extract_sismique.py`, `fct_zonage_sismique` |
| Qualité de fourniture du département dans `diagnose` : part des clients Enedis au-delà des seuils réglementaires de coupure (dernière année, moyenne sur cinq ans, médiane des départements, seuil de 3 %) ; « non publiée » hors Enedis | `fct_continuite_departement`, `ligne_continuite` |
| Risques « à vérifier » seulement sous un plan de prévention approuvé ou prescrit (13 919 communes au lieu des 31 706 qui ont un risque recensé) | `fct_plans_prevention_commune`, `_critere_risques` |
| Date de chargement de chaque source dans toutes les sorties ; pied de page des rapports avec la commande pour les refaire | `ligne_chargements`, `pied_de_page` |
| Prix de vente des locaux d'activité : médiane et quartiles au m² par commune (repli sur le département sous 5 ventes), cinq dernières années de ventes DVF (393 099 ventes de 2021 à 2025 chargées, 219 564 retenues) ; coût d'achat avec `--surface` | `ingestion/extract_dvf.py`, `ventes_locaux`, `fct_prix_locaux`, `ligne_locaux` |
| Pays de l'UE-27 : prix Eurostat et carbone Ember du pays, face à la France à tranche égale ; pays trouvé par Nominatim | `europe`, `fct_pays_ue` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |
| Rapport mensuel carbone et créneaux Tempo, sur le calendrier réel de l'API RTE (vérifié le 25 octobre sur septembre 2026 et janvier 2026) | `tempo`, `report` |
| Sortie réduite à la réponse (journal sous `--verbose`) ; avant tout chargement, la commande `refresh` à lancer | `ingestion/cli.py` |

**À surveiller** :
- l'API Géorisques était coupée depuis le 24 octobre ; elle répond de nouveau le 28 octobre
  (`/api/v1/resultats_rapport_risque`, HTTP 200, 5,9 s). Le repli sur l'export GASPAR (maille
  commune) et sur le zonage sismique WFS reste en place ; planseisme.fr ne résout plus ;
- les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 : vérifié le 25
  octobre, c'est toujours le dernier millésime valide de la Base Carbone (jeu mis à jour le 3
  juillet 2025) ;
- la fourchette de prix à 3 ans reste indicative tant que le contrôle sur le passé compte moins
  de 20 périodes (8 aujourd'hui, chevauchantes, toutes marquées par la crise de 2022) ;
- les prix Tempo (seed `tarifs_tempo`) ne remontent qu'au 1er février 2026 : un rapport plus ancien
  classe les créneaux sur le seul carbone ;
- l'accise au tarif normal (26,58 €/MWh) vit dans le seed daté `accise_electricite` : à compléter
  à chaque indexation du 1er février, que `--va` signale une fois la date passée ;
- DVF 2026 n'est pas encore publié (dernière vente chargée : décembre 2025) : relancer `refresh` après chaque actualisation d'avril et d'octobre ;
- EDF SEI publie jusqu'à fin 2024 seulement (2021-2024 « estimé »), sans Mayotte ; le jeu a
  quitté `opendata-corse-outremer.edf.fr` pour `opendata.edf.fr` (API data-fair).

## Suite

**Pivot vers les projets de 1 à 50 MW (28 octobre)** : le MVP du 16 octobre (prix moyen et verdict
carbone) reste atteint ; ce qui manque à ce persona est d'abord ce qui dit **si la puissance est
là, quand, et à quel coût**. Le prix de vente des locaux d'activité (étape 12, DVF) reste affiché
pour information (il couvre entrepôts et locaux industriels) mais n'est plus prioritaire ; le
loyer reste écarté. Le verdict « Brancher ici ? » en cinq critères garde sa forme ; son critère
« raccordement » devient le premier. Exemple de référence pour les essais réels : 12 MW et
70 000 MWh par an à Chilly-Mazarin (HTA, tranche IF, environ 5,3 M€ HTVA par an dont 1,4 M€
d'acheminement, vérifié le 28 octobre).

Déjà fait : étape 9 (maquettes 3D, carte à deux vues), sources 10.1 (Enedis par adresse), 10.2
(grille TURPE 7 HTA-BT), 10.3 (continuité d'alimentation), 10.4 (zonage sismique) et 11 (pôles de
fret) du 25 au 26 octobre ; étape 12 (prix de vente des locaux, PR 47) le 28 octobre. Prochaines
étapes, dans l'ordre où les prendre ; chacune n'est cochée qu'après ruff, pytest (dont `dbt build`)
et un essai réel sur un projet de 1 à 50 MW :

| Ordre | Étape | Pourquoi pour ce persona | Bloqué par |
|---|---|---|---|
| 1 | **17** Grille **TURPE 7 HTB** (RTE) en seed daté, à côté de la grille HTA-BT | Au-delà de 20 MW la part acheminement n'est pas chiffrée aujourd'hui ; c'est elle qui varie selon le site (HTA ou HTB) | délibération CRE du TURPE 7 HTB à relire (hausse moyenne de 3,34 % au 1er août 2026) |
| 2 | **8** Repère de prix au-delà de 150 000 MWh/an, après la fin de l'ARENH (contrats d'allocation de production nucléaire, prix de marché à terme) | Un projet de 20 MW constant dépasse la dernière tranche Eurostat (IG, sans borne) | source officielle et ouverte d'un prix à terme à trouver ; le « classement pondéré dans `compare` » est retiré (décision du 15 octobre maintenue) |
| 3 | **18** Accise selon l'activité : taux réduits des électro-intensifs (déjà `--va`), des centres de stockage de données et des autres catégories du CIBS ; option `--activite` (`data-center`, `irve`, `industrie`, `logistique`) | Change la facture d'un data center de plusieurs centaines de milliers d'euros par an ; donne le profil de charge par défaut | articles du CIBS et conditions (seuils de consommation, efficacité) à relire avant de coder |
| 4 | **10.5** Profils de charge réels > 36 kVA par secteur (Enedis `conso-sup36-region`) | Remplace les profils du seed : la part acheminement et le carbone horaire dépendent de la forme de la courbe | volume : filtrage à la source par région et secteur à mettre au point |
| 5 | **19** Capacité d'accueil en soutirage : intégrer la carte des capacités de RTE et le nouveau Caparéseau dès publication ; chercher ce qu'Enedis publie en HTA (consommation, pas seulement production) | Première question du persona, sans réponse publique aujourd'hui | publication RTE attendue au 1er janvier 2027 (délibération CRE 2026-173) ; capacité Enedis réservée au portail client à ce jour |
| 6 | **14** Zonage d'urbanisme à l'adresse (API Carto, Géoportail de l'Urbanisme) et **friches et zones d'activité** (Cartofriches) | Un terrain où l'activité industrielle ou l'entrepôt est interdit ne vaut rien, même raccordable | couverture variable selon les communes ; licence de Cartofriches à lire |
| 7 | **15** Risques complémentaires (argiles, cavités, inondation à l'adresse) et **ICPE, Seveso, sols pollués** voisins (Géorisques) | Un site industriel ou logistique peut être soumis à autorisation ; voisinage Seveso | endpoints à relire (documentation en erreur le 28 octobre) |
| 8 | **20** Eau : prélèvements et tension sur la ressource (BNPE, Hub'Eau, arrêtés sécheresse) | Refroidissement des data centers ; procédés industriels | licences et maille à vérifier |
| 9 | **13** Accès routier : temps vers l'échangeur autoroutier, le port, l'aéroport et le chantier rail-route les plus proches (Géoplateforme, `bdtopo-osrm`) | Logistique et industrie : desserte poids lourds ; complète les pôles de fret | pas de profil poids lourd dans la Géoplateforme |
| 10 | **16** Bâtiment et solaire : emprise (BD TOPO), productible solaire (PVGIS), solarisation des parkings et toitures (loi APER) | Autoconsommation d'un entrepôt ou d'un hub de recharge | licence de la BDNB ; conditions d'usage de PVGIS |
| 11 | **10.6** Signal Ecowatt dans le rapport mensuel | Jours de tension : effacement et flexibilité d'un data center ou d'un hub de recharge | identifiants enregistrés le 28 octobre ; accès réel (plus de 403) à vérifier |
| 12 | **6** Extension internationale, voir ci-dessous | En dernier (décision du 25 octobre) | jeton ENTSO-E et contours des zones de prix |

Les bornes de recharge ont leurs propres règles de raccordement (prise en charge d'une part du coût
par le tarif, dite réfaction) : à étudier avec l'étape 18, sans rien afficher avant d'avoir le texte.

**Échéances à guetter** (à lancer à la main, aucune planification) :
- 1er janvier 2027 : refonte de Caparéseau et éventuelle carte des capacités en soutirage de RTE
  (délibération CRE 2026-173), à intégrer dès publication ;
- 1er février 2027 : indexation de l'accise, ligne à ajouter au seed `accise_electricite` ;
- 1er août 2027 : nouvelle grille TURPE 7 HTA-BT, à reporter dans les seeds `turpe_soutirage`
  et `turpe_gestion_comptage` (les sorties le signalent une fois la date passée) ;
- quand EDF SEI publiera 2025 : `refresh` pour recaler le carbone horaire des îles.

**Sources à intégrer (étude du 25 octobre)**, de la plus utile à « brancher ici ? » à la moins
utile. Toutes sont publiées par leur producteur officiel, sous licence ouverte, et se lisent avec
les clients HTTP déjà en place (data-fair comme EDF SEI, Opendatasoft comme ODRÉ) : aucune
nouvelle dépendance.

| Rang | Source | Producteur, accès | Ce qu'elle apporte | Limite ou blocage |
|---|---|---|---|---|
| ~~10.1~~ | **Fait le 25 octobre.** Consommation d'électricité annuelle des entreprises par adresse : dernière année seulement (2025, 499 125 lignes, 142 TWh, en 18 s) | Enedis, data-fair `opendata.enedis.fr`, jeu `consommation-annuelle-entreprise-par-adresse` | Industrie et tertiaire réels de la commune et de l'adresse (MWh, code NAF), au lieu des seuls sites raccordés à RTE | Réseau Enedis seulement (ni ELD ni RTE) ; 7 043 lignes sans adresse, comptées à la commune seulement |
| ~~10.2~~ | **Fait le 26 octobre.** Grille TURPE 7 HTA-BT au 1er août 2026 | CRE, délibération n° 2026-105 du 21 mai 2026 (annexe 2, pages 21 à 27) ; seeds datés `turpe_soutirage` et `turpe_gestion_comptage` | Coût d'acheminement selon le domaine de tension (BT > 36 kVA, HTA courte ou longue utilisation) : 22,6 €/MWh pour 1 MW et 4 000 MWh en HTA | Mise à jour chaque 1er août ; HTB (RTE) non chiffré |
| ~~10.3~~ | **Fait le 26 octobre.** Continuité d'alimentation : indicateur réglementaire par département (2009-2024) | Enedis, data-fair `opendata.enedis.fr`, jeu `indicateur-continuite-dalimentation` (licence ouverte, 94 départements × 16 ans = 1 504 lignes ; l'Agence ORE en publie une copie) ; bronze `enedis_continuite_alimentation`, gold `fct_continuite_departement` | Repère « qualité de fourniture » dans `diagnose` : part des clients du département au-delà des seuils réglementaires, face au seuil de 3 % et à la médiane des départements (1,7 % en 2024) ; vérifié sur l'Ain (4,6 %, au-dessus), Lyon (0,8 %), Ajaccio (non publiée) | Maille du département ; ni Corse, ni outre-mer, ni ELD ; pas de critère B par commune en open data |
| ~~10.4~~ | **Fait le 26 octobre.** Zonage sismique réglementaire (décret n° 2010-1255, article D563-8-1 du code de l'environnement) | Géorisques, service WFS `https://www.georisques.gouv.fr/services`, couche `risq_zonage_sismique` (36 719 polygones, 158 Mo de géométrie, 85 s) ; bronze `zonage_sismique`, gold `fct_zonage_sismique` | Zone 1 à 5 à l'adresse quand l'API Géorisques est coupée ; vérifié sur Lyon (2), Nice (4), Pointe-à-Pitre (5), Paris (1) | Polygones des communes de 2010 sans code INSEE : situés par l'adresse, pas par le code commune |
| 10.5 | Agrégats de consommation au pas d'une demi-heure des points de soutirage de plus de 36 kVA, par secteur d'activité et puissance souscrite (maille régionale) | Enedis, data-fair, jeu `conso-sup36-region` | Profils de charge réels par secteur pour `fct_cout_horaire_profil`, au lieu des profils du seed | Volume (34,6 M lignes) : filtrer à la source par région et secteur |
| 10.6 | Signal Ecowatt (tension du système électrique, J à J+3) | RTE, API Ecowatt v5 (même compte que Tempo) | Jours de tension dans le rapport mensuel, à côté des jours rouges Tempo | Le bac à sable répond ; l'API réelle renvoie 403 tant que l'application n'est pas abonnée à « Ecowatt » sur le portail RTE |

**Étude du 28 octobre : données géographiques (étapes 13 à 16 et suites)**. L'étude complète (sources, licences, imagerie, modèle d'indicateurs, moteur de classement) est restée hors du dépôt (documentation limitée à ce plan et au README) ; ne restent ici que ses conclusions. Trois manques sans source ouverte : les loyers et les annonces de locaux, la capacité d'accueil en soutirage, les parcelles et propriétaires (fichiers fonciers du Cerema, réservés aux ayants droit sous convention). Version 2 : photovoltaïque existant (OpenPVMapper, CC-BY, ~75 % de précision), bassin d'emploi (isochrone et flux domicile-travail INSEE), friches et zones d'activité (Cartofriches), détection de changement sur l'ortho. Version 3 : projections climatiques (DRIAS, Explore2), extension européenne. Imagerie : 100 % ouverte au lancement (BD ORTHO 20 cm, LiDAR HD) ; l'imagerie commerciale (Pléiades Neo, 22 à 33 $/km² chez un revendeur) n'est justifiée que pour un site précis. Points non vérifiés avant de coder : licences de la BDNB, du LiDAR HD, de DRIAS et de l'OCS GE ; endpoints Géorisques ; conditions d'usage de PVGIS ; couverture du GPU ; stress hydrique (BNPE, Hub'Eau) ; date de la carte RGA 2026.

**Pôles de fret, étape 11 (étude du 26 octobre, faite le même jour)**. Fichiers téléchargés et
lus ; tonnages 2025. Retenu à l'implémentation : marchandises hors tare pour les ports (Sète, 4,9 Mt
hors tare, passe sous le seuil ; Calais tombe de 39,1 à 14,8 Mt).

| Mode | Source | Ce qu'elle donne | Seuil proposé et pôles retenus | Limite |
|---|---|---|---|---|
| Ports maritimes | SDES, data.gouv.fr « Maritime : transport de marchandises » (DiDo, CSV de 1 540 lignes, licence ouverte, mis à jour le 29 juillet 2026) | Tonnage entré et sorti par port et par année depuis 2000, par type de fret (vracs liquides et solides, conteneurs en EVP, roulier) ; 42 ports, code LOCODE | 5 Mt sur le continent : HAROPA (84,4), Marseille (74,0), Dunkerque (48,0), Calais (39,1, roulier vers le Royaume-Uni), Nantes-Saint-Nazaire (26,2), La Rochelle (8,8), Bordeaux (6,1), Sète (6,0) ; 0,5 Mt en Corse et outre-mer : Port Réunion (6,1), Pointe-à-Pitre (3,4), Fort-de-France (3,3), Bastia (2,9), Ajaccio (1,4), Cayenne (0,96) | Pas de coordonnées : seed LOCODE → commune, centre de la commune comme pour les centrales ; HAROPA publié d'un bloc (Le Havre, Rouen, Paris), placé au Havre ; Mayotte absente |
| Aéroports | DGAC, data.gouv.fr « Trafic aérien commercial mensuel français », série APT (zip de 860 Ko, licence ouverte 2.0, 1990-2025) | Fret et poste embarqués et débarqués (tonnes) et vols tout-cargo, par aéroport et par mois, code OACI | 20 000 t sur le continent : Paris-CDG (1 891 666), Orly (70 953), Marseille (61 883), Bâle-Mulhouse (54 606), Lyon (52 523), Toulouse (45 001), Saint-Nazaire-Montoir (24 251, Airbus) ; 3 000 t en Corse et outre-mer : La Réunion (37 992), Guadeloupe (15 950), Martinique (14 290), Bastia (8 723), Ajaccio (6 793), Mayotte (4 285), Cayenne (3 472) | Pas de coordonnées : seed OACI → commune ; Tahiti et Nouméa hors des cartes |
| Chantiers rail-route | SNCF Réseau, data.gouv.fr « Chantiers de transport combinés » (CSV, ODbL, figé au 23 mars 2022) | 18 chantiers du réseau ferré national avec adresse, latitude, longitude, longueur de voies de manutention et engins (portiques, grues) | Aucun seuil de tonnage possible : longueur de voies en repère de taille (Valenton 7,4 km, Bordeaux Hourcade 4,5 km, Perpignan 3,8 km…) | Aucun volume publié, ni par chantier ni par gare ; chantiers privés et portuaires absents (Dourges, Lyon Édouard-Herriot) ; licence ODbL, à citer |
| Ports fluviaux | SDES « Fluvial : transport de marchandises » (VNF) | Tonnage par région et département d'origine et de destination | Écarté | Pas de maille port |

Intégration, sur le modèle des pôles de production : extraction des trois fichiers vers le bronze
(`ports_fret`, `aeroports_fret`, `chantiers_rail_route`, étape `fret` de `refresh`), seeds
`dim_ports_fret` et `dim_aeroports_fret` (commune de chaque port et aéroport, contrôlés par le test
`assert_poles_fret_localises`), gold `fct_poles_fret`, seuils en variables dbt `fret_seuil_*` ;
dans `reporting/carte.html`, trois maquettes dans les deux vues, taille selon le tonnage (la
longueur de voies pour le rail), nom et tonnage au survol, encart « Fret » dans « Territoire ».

**Prix des locaux, étape 12 (étude du 28 octobre, avant implémentation)**. Il n'existe pas de jeu
officiel et ouvert des loyers de bureaux par commune : la carte des loyers du ministère et de
l'ANIL (data.gouv.fr, loyers d'annonce par commune, en €/m² par mois charges comprises) ne couvre
que le logement ; les loyers tertiaires (Cushman & Wakefield, Geolocaux, Ayming) sont des études
privées, payantes ou partielles. La seule source officielle des prix de l'immobilier d'activité est
la base **DVF** (demandes de valeurs foncières, DGFiP, Licence Ouverte 2.0 sur data.gouv.fr,
actualisée en avril et en octobre) : ventes sur cinq ans, avec le type de local (« local industriel,
commercial ou assimilé ») et la surface bâtie.

| Donnée | Source | Ce qu'elle permet | Limite |
|---|---|---|---|
| Prix de vente au m² des locaux d'activité | DVF, locaux « industriel, commercial ou assimilé », médiane par commune sur les ventes des cinq dernières années, avec le nombre de ventes | Coût d'achat d'un local de `--surface` m² ; comparaison entre villes | Pas de bureaux isolés (le type de local mêle bureaux, commerces et entrepôts) ; Alsace, Moselle et Mayotte absentes ; communes à peu de ventes : repli sur le département, signalé |
| Loyer annuel et mensuel | **Non affiché** (décision du 28 octobre) : aucune source officielle ; une estimation par rendement brut a été écartée faute de source pour l'hypothèse | — | À rouvrir si une source officielle ou un partenaire fournit des loyers observés |

Rien n'est affiché pour une commune sans ventes : « non disponible », jamais un chiffre inventé.
À vérifier avant de coder : champs exacts du fichier DVF (type de local, surface réelle du bâti,
mutations à plusieurs lots), millésime le plus récent, et volume (plusieurs millions de lignes :
filtrer au bronze sur les locaux d'activité, comme pour `conso-sup36-region`).
Intégration prévue : extraction vers un Parquet bronze immuable (`dvf_locaux`, étape `locaux` de
`refresh`), silver nettoyé (mutations mono-local, valeurs aberrantes écartées), gold
`fct_prix_locaux_commune` (médiane, quartiles, nombre de ventes), ligne « Locaux » dans
`essentiel`, `diagnose` et `compare` (colonne par ville), limite dans `limites_methodologiques`.

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

## Refonte de la carte et direction artistique (étude du 28 octobre, à valider)

Proposition, rien n'est codé ni décidé. Elle garde les décisions en vigueur : ligne de commande,
local, page HTML autonome qui s'ouvre hors ligne (pas de site web, pas de CDN), données publiques
sourcées, « non disponible » plutôt qu'un chiffre inventé.

**Constat.** three.js est déjà au cœur de la carte (`reporting/carte.html`, grille hexagonale,
maquettes, deux vues) mais en **r128 (2021)** ; la version courante est la **r186** (septembre
2026). Depuis environ la r160, three.js ne publie plus de fichier unique à charger par `<script>` :
`three.module.js` et `three.webgpu.js` importent `three.core.js`, ce qui casse l'embarquement actuel
(`SCRIPTS` dans `reporting/carte.py`). Les nouveautés utiles : `WebGPURenderer` (repli WebGL 2
automatique), shaders en JavaScript (TSL), `BatchedMesh` pour des milliers de maquettes en un appel
de dessin. La vue par défaut « Raccorder » (zones RTE, sites clés en main, postes 400 kV) parle
déjà au persona des projets de 1 à 50 MW (pivot du 28 octobre) ; il lui manque de montrer ce que
la puissance demandée change : domaine de tension, gestionnaire, délai.

### Direction artistique : « le relevé »

Ce que l'outil promeut : une décision d'implantation d'un projet de 1 à 50 MW fondée sur des faits
publics, vérifiables, avec leurs limites, et une électricité bas-carbone ; le lecteur est un
développeur de data center, un opérateur de recharge, un industriel ou un logisticien, souvent
ingénieur, qui présente le site à un comité d'investissement. La direction qui en découle est celle d'une
**carte d'état-major moderne** : relief ombré à la manière des cartes IGN et suisses (Imhof), encre
et papier, chiffres en avant, chaque couche signée de sa source comme un cartouche. Ni jeu vidéo,
ni tableau de bord néon : la sobriété est le message (le carbone, la péréquation, la capacité non
publique se disent calmement). Références étudiées : le relief des cartes de Swissgrid et le
« Landscape of Power Flows » de Nadieh Bremer pour Swissgrid (données réseau traitées en paysage),
les cartes en relief de la presse de données.

| Élément | Proposition | Pourquoi |
|---|---|---|
| Nom | « Où brancher mon entreprise » partout ; « Brancher ici ? » réservé au verdict | La carte s'appelle aujourd'hui « Atlas électrique », sa légende « Où se brancher » : trois noms pour un outil |
| Fond | Papier clair (`#F4F1EA`), encre bleu nuit (`#14202B`) ; mer en bleu gris désaturé ; relief en ombrage gris chaud, pas de cônes | Même identité que le PDF (fond clair) ; lisible imprimé et en capture pour un comité de direction |
| Accent | Un seul : vert « décarboné » (`#2E7D5B`) pour ce qui est favorable et sourcé ; orange brûlé (`#C2571A`) pour « à vérifier » | Le vert porte le message bas-carbone ; l'or et le parchemin actuels évoquent un jeu de stratégie |
| Note des cases | Échelle divergente orange brûlé → blanc → bleu-vert, contrôlée pour le daltonisme (deutéranopie, protanopie) | Le rouge → vert actuel est illisible pour environ 8 % des hommes |
| Typographie | **Spectral** (Production Type, Paris ; SIL OFL) pour les titres, **Inter** (OFL, chiffres tabulaires) pour le texte et les nombres ; embarquées en base64 dans la page et enregistrées dans reportlab pour le PDF | Trajan Pro n'est installé presque nulle part (rendu différent d'un poste à l'autre) ; Helvetica du PDF n'a pas « ₂ » ni « ≥ », d'où la table de remplacement de `reporting/implantation_pdf.py` |
| Maquettes | Gardées (centrales, fret, sites) mais en « maquette d'architecte » : blanc cassé mat, une seule couleur de filière en touche, ombres douces | Elles restent la signature de la carte ; moins de couleurs, plus de lisibilité |
| Mouvement | Une arrivée de caméra (France → adresse, 1,5 s), transitions entre vues ; tout coupé sous `prefers-reduced-motion` | L'effet « avant-gardiste » vient de la mise en scène, pas de l'accumulation d'effets |
| Interdits | Ni Marianne, ni bloc-marque, ni police Marianne du système de design de l'État | Réservés à l'État (charte de la marque État, loi de 1918 sur les sceaux) : l'outil n'est pas un service public |

Source unique des couleurs et polices : un module `reporting/charte.py` (constantes), injecté dans
`carte.html` en variables CSS et lu par `pdf.py`, `charts.py` et `implantation_charts.py`, au lieu
des trois jeux de couleurs actuels (navy et or de la carte, gris et bleu du PDF, palette Tempo).

### three.js : ce qui sert le persona

Chaque effet doit répondre à une question du persona ; sinon il n'entre pas.

| Fonction | Rendu three.js | Question servie | Données |
|---|---|---|---|
| « Raccorder » selon la puissance | Autour de l'épingle, un anneau par domaine de tension (HTA, zone grise, HTB) teinté selon `--puissance` ; zones RTE en colonnes de lumière dont la hauteur dit l'année de capacité ; contraintes RTE en voile sur les cases | Qui raccorde mes 12 MW, et quand ? | existant (`seuils_raccordement`, SDDR, contraintes) |
| Capacité en soutirage (étape 19) | Cases teintées par capacité restante dès que RTE la publie ; d'ici là, rien d'inventé | La puissance est-elle là ? | RTE, 1er janvier 2027 |
| `compare` sur la carte | Plusieurs épingles numérotées ; caméra qui passe d'un site à l'autre ; fiches côte à côte, **sans classement** (décision du 15 octobre) | Le persona présélectionne 2 à 6 sites | existant |
| Foncier et contraintes (étapes 14, 15) | Sites clés en main, friches et zones d'activité en maquettes ; ICPE et Seveso en marqueurs discrets | Le terrain est-il prêt et constructible ? | étapes 14 et 15 |
| Desserte (étape 13) | Temps vers l'échangeur, le port, l'aéroport, le rail-route, tracés en arcs depuis l'épingle | Logistique et industrie | étape 13 |
| Relief continu | Un maillage de terrain par système électrique tiré de l'altimétrie IGN déjà chargée, ombrage calculé dans le shader, hexagones projetés dessus en filet | Remplace les cônes de montagne ; donne la lecture « carte d'état-major » | existant (`ingestion/hexagones.py`) |
| Carbone heure par heure (Corse, outre-mer) | Curseur de 24 h : lumière du soleil et couleur du réseau suivent `fct_carbone_horaire_zni` | Seul cas où l'heure de consommation change le carbone | existant |
| Export d'image | Bouton « Image » : PNG haute définition de la vue, avec cartouche (adresse, date, sources) | La carte finit dans un support de comité | — |
| Sans WebGL | La fiche « Brancher ici ? » et les critères s'affichent quand même, carte remplacée par un message | Aujourd'hui un poste sans WebGL n'a qu'un message d'erreur | — |

Hors de propos : globe terrestre, particules, néons, post-traitements lourds (bloom, profondeur de
champ), WebXR. `WebGPURenderer` n'est retenu que si le `WebGLRenderer` de la r186 ne tient pas
60 images/s sur un portable de bureau ; TSL seulement pour l'ombrage du relief.

### Embarquer three.js r186 hors ligne

La page doit rester un fichier unique, ouvert en `file://` par `essentiel` et `diagnose`. Les
modules ES ne se chargent pas en `file://` par une URL relative, et une carte d'import vers des
URL `data:` n'est confirmée par aucune source : à prouver par un prototype avant tout.

1. **Prototype (une demi-journée)** : `three.core.js`, `three.module.js` et `OrbitControls.js`
   (r186, licence MIT) dans `reporting/vendor/`, insérés en texte par `carte.py` ; un amorçage de
   quelques lignes crée une URL `blob:` par module, réécrit les imports relatifs vers ces URL, puis
   `import()`. Critère : Chrome, Edge et Firefox à jour, en `file://`, sans serveur ni réseau.
2. Repli si le prototype échoue : un paquet unique déjà assemblé (IIFE) versionné dans
   `reporting/vendor/`, avec la commande qui l'a produit dans le README ; l'outil d'assemblage
   (Node) ne sert qu'à la mise à jour de three.js, jamais à l'exécution.
3. Dernier recours : rester en r128 et appliquer seulement la direction artistique.

La version est figée dans le nom du fichier vendu (`three-r186.core.js`) ; un test vérifie que la
page générée ne contient aucune URL `http`.

### Lots, dans l'ordre

Chaque lot n'est coché qu'après ruff, pytest (dont `dbt build`), un essai réel de `carte` sur
Lyon, Ajaccio et Saint-Denis de La Réunion, et une capture mise à jour dans `docs/exemples/`.

| Lot | Contenu | Validation |
|---|---|---|
| 0 | **Cohérence** (liste ci-dessous) : nom, exemples du README, captures, ordre des étapes | relecture README et PLAN, `essentiel` sur les exemples |
| A | `reporting/charte.py` ; polices embarquées ; PDF et graphiques sur la charte ; carte sur la charte en r128 | rendu identique sur deux postes ; PDF sans caractère remplacé |
| B | Prototype puis passage à three.js r186 (`WebGLRenderer`, `BatchedMesh` pour les maquettes) | carte sans requête réseau ; 60 images/s sur la métropole |
| C | « Raccorder » selon la puissance : anneaux des domaines de tension, zones RTE en colonnes datées | 1, 12, 30 et 50 MW à Chilly-Mazarin et Dunkerque : gestionnaire conforme à `seuils_raccordement` |
| D | `compare` épinglé sur la carte ; export d'image ; fiche sans WebGL | `compare` à 3 sites |
| E | Relief continu et ombrage ; curseur horaire des îles | Ajaccio : 522 g vers 13 h, 681 g vers 5 h |
| F | Couches des étapes 13, 14, 15 et 19 au fil de leur arrivée | une adresse contrôlée à la main par couche |

### Écarts de cohérence relevés le 28 octobre (lot 0)

- **Exemple du README** : `--puissance 50 --conso 350` décrivait 50 MW pour 350 MWh par an, soit
  moins de 0,1 % d'utilisation ; remplacé le 28 octobre par 12 MW et 70 000 MWh (sortie réelle).
- **Captures et PDF de Lyon** (`docs/exemples/`) : faits pour des bureaux de 100 kVA ; à refaire
  pour un projet de 1 à 50 MW, avec la légende du README.
- **Courbe d'exemple** `courbe_bureau_septembre.csv` : une courbe de bureau ; `--courbe` ne chiffre
  le coût qu'à 36 kW au plus. Fournir une courbe d'exemple HTA et étendre le coût au-delà (étape 10.5).
- **Profil par défaut** : sans `--conso`, la tranche IA (décision du 16 octobre) ; pour ce persona,
  déduire la consommation de `--puissance` et du profil d'activité (étape 18).
- **Trois noms** : « Atlas électrique » (titre et bandeau de la carte), « Où se brancher » (légende),
  « Où brancher mon entreprise » (projet).
- **Deux identités visuelles** : carte bleu nuit, or et Trajan ; PDF et graphiques gris, bleu et
  Helvetica.
- **Bandeau d'aide** de la carte (« Glisser pour se déplacer… ») illisible sur les cases claires
  (capture de Lyon).
- Corrigés le 28 octobre avec le pivot : ordre du tableau « Suite » (il se lisait 0, 13, 14… 4, 5),
  classement pondéré de l'étape 8 retiré (contraire à la décision du 15 octobre).
- Vérifié sans écart : 473 tests collectés, comme annoncé dans le README.

**À décider avant le lot A** : fond clair (proposé) ou sombre actuel ; Spectral et Inter ou une
autre paire sous licence OFL ; échelle de note daltonien-compatible à la place du rouge → vert
(décision du 26 octobre à remplacer).

## Ce que les études ont établi (9 et 15 octobre)

- **Les besoins des entreprises**, dans l'ordre : de la puissance disponible à une date connue
  (premier critère des gros projets ; zones RTE P1 dès fin 2028, P2 dès 2029) ; du foncier
  viabilisé ; une électricité bas-carbone ; un prix prévisible, qui ne décide que pour les
  électro-intensifs et les data centers ; la connaissance des risques du site.
- **Persona des bureaux (recherche du 28 octobre, abandonné le même jour pour les projets de 1 à
  50 MW)** : pour des bureaux, le raccordement est rarement un sujet
  (un local existant équipé d'un Linky s'ouvre en un jour ouvré environ ; un local neuf coûte de
  426 € à 10 000 € d'extension, sources commerciales à confirmer) ; le tarif bleu exige moins de
  10 salariés, 2 M€ de chiffre d'affaires et 36 kVA au plus (fiche CRE d'EDF, juillet 2026). Aucune
  source ouverte trouvée sur les loyers de bureaux, ni sur la consommation d'une PME de bureaux
  (seul repère : 119 kWh/m²/an pour des bureaux neufs d'Île-de-France, DRIEAT).
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
| 24 oct. | Une étape n'est validée qu'après un test complet : ruff, pytest (dont `dbt build`), essai réel |
| 24 oct. | `--va` : électro-intensité = consommation × accise au tarif normal (26,58 €/MWh au 1er février 2026) ÷ valeur ajoutée, paliers 0,5 %, 3,375 % et 6,75 % (CIBS L312-65, L312-71) ; montant du taux réduit non chiffré |
| 24 oct. | `--courbe` : export Enedis lu en Python à la demande, jamais stocké ; horodate = fin du pas ; tient lieu de `--conso` et `--puissance` s'ils manquent ; coût au barème HP/HC en vigueur, seulement jusqu'à 36 kW |
| 24 oct. | Carbone horaire des îles : profil de la part thermique calé sur le facteur ADEME annuel, plutôt que des facteurs par filière non sourcés ; imports de la Corse comptés non thermiques |
| 24 oct. | UE-27 : moyennes nationales seulement (Eurostat depuis 2024-S1, deux derniers semestres ; Ember, dernière année, production sans échanges) ; jointure des trois codes pays par le seed `dim_pays_ue` ; hors UE refusé |
| 24 oct. | Commandes de question : journal masqué (niveau WARNING, message seul), chemins des fichiers écrits sur stderr ; warehouse absent ou gold incomplet → `DonneesAbsentesError` et commande `refresh` à lancer |
| 24 oct. | Carte : décor (villes, forêts) et lentilles Terrain et Réseau retirés ; prix et compteurs hors de la barre du haut ; la fiche de l'adresse donne le verdict |
| 25 oct. | Carte : deux vues au lieu de quatre ; « Raccorder » note chaque case de -1 à +1 (zone RTE à moins de 3 cases, site clé en main à moins de 2, poste 400 kV, contraintes RTE au-delà de la médiane, relief, carbone au-dessus de 100 g, équilibre régional), note indicative ; « Territoire » réunit industrie (hauteur), équilibre (couleur, violet → bleu pour ne pas se confondre avec le rouge et le vert de la note) et production (maquettes) |
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
| 26 oct. | Carte : fausses couleurs réservées à « Raccorder » (rouge → blanc → vert, sans cônes de montagne) ; « Territoire » revient aux couleurs réelles et au relief IGN, l'encart donne carbone et prix moyen par tranche (`fct_prix_entreprise_tranche`) ; frontières régionales en gris sombre plutôt qu'en couleurs |
| 26 oct. | Carte : sites clés en main en maquette posée sur la case (dalle, hangar, bureaux, mât au drapeau doré) au lieu d'un losange flottant ; centrales en maquettes aussi dans « Raccorder » ; survol d'un site : son nom |
| 26 oct. | Acheminement : TURPE 7 HTA-BT seulement (HTA à pointe fixe, BT > 36 kVA ; le tarif bleu l'inclut déjà, le HTB relève de RTE) ; une même puissance souscrite dans toutes les classes ; énergie répartie sur la courbe de charge, sinon sur toutes les heures de l'année ; heures creuses 22 h – 6 h, pointe 9 h – 11 h et 18 h – 20 h de décembre à février hors dimanche ; version courte ou longue utilisation au moindre coût ; gestion en contrat unique (Rf inclus), comptage communicant. Montant donné comme part déjà comprise dans le prix moyen Eurostat |
| 26 oct. | Zonage sismique : couche WFS de Géorisques plutôt qu'un seed (le tableur de planseisme.fr n'est plus en ligne, le fichier wpd reste écarté) ; zone trouvée par l'adresse dans son polygone, ce qui évite le recalage sur le code officiel géographique ; pages triées par `gid` (sans tri, 84 polygones perdus) et nombre de polygones contrôlé contre le total annoncé ; sur une limite, la zone la plus forte ; le critère « risques » du verdict reste réglé par les seuls plans de prévention |
| 26 oct. | Continuité d'alimentation : jeu d'Enedis, producteur, plutôt que la copie de l'Agence ORE ; colonnes d'années lues dans le schéma du jeu (une de plus par publication) ; comparaison au seuil de 3 % de l'arrêté du 24 décembre 2007 (article 3) et à la médiane des départements publiés la même année ; repère dans la section « Raccordement » de `diagnose`, sans entrer dans le verdict (moyenne départementale, pas la ligne du site) |
| 26 oct. | Pôles de fret : ports sur les marchandises hors tare de la dernière année publiée (SDES), seuil 5 Mt sur le continent et 0,5 Mt en Corse et outre-mer ; aéroports sur le fret et la poste de la dernière année complète (DGAC), 20 000 t et 3 000 t ; chantiers rail-route de SNCF Réseau sur au moins 2 km de voies de manutention, dit repère de capacité (aucun tonnage publié) ; HAROPA placé au Havre ; ports et aéroports au centre de leur commune ; ports fluviaux écartés (pas de maille port). Carte seulement, sans ligne dans `limites_methodologiques` (réservées aux chiffres de `diagnose`) |
| 26 oct. | Seeds recréés à chaque `dbt build` (`+full_refresh`) : un seed qui gagne une colonne bloquait le warehouse existant |
| 28 oct. | **Pivot du persona** : les projets de **1 à 50 MW** (data centers, bornes de recharge, industrie, sites logistiques), modèle de Yottar ; le persona des bureaux est abandonné (le prix de l'électricité, identique partout, ne départage pas leurs villes). L'ordre des étapes suit désormais : puissance et délai, coût complet (TURPE HTB, au-delà de 150 GWh, accise selon l'activité), foncier constructible, risques et eau, desserte. Les projets de plus de 50 MW restent servis |
| 28 oct. | Prix de vente des locaux d'activité (étape 12, DVF) gardé en information, plus prioritaire ; **pas de loyer** (aucune source officielle et ouverte) ; « non disponible » sans ventes, jamais de chiffre inventé |
| 28 oct. | DVF : ventes de locaux d'activité d'un seul bien (ni logement, ni plusieurs dispositions), surfaces de 15 à 5 000 m² et prix de 100 à 15 000 €/m² (variables dbt `locaux_*`) ; médiane sur les cinq dernières années de ventes, fiable à partir de 5 ventes (`locaux_min_ventes`), sinon le département ; arrondissements de Paris, Lyon et Marseille ramenés à la commune ; fichiers annuels lus en flux (~100 Mo chacun), le pilote Spark passe à 3 Go (400 000 lignes saturaient 1 Go) ; Bourg-en-Bresse 1 179 €/m², Lyon 3 298, Paris 7 760 |
| 28 oct. | Commentaires réservés à la logique du code (règle, piège d'une source, raison d'un choix) : plus d'en-tête d'utilité au-dessus de chaque fonction ni de chaque modèle dbt ; code le plus simple possible, en Python, PySpark et dbt |
| 28 oct. | Consolidation : `essentiel`, `diagnose` et `compare` partagent les mêmes options de projet (`--puissance`, `--conso`, `--ca`, `--va`, `--surface`, `--courbe`) et les mêmes messages d'erreur ; essai de bout en bout après `refresh` complet |
| 28 oct. | Identifiants de l'application Ecowatt dans `identifiants.local.txt` (ignoré par git), sous `ECO2MIX_ECOWATT_*`, distincts de ceux de Tempo ; le secret a transité par la conversation : à régénérer si elle est partagée |

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
