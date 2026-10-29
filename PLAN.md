# Plan du projet

> Mis à jour le 29 octobre 2026. Périmètre : la France (métropole, Corse, outre-mer), en ligne de
> commande, en local et à la demande.

Ce fichier porte l'état du projet, les étapes qui restent, les recherches qui les justifient et
les décisions en vigueur ; le [README](README.md) dit comment s'en servir. Sections, dans l'ordre :
persona, état (ce qui existe), suite (ce qui reste, en trois lots), refonte de la carte, recherches
documentaires, décisions, sujets écartés. Une étape terminée quitte « Suite » et devient une ligne
de « État » (skill `cloturer-etape`).

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

**Exemple de référence** des essais réels : 12 MW et 70 000 MWh par an au 3 allée des Peupliers,
91380 Chilly-Mazarin (HTA, tranche IF, environ 5,3 M€ HTVA par an dont 1,4 M€ d'acheminement,
vérifié le 28 octobre). Au-delà de 40 MW : 45 MW et 300 000 MWh (HTB 1, 3,83 M€ d'acheminement
par an, vérifié le 29 octobre).

## État

Le MVP est atteint depuis le 16 octobre. `essentiel` donne en 1,5 s environ le prix moyen et le
verdict carbone ; il répond juste sur 11 adresses de recette, de Paris à Mamoudzou (test
`test_recette_mvp.py`). Depuis le pivot du 28 octobre, `diagnose` couvre aussi le foncier
(urbanisme, friches, zones d'activité), l'eau, la desserte et le solaire de l'adresse, et le
voisinage du site (argiles, cavités, Seveso, installations classées, sols pollués).

| Brique | Où |
|---|---|
| Ingestion PySpark (typage, UTC, clés du lot), écriture Arrow vers le bronze | `ingestion/transform.py`, `ingestion/spark.py` |
| Adresse → commune, département, région ; adresse ambiguë ou approximative refusée | `ingestion/geocode.py` (BAN) |
| Prix par tranche Eurostat, scénarios à 3 ans calibrés sur les erreurs passées | `fct_prix_entreprise_tranche` |
| Carbone par système électrique, seuil de 100 gCO₂e/kWh | `fct_carbone_territoire` |
| Carbone heure par heure en Corse et outre-mer (production EDF SEI, profil calé sur l'ADEME) | `fct_carbone_horaire_zni` |
| Part acheminement de la facture (TURPE 7 HTA-BT et HTB 1 à 3 au 1er août 2026) selon le domaine de tension, la puissance et la consommation, sur la courbe de charge si elle est fournie ; HTB dès 40 MW, HTB 1 par défaut | `ingestion/turpe.py`, seeds `turpe_soutirage`, `turpe_gestion_comptage` |
| Part de la facture dans le chiffre d'affaires (`--ca`) et électro-intensité fiscale dans la valeur ajoutée (`--va`, accise au tarif normal de 26,35 €/MWh) | `essentiel`, `diagnose`, seed `accise_electricite` |
| Activité du projet (`--activite data-center\|irve\|industrie\|logistique`) : profil de charge de l'acheminement et régime d'accise de l'activité | `essentiel`, `diagnose` |
| Accise au tarif réduit de la loi de finances 2026, face au tarif normal : 10 €/MWh au-delà du premier GWh pour un data center (refusé si `--va` montre moins de 2,25 % d'électro-intensité), 5,5, 3 ou 0,5 €/MWh selon le palier pour l'industrie ; montant, économie et conditions, dans les deux commandes, le verdict et le JSON | `accise_reduite`, `ligne_accise`, seed `accise_electricite` |
| Courbe de charge Enedis (`--courbe`) : consommation, pointe, heures creuses, coût (jusqu'à 36 kW) et carbone réels | `ingestion/courbe.py` |
| Diagnostic complet : industrie, réseau, zone RTE, site clé en main, risques | `diagnose`, `compare` |
| Entreprises raccordées à Enedis (2025) : volume, industrie et premier secteur NAF de la commune, rang dans le département ; consommation déjà relevée à l'adresse (« Déjà raccordé ici ») | `fct_conso_entreprises_commune`, `fct_conso_entreprises_adresse`, seed `dim_naf2` |
| Verdict « brancher ici ? » en cinq critères (raccordement, risques, carbone, facture, foncier), dans `essentiel`, `diagnose`, `compare` et sur la carte | `verdict`, `phrase_verdict` |
| Carte 3D épurée autour du verdict : vue « Raccorder » par défaut, adresse reliée à la zone RTE utile et au site clé en main ; épinglée par `essentiel` et `diagnose` (seule l'épingle bouge si les données n'ont pas changé) | `carte`, `reporting/carte.py` |
| Une carte par système électrique (métropole et Corse, chaque DROM), ouverte sur celle de l'adresse ; deux vues : « Raccorder », cases notées du rouge au blanc puis au vert, et « Territoire », paysage réel (mer bleue, verdure, relief IGN) avec carbone et prix moyen dans l'encart ; dans les deux vues, 95 pôles de production du registre national en maquettes par filière, et les sites clés en main en maquette, nommés au survol | `ingestion/hexagones.py`, `fct_sites_production` |
| Grands pôles de fret dans les deux vues de la carte, en maquettes (port à portique, aéroport et avion cargo, chantier rail-route) : 34 pôles en 2025 au-dessus des seuils de la décision du 26 octobre | `ingestion/extract_fret.py`, `fct_poles_fret`, seeds `dim_ports_fret` et `dim_aeroports_fret` |
| Risques sans dépendre de l'API Géorisques : export GASPAR chargé par `refresh` | `fct_risques_commune` |
| Zone de sismicité réglementaire (1 à 5) à l'adresse, sans l'API : couche WFS de Géorisques chargée par `refresh`, point dans polygone en Python | `ingestion/extract_sismique.py`, `fct_zonage_sismique` |
| Qualité de fourniture du département dans `diagnose` : part des clients Enedis au-delà des seuils réglementaires de coupure (dernière année, moyenne sur cinq ans, médiane des départements, seuil de 3 %) ; « non publiée » hors Enedis | `fct_continuite_departement`, `ligne_continuite` |
| Risques « à vérifier » seulement sous un plan de prévention approuvé ou prescrit (13 919 communes au lieu des 31 706 qui ont un risque recensé) | `fct_plans_prevention_commune`, `_critere_risques` |
| Zone d'urbanisme de l'adresse (U, AU, A ou N, document et règlement) dans `diagnose` et `compare`, et dans le critère « foncier » du verdict (« à vérifier » en zone A ou N) ; appel à la demande, sans stockage | `ingestion/urbanisme.py`, API Carto module GPU |
| Friches de 1 ha ou plus et pas reconverties (Cartofriches, Cerema, Licence Ouverte v2) : nombre à moins de 10 km et les trois plus proches, avec type, surface, zone d'urbanisme et statut, dans `diagnose` et `compare` (détail du critère « foncier ») | `ingestion/extract_friches.py`, `friches`, `fct_friches` |
| Arrêtés de restriction des usages de l'eau en vigueur à l'adresse (VigiEau : niveau le plus haut parmi les eaux superficielles, souterraines et potable, date de fin) dans `diagnose` et `compare` ; appel à la demande | `ingestion/eau.py` |
| Zones d'activité et industrielles de la BD TOPO à moins de 5 km (nombre, trois plus proches avec surface, adresse dans la zone ou non) dans `diagnose` et `compare` ; appel WFS à la demande | `ingestion/zones_activite.py` |
| Accès routier : temps en voiture (Géoplateforme) vers le port, l'aéroport et le chantier rail-route de fret les plus proches du même territoire, et distance à vol d'oiseau jusqu'à l'autoroute la plus proche (BD TOPO), dans `diagnose` et `compare` | `ingestion/acces_routier.py` |
| Bâtiment de l'adresse (BD TOPO : usage, emprise au sol, hauteur) et productible photovoltaïque d'un kWc (PVGIS : kWh par kWc et par an, inclinaison optimale), dans `diagnose` et `compare` ; appels à la demande | `ingestion/solaire.py` |
| Date de chargement de chaque source dans toutes les sorties ; pied de page des rapports avec la commande pour les refaire | `ligne_chargements`, `pied_de_page` |
| Prix de vente des locaux d'activité : médiane et quartiles au m² par commune (repli sur le département sous 5 ventes), cinq dernières années de ventes DVF (393 099 ventes de 2021 à 2025 chargées, 219 564 retenues) ; coût d'achat avec `--surface` ; affiché pour information, aucun loyer | `ingestion/extract_dvf.py`, `ventes_locaux`, `fct_prix_locaux`, `ligne_locaux` |
| Pays de l'UE-27 : prix Eurostat et carbone Ember du pays, face à la France à tranche égale ; pays trouvé par Nominatim | `europe`, `fct_pays_ue` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |
| Rapport mensuel carbone et créneaux Tempo, sur le calendrier réel de l'API RTE (vérifié le 25 octobre sur septembre 2026 et janvier 2026) | `tempo`, `report` |
| Profils de charge réels des points de plus de 250 kVA, industrie et tertiaire (Enedis, 12 mois, 843 072 demi-heures chargées le 29 octobre) : journée type pondérée par le nombre de points, chiffrée heure par heure comme le profil du seed | `ingestion/extract_enedis.py`, `profils_charge_sup36`, `profils_charge_reels`, `fct_cout_horaire_profil` |
| Signal Ecowatt (J à J+3) relevé à la demande par `ecowatt`, jours orange et rouges dans le rapport mensuel | `RteEcowattClient`, `rte_ecowatt`, `ecowatt_jours`, `ligne_ecowatt` |
| Modèle en étoile : 3 dimensions (`dim_commune`, `dim_date`, `dim_risque`) et 4 faits (conso des entreprises, prix des locaux, risques, mix journalier), clés étrangères testées sur le warehouse réel | `dbt/models/etoile/` |
| Exemples du README sur le persona : captures des deux vues et PDF de `diagnose` pour 12 MW et 70 000 MWh à Chilly-Mazarin, courbe d'exemple HTA de 12 MW (journée type Enedis de l'industrie mise à l'échelle) ; un seul nom, « Où brancher mon entreprise », sur la carte | `docs/exemples/` |
| Voisinage du site (étape 15, 29 octobre), à la demande par l'API Géorisques : argiles à l'adresse, cavités à moins de 500 m, établissements Seveso en exploitation à moins de 5 km (distance), installations classées sous autorisation ou enregistrement à moins de 1 km, sols pollués à moins de 500 m (SIS, SUP, sites en instruction, CASIAS) ; à Chilly-Mazarin : argiles fortes, SMCA (Seveso seuil bas) à 4,8 km, 2 installations classées, 11 anciens sites CASIAS | `GeorisquesClient.voisinage`, `completer_voisinage`, `lignes_voisinage` |
| Sortie réduite à la réponse (journal sous `--verbose`) ; avant tout chargement, la commande `refresh` à lancer | `ingestion/cli.py` |

**À surveiller** :
- **Géorisques** : l'API, coupée du 24 au 28 octobre, répond de nouveau. Le 29 octobre au soir,
  depuis Python (`httpx`), les points du voisinage (`installations_classees`, `ssp/*`, `rga`,
  `cavites`) répondent en moins d'une seconde ; le rapport à l'adresse
  (`resultats_rapport_risque`) reste instable (un 200 en 11 s, puis « Server disconnected ») et
  `urllib` est toujours coupé. Le repli sur l'export GASPAR (maille commune) et sur le zonage
  sismique WFS joue ; l'inondation à l'adresse n'est donnée que quand le rapport répond. Taille de
  page maximale de l'API : 1 000 ; `rga` répond 200 sans corps hors carte (La Réunion, Ajaccio).
  planseisme.fr ne résout plus ;
- les facteurs ADEME de la Corse et de l'outre-mer datent de décembre 2020 : vérifié le 25
  octobre, c'est toujours le dernier millésime valide de la Base Carbone (jeu mis à jour le 3
  juillet 2025) ;
- la fourchette de prix à 3 ans reste indicative tant que le contrôle sur le passé compte moins
  de 20 périodes (8 aujourd'hui, chevauchantes, toutes marquées par la crise de 2022) ;
- les prix Tempo (seed `tarifs_tempo`) ne remontent qu'au 1er février 2026 : un rapport plus ancien
  classe les créneaux sur le seul carbone ;
- l'accise au tarif normal (26,35 €/MWh depuis le 1er août 2026, révisée deux fois en 2026) vit
  dans le seed daté `accise_electricite` : à compléter à chaque indexation, que `--va` signale une
  fois le 1er février passé ;
- DVF 2026 n'est pas encore publié (dernière vente chargée : décembre 2025) : relancer `refresh`
  après chaque actualisation d'avril et d'octobre ;
- EDF SEI publie jusqu'à fin 2024 seulement (2021-2024 « estimé »), sans Mayotte ; le jeu a
  quitté `opendata-corse-outremer.edf.fr` pour `opendata.edf.fr` (API data-fair).

## Suite

Les étapes gardent leur numéro d'origine (l'ordre de prise est la colonne « Ordre »). Chacune
n'est cochée qu'après ruff, pytest (dont `dbt build`) et un essai réel sur un projet de 1 à 50 MW
(`python .claude/scripts/verifier.py`). Trois lots : A, à prendre maintenant ; B, en attente d'une
source, d'une date ou d'une décision ; C, présenter le projet à un incubateur.

**Ordre conseillé** : C21 (phrase de mission, sans code) ; C25 (démonstration rejouable, que les
exemples du 29 octobre préparent) ; le lot A dans l'ordre ; puis les entretiens C23 **avant** les pistes nouvelles, que leurs
réponses peuvent réordonner.

### Lot A : à prendre maintenant

Les sources existent ; il reste à lire un texte, à tester une API ou à finir une étape commencée.

| Ordre | Étape | Reste à faire | Pourquoi pour ce persona | À vérifier avant de coder |
|---|---|---|---|---|
| 1 | **17** TURPE 7 HTB (grille HTB 1 à 3 faite le 29 octobre) | Calendrier horosaisonnier HTB (supposé identique à la HTA), composante d'injection-soutirage, alimentations de secours | Au-delà de 40 MW, l'acheminement est chiffré sur une hypothèse | Délibération CRE 2025-77 et 2026-104 (annexe 2) |
| 2 | **16** Bâtiment et solaire (emprise BD TOPO et productible PVGIS faits le 29 octobre) | Obligations de solarisation des parkings et toitures de la loi APER, échéances reportées par la loi du 26 novembre 2025 ; structure des toitures | Autoconsommation d'un entrepôt ou d'un hub de recharge ; obligation légale du site | Texte de la loi du 26 novembre 2025 ; licence de la BDNB |
| 3 | **20** Eau (arrêtés VigiEau faits le 29 octobre) | Tension sur la ressource : débits (Hub'Eau hydrométrie) ou niveaux de nappe (Hub'Eau piézométrie) rapportés à une référence publiée ; les volumes BNPE ne disent pas la disponibilité | Refroidissement des data centers ; procédés industriels | Référence officielle de tension (seuils, débits d'étiage) et licence ; maille |

Fins d'étapes closes, gardées en limite plutôt qu'en étape : l'étape 18 est terminée (le tarif
réduit des secteurs exposés à la concurrence internationale, fixés par arrêté, n'est pas
identifié par l'outil) ; l'échangeur autoroutier de
l'étape 13 n'est pas identifiable dans la BD TOPO (bretelles sans classement) et la Géoplateforme
n'a pas de profil poids lourd ; l'étape 14 (urbanisme, friches, zones d'activité) est terminée.

**Manques repérés et pistes** (recherche du 29 octobre ; à trier après les entretiens C23, rien
n'est décidé) :

| Manque | Piste | Condition |
|---|---|---|
| Taux de charge réel : `--conso` doit être donnée, rien n'est déduit de `--puissance` (un data center n'utilise qu'environ 20 % de la puissance demandée, chiffre relayé) | Consommation par défaut déduite de `--puissance` et `--activite`, dite hypothèse | Une source officielle du taux de charge par activité ; sans elle, rien |
| Coût du raccordement, dont la réfaction des bornes de recharge (part prise en charge par le tarif) | Ordre de grandeur du coût de raccordement selon le domaine de tension | Barème de raccordement d'Enedis et texte de la réfaction (arrêté et délibération CRE) ; rien sans texte |
| Débouché de la chaleur fatale, condition de l'accise réduite des data centers (L312-70) | Réseaux de chaleur à proximité | Jeu national des réseaux de chaleur : producteur et licence à vérifier |
| Zones « prêtes à raccorder » de RTE (ports de Dunkerque, du Havre, de Fos et cinq zones moyennes) | Une couche de plus dans « Raccorder » | Périmètres publiés par RTE |
| Maturité du projet pour la file « premier prêt, premier servi » | Pièces d'un dossier de raccordement (autorisations, maîtrise du foncier) en liste de vérification | Relecture de la procédure RTE de 2026 et de la délibération CRE 2026-32 |
| Artificialisation (zéro artificialisation nette) de la commune | Ligne « enveloppe restante » du guide de la DGE | Portail de l'artificialisation des sols (Cerema), licence |
| Besoin de recharge poids lourds au dépôt | Situer l'adresse dans une zone logistique à fort besoin de puissance (étude Enedis de décembre 2025) | Maille des zones logistiques (SDES), licence |
| Version 2 de l'étude du 28 octobre | Photovoltaïque existant (OpenPVMapper, CC-BY, ~75 % de précision), bassin d'emploi (isochrone et flux domicile-travail INSEE), détection de changement sur l'ortho | Licences du LiDAR HD et de l'OCS GE |
| Version 3 de l'étude du 28 octobre | Projections climatiques (DRIAS, Explore2) | Licence de DRIAS ; date de la carte RGA 2026 |

### Lot B : en attente

Rien à coder tant que la condition n'est pas levée.

| Étape | Pourquoi pour ce persona | Condition pour s'y mettre |
|---|---|---|
| **19** Capacité d'accueil en soutirage : carte des capacités de RTE, nouveau Caparéseau ; ce qu'Enedis publie en HTA | Première question du persona, sans réponse publique aujourd'hui | Publication RTE attendue au 1er janvier 2027 (délibération CRE 2026-173) ; capacité Enedis réservée au portail client |
| **Carte** Refonte (lots A à F, voir plus bas) | Une carte lisible imprimée, qui montre ce que la puissance demandée change | Décisions de direction artistique (fond, polices, échelle de note) ; lot 0 fait le 29 octobre |
| **S** Ambiance sonore de la tuile (demande du 29 octobre) : un son discret qui rappelle le facteur le plus marquant de la case (par exemple un bourdonnement électrique si le nucléaire domine), aucun s'il n'y a pas de facteur dominant | Rendre la carte plus parlante sans texte de plus ; le son n'est jamais une information en soi | Après le lot A de la carte. Prérequis : règle du facteur dominant écrite à partir des critères déjà calculés, « aucun » possible ; sons libres de droits (CC0 ou domaine public, licence et auteur notés dans le dépôt) ; lecture après un geste de l'utilisateur, volume bas, bouton de coupure, sans effet sur le rapport imprimé ; vérification par le skill `essai-carte` |
| **8** Repère de prix au-delà de 150 000 MWh/an, après la fin de l'ARENH | Un projet de 20 MW constant dépasse la dernière tranche Eurostat (IG, sans borne) | Une source officielle et ouverte d'un prix à terme ; le classement pondéré dans `compare` reste écarté (décision du 15 octobre) |
| **6** Extension internationale, voir plus bas | En dernier (décision du 25 octobre) | Jeton ENTSO-E et contours des zones de prix |

### Lot C : présenter le projet à un incubateur

Recherche du 29 octobre, détail dans « Ce que demandent les incubateurs ». Étapes surtout hors
code ; seules 21 et 25 touchent au dépôt et suivent la règle des trois vérifications. Le dossier,
le pitch et le Lean Canvas restent hors du dépôt (documentation limitée à README et PLAN) ; seules
leurs conclusions reviennent ici.

| Ordre | Étape | Ce que l'incubateur demande | Application à l'outil |
|---|---|---|---|
| 1 | **21** Phrase de mission et « pourquoi maintenant » en tête du README | Une phrase de mission, pas une liste de fonctions ; problème, solution, pourquoi maintenant (plan Sequoia) | Une phrase du type « dire en une commande si un projet de 1 à 50 MW peut se brancher à une adresse, quand et à quel coût » ; « pourquoi maintenant » appuyé sur des sources P : 33 GW en file d'attente (RTE, juillet 2026), file « premier prêt, premier servi » d'ici fin 2026, carte des capacités en soutirage attendue le 1er janvier 2027 |
| 2 | **22** Lean Canvas en neuf blocs, chaque bloc écrit comme une hypothèse à tester | Format recommandé par Bpifrance Création pour les projets qui avancent par itération | Segments à départager : porteurs de data centers, opérateurs de recharge poids lourds, industriels, foncières logistiques, agences de développement économique ; les trois problèmes du persona (puissance et délai, foncier constructible, coût complet) ; indicateurs chiffrés ; avantage : tout est sourcé, daté et reproductible en local |
| 3 | **23** Preuve du besoin : entretiens avec des porteurs de projets avant tout nouveau développement | « Comment savez-vous que les gens ont besoin de ce que vous faites ? » (question de candidature YC, relayée) ; le bloc « problème » se fonde sur des entretiens | Grille de questions tirée des manques du lot A (capacité réelle, pièces de maturité, chaleur fatale, eau) ; nombre d'entretiens à fixer, aucune source lue n'en donne ; noter pour chaque entretien le critère qui a décidé ou bloqué un site. Les réponses peuvent réordonner les lots A et B |
| 4 | **24** Traction mesurée sans télémétrie | Chiffres concrets : utilisateurs, croissance, état du prototype | L'outil reste local : aucune collecte d'usage. Traction comptée à la main : essais réels avec un porteur de projet, retours écrits, issues et étoiles GitHub, adresses de recette couvertes (11 aujourd'hui) |
| 5 | **25** Démonstration rejouable sans `refresh` de 180 mois | Station F demande une vidéo de présentation ; YC une vidéo des fondateurs ; MVP existant exigé (Station F) | Un mode démonstration sur la base synthétique des tests (`medallion_db`), dit comme tel dans la sortie, pour qu'un jury lance `essentiel` et la carte en quelques minutes ; vidéo de 1 à 2 min sur l'exemple de référence, hors dépôt |
| 6 | **26** Modèle économique à décider | Bloc « revenus » du Lean Canvas et section « business model » du plan Sequoia | Pistes à trancher : outil libre (MIT) et études sur mesure, offre aux collectivités et agences de développement, licence d'usage. À vérifier d'abord : la réutilisation commerciale de chaque source (Licence Ouverte : permise ; ODbL des chantiers SNCF Réseau et de Nominatim : partage à l'identique des données dérivées) |
| 7 | **27** Concurrence et alternatives | Concurrents directs et indirects, et comment les battre (plan Sequoia) | Tableau des alternatives : Caparéseau, France Foncier +, sites clés en main France 2030, Yottar (aucune source publique), cabinets de conseil en implantation, étude exploratoire de raccordement de RTE ou Enedis ; ce que chacun donne ou non par rapport à l'outil |
| 8 | **28** Choix du programme et calendrier (relevé à la main, aucune planification) | Chaque programme a son thème, ses critères et ses dates | GreenTech Innovation et Station F Founders correspondent au sujet (transition écologique, projet tech avec MVP) ; vérifier les règlements en vigueur avant de candidater, les pages lues datent de 2018 à 2026 |

### Échéances à guetter

À lancer à la main, aucune planification :
- 1er janvier 2027 : refonte de Caparéseau et éventuelle carte des capacités en soutirage de RTE
  (délibération CRE 2026-173), à intégrer dès publication (étape 19) ;
- 1er janvier et 1er février 2027 : tarifs réduits de la loi de finances 2027 et indexation du tarif
  normal de l'accise, lignes à clore et à ajouter au seed `accise_electricite` ;
- 1er août 2027 : nouvelle grille TURPE 7 HTA-BT et HTB, à reporter dans les seeds
  `turpe_soutirage` et `turpe_gestion_comptage` (les sorties le signalent une fois la date passée) ;
- avril et octobre : actualisation de DVF, puis `refresh` ;
- quand EDF SEI publiera 2025 : `refresh` pour recaler le carbone horaire des îles ;
- chaque mois : `ecowatt` (jours à venir) pour garder l'historique du signal Ecowatt, qui n'est pas
  rejouable ; `refresh` rafraîchit aussi les profils de charge Enedis (12 mois glissants).

### Extension internationale (étape 6)

L'UE-27 à la maille pays est faite (`europe`). Ensuite, dans cet ordre :
1. Zones de prix (Italie, pays nordiques) : ENTSO-E et point-dans-polygone avec l'extension
   `spatial` de DuckDB. La bibliothèque `entsoe-py` (EnergieID, MIT, publiée sur PyPI, maintenue
   en 2026) est le client de référence, plutôt qu'un client maison.
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

Proposition, rien n'est codé ni décidé hors du lot 0 (fait le 29 octobre). Elle garde les décisions en
vigueur : ligne de commande, local, page HTML autonome qui s'ouvre hors ligne (pas de site web,
pas de CDN), données publiques sourcées, « non disponible » plutôt qu'un chiffre inventé.

**Constat.** three.js est déjà au cœur de la carte (`reporting/carte.html`, grille hexagonale,
maquettes, deux vues) mais en **r128 (2021)** ; la version courante est la **r186** (septembre
2026). Depuis environ la r160, three.js ne publie plus de fichier unique à charger par `<script>` :
`three.module.js` et `three.webgpu.js` importent `three.core.js`, ce qui casse l'embarquement actuel
(`SCRIPTS` dans `reporting/carte.py`). Les nouveautés utiles : `WebGPURenderer` (repli WebGL 2
automatique), shaders en JavaScript (TSL), `BatchedMesh` pour des milliers de maquettes en un appel
de dessin. La vue par défaut « Raccorder » (zones RTE, sites clés en main, postes 400 kV) parle
déjà au persona ; il lui manque de montrer ce que la puissance demandée change : domaine de
tension, gestionnaire, délai. Les briques du 29 octobre (friches, zones d'activité, desserte,
eau) ne sont aujourd'hui que dans `diagnose`, pas sur la carte (lot F).

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
| Nom | « Où brancher mon entreprise » partout ; « Brancher ici ? » réservé au verdict | Fait le 29 octobre (lot 0) : la carte s'appelait « Atlas électrique », sa légende « Où se brancher » |
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

**À décider avant le lot A** : fond clair (proposé) ou sombre actuel ; Spectral et Inter ou une
autre paire sous licence OFL ; échelle de note daltonien-compatible à la place du rouge → vert
(décision du 26 octobre à remplacer).

### three.js : ce qui sert le persona

Chaque effet doit répondre à une question du persona ; sinon il n'entre pas.

| Fonction | Rendu three.js | Question servie | Données |
|---|---|---|---|
| « Raccorder » selon la puissance | Autour de l'épingle, un anneau par domaine de tension (HTA, zone grise, HTB) teinté selon `--puissance` ; zones RTE en colonnes de lumière dont la hauteur dit l'année de capacité ; contraintes RTE en voile sur les cases | Qui raccorde mes 12 MW, et quand ? | existant (`seuils_raccordement`, SDDR, contraintes) |
| Capacité en soutirage (étape 19) | Cases teintées par capacité restante dès que RTE la publie ; d'ici là, rien d'inventé | La puissance est-elle là ? | RTE, 1er janvier 2027 |
| `compare` sur la carte | Plusieurs épingles numérotées ; caméra qui passe d'un site à l'autre ; fiches côte à côte, **sans classement** (décision du 15 octobre) | Le persona présélectionne 2 à 6 sites | existant |
| Foncier et contraintes | Sites clés en main, friches et zones d'activité en maquettes ; ICPE et Seveso (étape 15) en marqueurs discrets | Le terrain est-il prêt et constructible ? | `fct_friches`, `ingestion/zones_activite.py`, étape 15 |
| Desserte | Temps vers le port, l'aéroport, le rail-route, tracés en arcs depuis l'épingle | Logistique et industrie | `ingestion/acces_routier.py` |
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
Chilly-Mazarin, Lyon, Ajaccio et Saint-Denis de La Réunion (skill `essai-carte`), et une capture
mise à jour dans `docs/exemples/`.

| Lot | Contenu | Validation |
|---|---|---|
| 0 | **Fait le 29 octobre** : captures et PDF pour 12 MW à Chilly-Mazarin, courbe d'exemple HTA, un seul nom, bandeau d'aide sur fond | relecture README et PLAN, `essentiel` sur les exemples |
| A | `reporting/charte.py` ; polices embarquées ; PDF et graphiques sur la charte ; carte sur la charte en r128 | rendu identique sur deux postes ; PDF sans caractère remplacé |
| B | Prototype puis passage à three.js r186 (`WebGLRenderer`, `BatchedMesh` pour les maquettes) | carte sans requête réseau ; 60 images/s sur la métropole |
| C | « Raccorder » selon la puissance : anneaux des domaines de tension, zones RTE en colonnes datées | 1, 12, 30 et 50 MW à Chilly-Mazarin et Dunkerque : gestionnaire conforme à `seuils_raccordement` |
| D | `compare` épinglé sur la carte ; export d'image ; fiche sans WebGL | `compare` à 3 sites |
| E | Relief continu et ombrage ; curseur horaire des îles | Ajaccio : 522 g vers 13 h, 681 g vers 5 h |
| F | Couches friches, zones d'activité et desserte (déjà calculées), puis étapes 15 et 19 à leur arrivée | une adresse contrôlée à la main par couche |

## Besoins des porteurs de projets : recherche du 29 octobre

Recherche documentaire pour le persona (1 à 50 MW). Niveau de preuve noté à chaque ligne :
**P** = document du producteur lu (RTE, Enedis, DGE, DGFiP), **S** = chiffre relayé par la presse
ou un cabinet, non recoupé avec le texte d'origine. Aucun chiffre S n'entre dans l'outil. Les
manques qui en découlent sont dans « Suite », lot A.

| Segment | Constat | Niveau | Source |
|---|---|---|---|
| Tous | Plus de 200 projets industriels, près de 33 GW, attendent un raccordement au réseau haute tension (data centers, hydrogène, industrie lourde) : près de trois fois la consommation actuelle de l'industrie française | P | [RTE, « Réussir l'électrification de la France », 3 juillet 2026](https://www.rte-france.com/actualites/2026-07-03-reussir-electrification-france) |
| Tous | File d'attente : « premier prêt, premier servi » à la place de « premier arrivé, premier servi » d'ici fin 2026 ; la CRE demande à RTE une première évolution tenant compte de la maturité des projets au plus tard le 1er octobre 2026 | P (RTE) ; S (CRE) | RTE (même page) ; [CRE, délibération n° 2026-32 du 4 février 2026](https://www.cre.fr/fileadmin/Documents/Deliberations/2026/260204_2026-32_Procedure_raccordement_RTE.pdf), citée par une recherche, texte non relu |
| Tous | Zones « prêtes à raccorder » : environ 9 GW de plus d'ici 2029 sur les ports industriels de Dunkerque, du Havre et de Fos, et environ 5 GW dans cinq zones industrielles moyennes | P | RTE (même page) |
| Data centers | Cinq projets de 700 MW à 2 GW en procédure accélérée sur des sites identifiés par l'État, raccordement visé en 2028-2029 | P | RTE (même page) |
| Data centers | Environ 80 projets et 18 GW de capacité réservée en mai 2026 (5 GW fin 2024) ; 72 % de la puissance demandée en développement ; huit data centers seulement raccordés à RTE (800 MW) ; ceux raccordés depuis deux à trois ans n'utilisent en moyenne que 20 % de la puissance demandée | S | [Institut Quorum](https://www.institut-quorum.fr/article-data-centers-reseau-electrique), [France Épargne](https://www.france-epargne.fr/news/data-centers-883-milliards-annonces-en-france-18-gw-en-attente-de-raccordement) |
| Data centers | Enedis raccorde jusqu'à 40 MW (contrainte : le poste source le plus proche), RTE au-delà (contrainte : capacité du réseau HTB) ; étude exploratoire facultative de six semaines environ, sans réservation de capacité ; autorisations à fournir trois mois avant la convention de raccordement | S | [gridreadiness.com](https://gridreadiness.com/raccordement-data-center) |
| Data centers | Eau : prélèvement direct de l'ordre de 680 000 m³ en France en 2023, presque tout en eau potable du réseau ; le WUE (litres par kWh) est bien moins publié que le PUE ; le refroidissement adiabatique économise de l'électricité au prix de l'eau ; potentiel de chaleur fatale de 1 TWh par an aujourd'hui et 3,5 TWh en 2030 (ADEME) | S | [Hubblo](https://hubblo.org/fr/articles/data-center-water-consumption-assessment/), [IMT](https://imtech.imt.fr/2026/05/29/centres-de-donnees-refroidissement-consommation-eau/) |
| Bornes de recharge poids lourds | Dépôt et entrepôt : 8,3 TWh en 2035 (11,8 TWh de recharge au total) et 16,8 TWh en 2050 ; pic de puissance de 2,4 GW en 2035 et 4,8 GW en 2050 ; besoin diffus sur environ 7 300 zones logistiques, surtout en HTA (au-dessus de 250 kVA) ; un seul poste source dépasse 10 MW en 2035, 12 en 2040, 33 en 2050 ; une dizaine de zones à plus de 5 MW en 2035 (environ 40 en 2050), dont Saint-Quentin-Fallavier à plus de 10 MW dès 2035 | P | [Enedis, « Quels besoins de recharge au dépôt et en entrepôt pour les poids lourds électriques ? », décembre 2025](https://www.enedis.fr/sites/default/files/documents/pdf/rapport-quels-besoins-de-recharge-au-depot-et-en-entrepot-pour-les-poids-lourds-electriques-decembre-2025.pdf) |
| Bornes de recharge poids lourds | Itinérance : environ 1,1 GW au pic en 2035, 12 200 points sur 519 aires, 630 M€ de réseau, une soixantaine de postes sources à renforcer ou créer ; schéma national de 8 000 points poids lourds sur environ 560 aires ; 78 points publics poids lourds (16 MW) fin 2024 ; règlement AFIR : 400 MW au moins d'ici 2031 sur le réseau central | S | [VINCI Autoroutes, Enedis, TotalEnergies (2024)](https://ep.totalenergies.com/system/files/documents/2024-03/Electrification_des_poids_lourds_Enedis_TotalEnergies_VINCI-Autoroutes.pdf), [L'Officiel des transporteurs](https://www.lofficieldestransporteurs.fr/transition-ecologique/bornes-de-recharge-8-000-points-dedies-aux-poids-lourds-deployes-sur-le-reseau-routier-national-dici-2035-973174.php) |
| Industrie | Le guide ministériel liste les facteurs du choix de site : taille du foncier, besoin en raccordement, infrastructures de transport, réseaux d'eau et d'énergie, offre de services, risques technologiques, adéquation du PLU, enveloppe d'artificialisation de la commune (zéro artificialisation nette) ; un besoin élevé de puissance en zone déjà en tension allonge fortement le raccordement | P | [DGE, « Guide de l'implantation industrielle », mai 2025](https://www.entreprises.gouv.fr/files/files/Publications/2025/Guide/20250519-guide-implantation-industrielle-dge.pdf) |
| Industrie | Cibles publiques : autorisation environnementale ramenée de 17 à 9 mois (loi industrie verte de 2023) ; 55 sites « clés en main France 2030 » annoncés en 2024 ; France Foncier + : plus de 6 000 ha de foncier économique en 2025, fiche par terrain (eau, électricité, surface, transports) | P | DGE (même guide) |
| Logistique | Le critère dominant reste la surface adaptée, le coût du foncier et l'accès ; la distance de 10 m entre bornes et bâtiments est recommandée contre l'emballement thermique ; entrepôt d'au moins 50 000 m³ : enregistrement ou autorisation ICPE | S | [Institut Paris Région (SDRIF-E, janvier 2026)](https://www.institutparisregion.fr/fileadmin/DataStorage/SavoirFaire/NosTravaux/planification/idf2040/sdrife/fichesguides/SDRIF-E_fiche_29_logistique.pdf), [INERIS (AIDA)](https://aida.ineris.fr/sites/default/files/guides/Procedures_1510_Urba_Mars2025_vf.pdf) |
| Prix | Fin de l'ARENH le 31 décembre 2025, remplacé par le versement nucléaire universel (redistribution au-delà de 78 €/MWh) ; contrats de livraison 2026 entre 57 et 75 €/MWh selon les articles | S | [Selectra](https://entreprises.selectra.info/energie/electricite/arenh), [Optima](https://www.optima-energie.fr/blog/actualites/prix-de-lelectricite-et-du-gaz-evolution/) |

Trous de la recherche : aucun rapport récent de Cushman & Wakefield, CBRE, Gerlog ou de l'ADEME n'a
été trouvé (requêtes sans résultat) ; Yottar reste introuvable en source publique ; l'état des
obligations de l'ADEME et de l'Arcep sur le WUE et la chaleur fatale n'est pas établi ; les taux
réduits d'accise ont depuis été lus dans le formulaire 2040-TIC-SD et le texte consolidé du CIBS
(étape 18, décision du 29 octobre).

## Ce que demandent les incubateurs : recherche du 29 octobre

Recherche documentaire pour présenter le projet (étapes 21 à 28, lot C). Même notation que plus
haut : **P** = page du programme ou de l'organisme lue, **S** = relayé par un tiers. Il n'existe pas
de business plan unique imposé : chaque programme a son formulaire, mais tous reviennent aux mêmes
blocs (problème, solution, marché, concurrence, modèle économique, équipe, traction).

| Programme ou méthode | Ce qui est demandé ou jugé | Niveau | Source |
|---|---|---|---|
| Plan de Sequoia | Dix sections : mission en une phrase, problème, solution, pourquoi maintenant, marché, concurrence, modèle économique, équipe, finances, vision à cinq ans | P | [Sequoia, « Writing a business plan »](https://sequoiacap.com/article/writing-a-business-plan/) |
| Lean Canvas | Neuf blocs (segments, problèmes, proposition de valeur, solutions, canaux, revenus, coûts, indicateurs clés, avantage concurrentiel) ; outil à reprendre et faire pivoter, pour convaincre partenaires et financeurs | P | [Bpifrance Création](https://bpifrance-creation.fr/moment-de-vie/lean-canvas-lapplication-methode-du-lean-startup) |
| Y Combinator | Devise « Make something people want » ; entretien en visioconférence, décision le jour même ; questions de candidature citées par un tiers : pourquoi cette idée, expertise du domaine, preuve que les gens en ont besoin, avancement, vidéo des fondateurs ; réponses courtes et sans jargon commercial | P (page de candidature) ; S (questions) | [YC, Apply](https://www.ycombinator.com/apply), [Arc](https://arc.dev/employer-blog/y-combinator-application-tips/) |
| Station F, Founders Program | Projet technologique, équipe à plein temps, MVP déjà construit ; formulaire et vidéo ; trois mois (réseau et apprentissage, puis exécution), mentor choisi, jusqu'à 15 mois d'hébergement ; thème différent à chaque promotion | P (page de 2023) | [Station F, batch 3](https://stationf.co/news/founders-program-batch-3-open) |
| GreenTech Innovation (ministère de la Transition écologique, Ecolab du CGDD) | Label et accompagnement ; jugé sur l'ambition environnementale, la faisabilité, le réalisme économique et l'innovation ; 20 à 25 projets par édition ; appel annuel (clos le 19 janvier 2026 pour l'édition 2026) | S | [FrenchWeb](https://www.frenchweb.fr/greentech-innovation-2026-derniere-ligne-droite-avant-la-cloture-des-candidatures-le-19-janvier-2026/459622), [greentechinnovation.fr](https://greentechinnovation.fr/en/2025/04/22/startup-apply-to-the-greentech-initiative-2/) |
| Bourse French Tech (Bpifrance) | Jeune entreprise immatriculée en France ; dépenses de faisabilité, modèle économique, tests ; **montants contradictoires selon les relais (30 000 à 90 000 €)**, page officielle refusée à la lecture (HTTP 403) | S | [les-aides.fr](https://les-aides.fr/aide/VCEvGZx2wMDhXw/bpifrance/bourse-french-tech-emergence.html), [Hayot Expertise](https://hayot-expertise.fr/blog/bourse-french-tech-bpifrance-conditions) |
| Réseau Entreprendre | Prêt d'honneur sans intérêt ni garantie ; étude par des chefs d'entreprise puis comité d'engagement ; critère central : emplois créés (au moins 5 à 3 ans selon les antennes) | S | [Guide du candidat 2025 (Côte d'Azur)](https://www.reseau-entreprendre.org/cote-d-azur/wp-content/uploads/sites/75/2025/03/Guide-du-candidat-2025.pdf), [Maddyness](https://www.maddyness.com/2019/12/09/aides-subvention-prets-honneur/) |
| Wilco | Appels par thème ; en 2018, Île-de-France, moins de trois ans, traction commerciale déjà là, prêt d'honneur et 36 mois d'accompagnement | S (2018 à 2022) | [Maddyness](https://www.maddyness.com/evenement/appel-a-candidature-wilco-btoc/) |

Ce qui s'applique au projet tel qu'il est : le MVP existe (16 octobre), il est technologique et
touche la transition écologique, ce qui le place dans les critères de Station F et de GreenTech
Innovation. Ce qui manque à tous les dossiers : la preuve du besoin par des entretiens, des
chiffres de traction, un modèle économique et une équipe décrite. Réseau Entreprendre et la Bourse
French Tech supposent une entreprise créée (et des emplois pour le premier) : à voir après
l'étape 26.

Trous de la recherche : page officielle de la Bourse French Tech illisible (403) ; règlement 2027 de
GreenTech Innovation non publié ; aucun incubateur spécialisé énergie et data centers actif trouvé
(EDF Pulse Incubation accompagne des projets climat, sans lien établi avec le raccordement) ; aucune
source ne fixe un nombre d'entretiens clients.

## Ce que les études ont établi (9 au 28 octobre)

- **Les besoins des entreprises**, dans l'ordre : de la puissance disponible à une date connue
  (premier critère des gros projets ; zones RTE P1 dès fin 2028, P2 dès 2029) ; du foncier
  viabilisé ; une électricité bas-carbone ; un prix prévisible, qui ne décide que pour les
  électro-intensifs et les data centers ; la connaissance des risques du site.
- **En France, ni le prix ni le carbone ne dépendent de l'adresse** : d'où un diagnostic de
  contexte par adresse plutôt qu'un « prix à l'adresse ».
- **Limites de l'open data** : capacité de raccordement non publiée ; postes RTE sans
  coordonnées (maille du département seulement) ; ~40 % des IRIS sous secret statistique
  depuis 2022.
- **Sources de données (étude du 25 octobre, toutes intégrées)** : RTE a retiré les coordonnées
  GPS de ses postes et de ses lignes « pour des raisons de sécurité publique » (jeux ODRÉ au 16 juin
  2026), et EDF a fait de même pour ses postes sources ; le seul jeu de postes sources Enedis
  géolocalisés est local (agglomération du Puy-en-Velay). Toute distance à un poste est donc
  exclue. Enedis et l'Agence ORE ont quitté l'API Opendatasoft pour data-fair
  (`/data-fair/api/v1/datasets`) : l'ancienne API répond 410. La capacité en soutirage d'Enedis
  reste réservée au portail client entreprise ; l'outil Toprac de RTE, cité par la CRE, n'a pas de
  version publique trouvée.
- **Ce qui peut bouger (étude du 24 octobre)** : la CRE impose à RTE de refondre Caparéseau
  d'ici le 1er janvier 2027 et RTE étudie une carte de ses capacités en soutirage (délibération
  CRE 2026-173) : à intégrer dès sa publication. Enedis publie ses postes HTA/BT géolocalisés
  (licence ouverte) ; pas de jeu national des postes sources géolocalisés (voir ci-dessus).
- **Données géographiques (étude du 28 octobre, étapes 13 à 16, conclusions seulement)** : trois
  manques sans source ouverte, les loyers et annonces de locaux, la capacité d'accueil en
  soutirage, les parcelles et leurs propriétaires (fichiers fonciers du Cerema, réservés aux ayants
  droit sous convention). Imagerie 100 % ouverte au lancement (BD ORTHO 20 cm, LiDAR HD) ;
  l'imagerie commerciale (Pléiades Neo, 22 à 33 $/km² chez un revendeur) ne se justifie que pour un
  site précis. Suites version 2 et 3 dans les pistes du lot A.
- **Prix des locaux (étude du 28 octobre, étape 12)** : il n'existe pas de jeu officiel et ouvert
  des loyers d'activité par commune (la carte des loyers du ministère et de l'ANIL ne couvre que le
  logement ; Cushman & Wakefield, Geolocaux et Ayming sont privés) ; DVF (DGFiP) est la seule
  source officielle des prix de l'immobilier d'activité, d'où le prix de vente seul.

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
| 24 oct. | `--va` : électro-intensité = consommation × accise au tarif normal du seed (26,35 €/MWh depuis le 1er août 2026, voir 29 oct.) ÷ valeur ajoutée, paliers 0,5 %, 2,25 % et 6,75 % depuis le 1er janvier 2026 (CIBS L312-65, L312-71) ; taux réduits chiffrés le 29 oct. |
| 24 oct. | `--courbe` : export Enedis lu en Python à la demande, jamais stocké ; horodate = fin du pas ; tient lieu de `--conso` et `--puissance` s'ils manquent ; coût au barème HP/HC en vigueur, seulement jusqu'à 36 kW |
| 24 oct. | Carbone horaire des îles : profil de la part thermique calé sur le facteur ADEME annuel, plutôt que des facteurs par filière non sourcés ; imports de la Corse comptés non thermiques |
| 24 oct. | UE-27 : moyennes nationales seulement (Eurostat depuis 2024-S1, deux derniers semestres ; Ember, dernière année, production sans échanges) ; jointure des trois codes pays par le seed `dim_pays_ue` ; hors UE refusé |
| 24 oct. | Commandes de question : journal masqué (niveau WARNING, message seul), chemins des fichiers écrits sur stderr ; warehouse absent ou gold incomplet → `DonneesAbsentesError` et commande `refresh` à lancer |
| 24 oct. | Carte : décor (villes, forêts) et lentilles Terrain et Réseau retirés ; prix et compteurs hors de la barre du haut ; la fiche de l'adresse donne le verdict |
| 24 oct. | Carte : une carte par système électrique, placées à 600 unités l'une de l'autre dans la même scène (le brouillard cache les autres) plutôt qu'une page par territoire |
| 24 oct. | Pôles de production : registre ODRÉ agrégé à la source par commune et filière (installations en service d'au moins 10 MW ; les noms « Confidentiel » interdisent une clé par installation), centre de la commune comme position ; seuil 200 MW sur le continent, 20 MW en Corse et outre-mer ; stockage hors barrages exclu |
| 24 oct. | Après le MVP, extension par adresse en Europe de l'Ouest dans l'ordre de facilité des données : Pays-Bas, Belgique, Espagne, Autriche, Luxembourg, Suisse, Portugal, Allemagne, Royaume-Uni, Irlande, Italie, puis les micro-États (détail dans « Suite ») |
| 24 oct. | `main` protégée par un ruleset GitHub : PR obligatoire (sans approbation), job CI `ruff + pytest + dbt build` vert sur une branche à jour, ni force-push ni suppression, aucun contournement |
| 25 oct. | Carte : deux vues au lieu de quatre ; « Raccorder » note chaque case de -1 à +1 (zone RTE à moins de 3 cases, site clé en main à moins de 2, poste 400 kV, contraintes RTE au-delà de la médiane, relief, carbone au-dessus de 100 g, équilibre régional), note indicative ; « Territoire » réunit industrie (hauteur), équilibre (couleur, violet → bleu pour ne pas se confondre avec le rouge et le vert de la note) et production (maquettes) |
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
| 26 oct. | Acheminement : TURPE 7 HTA-BT (HTA à pointe fixe, BT > 36 kVA ; le tarif bleu l'inclut déjà), étendu au HTB le 29 oct. ; une même puissance souscrite dans toutes les classes ; énergie répartie sur la courbe de charge, sinon sur toutes les heures de l'année ; heures creuses 22 h – 6 h, pointe 9 h – 11 h et 18 h – 20 h de décembre à février hors dimanche ; version courte ou longue utilisation au moindre coût ; gestion en contrat unique (Rf inclus), comptage communicant. Montant donné comme part déjà comprise dans le prix moyen Eurostat |
| 26 oct. | Zonage sismique : couche WFS de Géorisques plutôt qu'un seed (le tableur de planseisme.fr n'est plus en ligne, le fichier wpd reste écarté) ; zone trouvée par l'adresse dans son polygone, ce qui évite le recalage sur le code officiel géographique ; pages triées par `gid` (sans tri, 84 polygones perdus) et nombre de polygones contrôlé contre le total annoncé ; sur une limite, la zone la plus forte ; le critère « risques » du verdict reste réglé par les seuls plans de prévention |
| 26 oct. | Continuité d'alimentation : jeu d'Enedis, producteur, plutôt que la copie de l'Agence ORE ; colonnes d'années lues dans le schéma du jeu (une de plus par publication) ; comparaison au seuil de 3 % de l'arrêté du 24 décembre 2007 (article 3) et à la médiane des départements publiés la même année ; repère dans la section « Raccordement » de `diagnose`, sans entrer dans le verdict (moyenne départementale, pas la ligne du site) |
| 26 oct. | Pôles de fret : ports sur les marchandises hors tare de la dernière année publiée (SDES), seuil 5 Mt sur le continent et 0,5 Mt en Corse et outre-mer ; aéroports sur le fret et la poste de la dernière année complète (DGAC), 20 000 t et 3 000 t ; chantiers rail-route de SNCF Réseau (ODbL, figé au 23 mars 2022) sur au moins 2 km de voies de manutention, dit repère de capacité (aucun tonnage publié) ; HAROPA placé au Havre ; ports et aéroports au centre de leur commune ; ports fluviaux écartés (pas de maille port). Carte seulement, sans ligne dans `limites_methodologiques` (réservées aux chiffres de `diagnose`) |
| 26 oct. | Seeds recréés à chaque `dbt build` (`+full_refresh`) : un seed qui gagne une colonne bloquait le warehouse existant |
| 28 oct. | **Pivot du persona** : les projets de **1 à 50 MW** (data centers, bornes de recharge, industrie, sites logistiques), modèle de Yottar ; le persona des bureaux est abandonné (le prix de l'électricité, identique partout, ne départage pas leurs villes). L'ordre des étapes suit désormais : puissance et délai, coût complet (TURPE HTB, au-delà de 150 GWh, accise selon l'activité), foncier constructible, risques et eau, desserte. Les projets de plus de 50 MW restent servis |
| 28 oct. | Prix de vente des locaux d'activité (étape 12, DVF) gardé en information, plus prioritaire ; **pas de loyer** (aucune source officielle et ouverte) ; « non disponible » sans ventes, jamais de chiffre inventé |
| 28 oct. | DVF : ventes de locaux d'activité d'un seul bien (ni logement, ni plusieurs dispositions), surfaces de 15 à 5 000 m² et prix de 100 à 15 000 €/m² (variables dbt `locaux_*`) ; médiane sur les cinq dernières années de ventes, fiable à partir de 5 ventes (`locaux_min_ventes`), sinon le département ; arrondissements de Paris, Lyon et Marseille ramenés à la commune ; fichiers annuels lus en flux (~100 Mo chacun), le pilote Spark passe à 3 Go (400 000 lignes saturaient 1 Go) ; Bourg-en-Bresse 1 179 €/m², Lyon 3 298, Paris 7 760 |
| 28 oct. | Commentaires réservés à la logique du code (règle, piège d'une source, raison d'un choix) : plus d'en-tête d'utilité au-dessus de chaque fonction ni de chaque modèle dbt ; code le plus simple possible, en Python, PySpark et dbt |
| 28 oct. | Consolidation : `essentiel`, `diagnose` et `compare` partagent les mêmes options de projet (`--puissance`, `--conso`, `--ca`, `--va`, `--surface`, `--courbe`, puis `--activite`) et les mêmes messages d'erreur ; essai de bout en bout après `refresh` complet |
| 28 oct. | Identifiants de l'application Ecowatt dans `identifiants.local.txt` (ignoré par git), sous `ECO2MIX_ECOWATT_*`, distincts de ceux de Tempo ; le secret a transité par la conversation : à régénérer si elle est partagée |
| 29 oct. | Profils de charge réels (10.5) : agrégat Enedis `conso-sup36-region`, plage « total > 250 kVA » (P7), industrie et tertiaire, 12 derniers mois publiés, filtré à la source (843 072 demi-heures au lieu de 34,6 M) ; les courbes masquées par le secret statistique sont écartées ; journée type par heure locale, jours ouvrés et week-end (jours fériés comptés ouvrés), moyenne des régions pondérée par le nombre de points ; `puissance_flexible_kw` à zéro (Enedis ne dit rien de la flexibilité) ; ajoutés à `fct_cout_horaire_profil` à côté du seed, qui reste le profil par défaut |
| 29 oct. | Ecowatt (10.6) : l'API répond (HTTP 200) depuis l'application Ecowatt ; elle ne publie que le jour et les trois suivants, sans historique : la commande `ecowatt` se lance à la main, hors `refresh` (compte RTE requis, comme `tempo`), et le dernier niveau relevé d'un jour remplace le précédent ; le rapport dit combien de jours ont été relevés plutôt que de les supposer verts |
| 29 oct. | Accise selon l'activité (18), première partie : `--activite` (`data-center`, `irve`, `industrie`, `logistique`) choisit le profil de charge de l'acheminement (journée type Enedis > 250 kVA pour l'industrie, puissance constante dite telle pour les autres) et affiche les régimes d'accise de l'attestation 2040-TIC-ATT-E (codes E15, E16, E21, E23 à E25, E30 ; CIBS L312-64 à L312-72). Taux chiffrés le même jour (voir plus bas) |
| 29 oct. | Accise au tarif normal corrigée : le formulaire DGFiP 2040-TIC-SD (2026) donne 26,35 €/MWh pour les entreprises à compter du 1er août 2026 (20,42 de tarif normal + 5,93 de majoration), après 26,58 du 1er février au 31 juillet 2026 ; `--va` s'appuie désormais sur 26,35. Les taux réduits du même formulaire ne sont pas chiffrés : l'extraction du PDF désaligne les libellés et les montants |
| 29 oct. | TURPE 7 HTB (17) : grille HTB 1 à 3 du 1er août 2026 (délibération CRE 2026-104, annexe 2) en seed daté à côté de la HTA-BT, appliquée dès 40 MW avec HTB 1 par défaut ; calendrier horosaisonnier supposé identique à la HTA jusqu'à relecture de la délibération 2025-77 |
| 29 oct. | Zonage d'urbanisme (14) : zone du point par l'API Carto de l'IGN (module GPU, sans clé), appelée à la demande par `diagnose` et `compare` sauf sous `--sans-risques` ; la plus contraignante des zones superposées (A ou N) passe devant ; type de zone seulement, la destination autorisée se lit au règlement (lien donné s'il est publié) ; zone A ou N : critère « foncier » à vérifier ; aucune zone : « document non publié ou non numérisé », jamais un chiffre inventé. Pas de stockage local (couverture et version du document changent) |
| 29 oct. | Friches (14) : fichier national « Sites référencés dans Cartofriches » (data.gouv.fr, lien permanent de la ressource, Cerema, Licence Ouverte v2, Windows-1252, séparateur « ; », « NA » pour l'absent) chargé par `refresh` (étape `friches`) ; retenues : surface d'unité foncière d'au moins 1 ha (variable dbt `friches_surface_min_m2`) et statut autre que « friche reconvertie » ; sites sans surface écartés plutôt que supposés ; distance à vol d'oiseau, rayon de 10 km ; inventaire déclaratif et incomplet, dit comme tel ; les zones d'activité n'y figurent pas |
| 29 oct. | Zones d'activité (14) : couche `zone_d_activite_ou_d_interet` de la BD TOPO (WFS de la Géoplateforme, Licence Ouverte), nature « Zone industrielle » (zones d'activité, industrielles et artisanales confondues) ; boîte de 5 km, distance au bord du polygone et surface calculées dans un plan local, plafond de 500 objets ; à la demande, comme le zonage ; ne vaut pas constructibilité ni disponibilité |
| 29 oct. | Eau (20), première partie : VigiEau (API publique du ministère de la Transition écologique, sans clé, profil « entreprise ») à la demande, sous `--sans-risques` coupé comme Géorisques et l'urbanisme ; niveau le plus haut des zones en vigueur (vigilance, alerte, alerte renforcée, crise) ; situation du jour, pas un risque futur ; aucun arrêté : dit ; volumes BNPE écartés (ils ne mesurent pas la tension). Sans effet sur le verdict : la ressource réellement utilisée par un data center ne se déduit pas de l'adresse |
| 29 oct. | Accès routier (13) : calcul d'itinéraire de la Géoplateforme (ressource `bdtopo-pgr`, profil voiture : `bdtopo-osrm`, cité auparavant, n'existe plus) vers les pôles de fret de `fct_poles_fret` (centre de la commune pour ports et aéroports, ordre de grandeur) ; pas de profil poids lourd, dit dans la limite ; l'échangeur autoroutier n'est pas identifiable dans la BD TOPO (bretelles sans classement) : seule la distance à vol d'oiseau jusqu'au tronçon « type autoroutier » classé autoroute (10 km au plus) est donnée ; à la demande |
| 29 oct. | Bâtiment et solaire (16) : bâtiment de la BD TOPO à 60 m au plus de l'adresse (le plus proche si l'adresse n'est pas dessus) et PVGIS 5.3 (JRC) pour un kWc de panneaux fixes, inclinaison et orientation optimales, 14 % de pertes ; aucun dimensionnement de centrale déduit de l'emprise (hypothèses non sourcées) ; obligations de la loi APER non évaluées (reste de l'étape 16, lot A) |
| 29 oct. | Étoile en schéma `etoile` au-dessus du gold, sans renommer les `fct_*` ; `dim_commune` reprend les communes des faits absentes du référentiel (clé étrangère jamais orpheline) |
| 29 oct. | Accise réduite chiffrée (18, fin) : tarifs de la loi de finances 2026 (loi n° 2026-103) lus dans le formulaire DGFiP 2040-TIC-SD 2026, en mode « table » de pdftotext (le mode « layout » décalait d'une ligne libellés et montants ; alignement vérifié sur les tarifs normaux, dont le montant figure dans le libellé), et recoupés avec le texte consolidé du CIBS sur Légifrance : 5,5 €/MWh (grande consommatrice, dès 0,5 % d'électro-intensité), 3 (électro-sensible, dès 2,25 %), 0,5 (électro-intensive, dès 6,75 %), 0,5 (exposée à la concurrence internationale, L312-72), 10 pour les centres de stockage de données sur la fraction au-delà de 1 GWh par an (L312-64, L312-70) ; seed `accise_electricite` à une ligne par catégorie. Les paliers de `--va` passent de 3,375 % (bouclier tarifaire, fini le 31 décembre 2025) à 2,25 %. Tarif réduit total, sans majoration ZNI à part (le formulaire n'en déclare pas). Industriel : seulement sans `--activite` ou avec `industrie` (sections B à E de la NACE) ; data center : refusé quand `--va` montre moins de 2,25 % (condition 8 de L312-70), les sept autres conditions dites, pas vérifiées ; aucune réduction pour la recharge ni l'entrepôt. Montants donnés à côté du prix moyen Eurostat, sans l'en retrancher (on ignore la part d'accise réduite dans la moyenne de la tranche) |
| 29 oct. | Cohérence (lot 0) : exemples du README refaits sur l'exemple de référence ; captures prises par Chrome sans interface (`--headless=new`, rendu WebGL logiciel SwiftShader), la vue « Territoire » par un appel à `setLens` ajouté à une copie de la page, jamais au gabarit ; courbe d'exemple tirée de `profils_charge_reels` (industrie > 250 kVA) et mise à l'échelle d'une pointe de 12 MW, dite comme telle, à la place de la courbe de bureau. Au-delà de 36 kVA, `--courbe` ne chiffre pas l'énergie heure par heure (aucun prix horaire public, décision du 12 octobre) : la ligne renvoie au prix de la tranche et à l'acheminement sur la courbe |
| 29 oct. | Voisinage du site (15) : API Géorisques à la demande, coupée par `--sans-risques`, en plus du rapport à l'adresse et indépendante de lui (l'un peut répondre sans l'autre). Rayons choisis par l'outil, aucun texte n'en fixe : argiles au point, cavités et sols pollués à 500 m (filtre de l'API, sans distance : SIS, SUP et instructions n'ont qu'un polygone), installations classées à 1 km, Seveso à 5 km ; seules les installations sous autorisation ou enregistrement « en exploitation avec titre » sont retenues (« Non ICPE » et « Autres régimes » n'ont ni titre ni état) ; un ancien site CASIAS n'est pas un sol pollué avéré. Le critère « risques » du verdict reste réglé par les plans de prévention (un Seveso seuil haut impose un PPRT, déjà compté) : le voisinage complète son détail (Seveso le plus proche, SIS) sans changer son état |
| 29 oct. | Prochaines étapes en trois lots (A maintenant, B en attente, C incubateur) ; une étape terminée quitte « Suite » et ne reste que dans « État » et les décisions |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Distance aux postes sources et aux lignes | Coordonnées retirées par RTE et EDF en 2026 (sécurité publique) ; jeu Enedis seulement local |
| Zonage sismique national publié par wpd (data.gouv.fr, 2018) | Producteur non officiel (un développeur éolien) : le décret et planseisme.fr font foi |
| Loyer des locaux d'activité, y compris estimé par un rendement brut | Aucune source officielle et ouverte ; l'hypothèse de rendement n'a pas de source (28 oct.) |
| Classement pondéré des sites dans `compare` | Contraire à la comparaison sans classement (15 oct.) |
| Ports fluviaux parmi les pôles de fret | Pas de maille port dans les données VNF du SDES |
| Persona des bureaux | Pour quelques dizaines de MWh, le prix est le même partout et le raccordement rarement un sujet (28 oct.) |
| Spark pour silver et gold | dbt-duckdb suffit à ce volume |
| Site web, exécution planifiée, Airflow | Hors périmètre : ligne de commande, en local |
