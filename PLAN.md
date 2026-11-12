# Plan du projet

> Périmètre : la France (métropole, Corse, outre-mer), en ligne de commande ou dans l'application
> locale, à la demande. Le projet vit sur GitHub et se lance sur son poste ; rien n'est hébergé.

Ce fichier porte l'état du projet, les étapes qui restent, les recherches qui les justifient et
les règles en vigueur ; le [README](README.md) dit comment s'en servir. Sections, dans l'ordre :
persona, état (ce qui existe), suite (prochaines étapes, puis trois lots), refonte de la carte,
recherches documentaires, règles en vigueur, sujets écartés. Une étape terminée quitte « Suite »
et devient une ligne de « État » (skill `cloturer-etape`).

## Persona

Le porteur d'un **projet de 1 à 50 MW** qui cherche où s'implanter : data center, hub de bornes de
recharge (véhicules légers et poids lourds), site industriel, site logistique (entrepôt froid,
quai électrifié). Pour des bureaux, le prix de l'électricité est identique partout et ne départage
pas les villes : l'outil n'a rien de décisif à leur dire.


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
vérifié le 28 octobre). Au-delà de 40 MW : 45 MW et 300 000 MWh (HTB 1, 3,79 M€ d'acheminement
par an sur le calendrier HTB, vérifié le 1er novembre).

## État

`essentiel` donne en 1,5 s environ le prix moyen et le
verdict carbone ; il répond juste sur 11 adresses de recette, de Paris à Mamoudzou (test
`test_recette_mvp.py`). `diagnose` couvre aussi le foncier
(urbanisme, friches, zones d'activité), l'eau, la desserte et le solaire de l'adresse, et le
voisinage du site (argiles, cavités, Seveso, installations classées, sols pollués).

| Brique | Où |
|---|---|
| Ingestion PySpark (typage, UTC, clés du lot), écriture Arrow vers le bronze | `ingestion/transform.py`, `ingestion/spark.py` |
| Adresse → commune, département, région ; adresse ambiguë ou approximative refusée | `ingestion/geocode.py` (BAN) |
| Prix par tranche Eurostat, scénarios à 3 ans calibrés sur les erreurs passées | `fct_prix_entreprise_tranche` |
| Carbone par système électrique, seuil de 100 gCO₂e/kWh | `fct_carbone_territoire` |
| Carbone heure par heure en Corse et outre-mer (production EDF SEI, profil calé sur l'ADEME) | `fct_carbone_horaire_zni` |
| Part acheminement de la facture (TURPE 7 HTA-BT et HTB 1 à 3 au 1er août 2026) selon le domaine de tension, la puissance et la consommation, sur la courbe de charge si elle est fournie ; HTB dès 40 MW, HTB 1 par défaut, sur son propre calendrier (délibération 2025-77 : deux régimes datés, week-ends et jours fériés creux, Nouvelle-Aquitaine et Occitanie à part dès 2027) | `ingestion/turpe.py`, seeds `turpe_soutirage`, `turpe_gestion_comptage` |
| Part de la facture dans le chiffre d'affaires (`--ca`) et électro-intensité fiscale dans la valeur ajoutée (`--va`, accise au tarif normal de 26,35 €/MWh) | `essentiel`, `diagnose`, seed `accise_electricite` |
| Activité du projet (`--activite data-center\|irve\|industrie\|logistique`) : profil de charge de l'acheminement et régime d'accise de l'activité | `essentiel`, `diagnose` |
| Accise au tarif réduit de la loi de finances 2026, face au tarif normal : 10 €/MWh au-delà du premier GWh pour un data center (refusé si `--va` montre moins de 2,25 % d'électro-intensité), 5,5, 3 ou 0,5 €/MWh selon le palier pour l'industrie ; montant, économie et conditions, dans les deux commandes, le verdict et le JSON | `accise_reduite`, `ligne_accise`, seed `accise_electricite` |
| Courbe de charge Enedis (`--courbe`) : consommation, pointe, heures creuses, coût (jusqu'à 36 kW) et carbone réels | `ingestion/courbe.py` |
| Diagnostic complet : industrie, réseau, zone RTE, site clé en main, risques | `diagnose`, `compare` |
| Entreprises raccordées à Enedis (2025) : volume, industrie et premier secteur NAF de la commune, rang dans le département ; consommation déjà relevée à l'adresse (« Déjà raccordé ici ») | `fct_conso_entreprises_commune`, `fct_conso_entreprises_adresse`, seed `dim_naf2` |
| Verdict « brancher ici ? » en cinq critères (raccordement, risques, carbone, facture, foncier), dans `essentiel`, `diagnose`, `compare` et sur la carte | `verdict`, `phrase_verdict` |
| Carte 3D épurée autour du verdict : vue « Raccorder » par défaut, adresse reliée à la zone RTE utile et au site clé en main ; épinglée par `essentiel` et `diagnose` (seule l'épingle bouge si les données n'ont pas changé) | `carte`, `reporting/carte.py` |
| Une carte par système électrique (métropole et Corse, chaque DROM), ouverte sur celle de l'adresse ; deux vues : « Raccorder », cases notées du rouge au blanc puis au vert, et « Territoire », paysage réel (mer bleue, verdure, relief IGN) avec carbone et prix moyen dans l'encart ; dans les deux vues, 95 pôles de production du registre national en maquettes par filière, et les sites clés en main en maquette, nommés au survol | `ingestion/hexagones.py`, `fct_sites_production` |
| Grands pôles de fret dans les deux vues de la carte, en maquettes (port à portique, aéroport et avion cargo, chantier rail-route) : 34 pôles en 2025 au-dessus des seuils | `ingestion/extract_fret.py`, `fct_poles_fret`, seeds `dim_ports_fret` et `dim_aeroports_fret` |
| Risques sans dépendre de l'API Géorisques : export GASPAR chargé par `refresh` | `fct_risques_commune` |
| Zone de sismicité réglementaire (1 à 5) à l'adresse, sans l'API : couche WFS de Géorisques chargée par `refresh`, point dans polygone en Python | `ingestion/extract_sismique.py`, `fct_zonage_sismique` |
| Qualité de fourniture du département dans `diagnose` : part des clients Enedis au-delà des seuils réglementaires de coupure (dernière année, moyenne sur cinq ans, médiane des départements, seuil de 3 %) ; « non publiée » hors Enedis | `fct_continuite_departement`, `ligne_continuite` |
| Risques « à vérifier » seulement sous un plan de prévention approuvé ou prescrit (13 919 communes ; 31 706 ont un risque seulement recensé) | `fct_plans_prevention_commune`, `_critere_risques` |
| Zone d'urbanisme de l'adresse (U, AU, A ou N, document et règlement) dans `diagnose` et `compare`, et dans le critère « foncier » du verdict (« à vérifier » en zone A ou N) ; appel à la demande, sans stockage | `ingestion/urbanisme.py`, API Carto module GPU |
| Friches de 1 ha ou plus et pas reconverties (Cartofriches, Cerema, Licence Ouverte v2) : nombre à moins de 10 km et les trois plus proches, avec type, surface, zone d'urbanisme et statut, dans `diagnose` et `compare` (détail du critère « foncier ») | `ingestion/extract_friches.py`, `friches`, `fct_friches` |
| Réseaux de chaleur à moins de 10 km (France Chaleur Urbaine, Licence Ouverte 2.0) : les trois plus proches de l'adresse, avec leur distance au tracé, la part d'énergies renouvelables et de récupération et le classement, dans `diagnose` (section 7) et `compare` ; un réseau sans tracé publié est placé au centre de sa commune ; fait `fait_reseau_chaleur` dans l'étoile, par département traversé | `ingestion/extract_reseaux_chaleur.py`, `reseaux_chaleur`, `fct_reseaux_chaleur` |
| Arrêtés de restriction des usages de l'eau en vigueur à l'adresse (VigiEau : niveau le plus haut parmi les eaux superficielles, souterraines et potable, date de fin) dans `diagnose` et `compare` ; appel à la demande | `ingestion/eau.py` |
| Tension structurelle sur la ressource en eau : l'adresse dans une zone de répartition des eaux (118 zones du Sandre, chargées par `refresh`), ressource visée (superficielle, souterraine ou les deux) et seuil d'autorisation des prélèvements (8 m³/h, R214-1 rubrique 1.3.1.0), dans `diagnose` ; Chilly-Mazarin dans la ZRE de l'Albien (eaux souterraines) | `ingestion/extract_zre.py`, `fct_zones_repartition_eaux`, `ligne_zones_repartition` |
| Zones d'activité et industrielles de la BD TOPO à moins de 5 km (nombre, trois plus proches avec surface, adresse dans la zone ou non) dans `diagnose` et `compare` ; appel WFS à la demande | `ingestion/zones_activite.py` |
| Accès routier : temps en voiture (Géoplateforme) vers le port, l'aéroport et le chantier rail-route de fret les plus proches du même territoire, et distance à vol d'oiseau jusqu'à l'autoroute la plus proche (BD TOPO), dans `diagnose` et `compare` | `ingestion/acces_routier.py` |
| Bâtiment de l'adresse (BD TOPO : usage, emprise au sol, hauteur) et productible photovoltaïque d'un kWc (PVGIS : kWh par kWc et par an, inclinaison optimale), dans `diagnose` et `compare` ; appels à la demande | `ingestion/solaire.py` |
| Obligations de solarisation, rappelées dans `diagnose` et `compare` : toiture d'un bâtiment neuf, d'une extension ou d'une rénovation lourde de plus de 500 m² au sol (40 % depuis le 1er juillet 2026, 50 % au 1er juillet 2027, CCH L171-4), bâtiment existant de l'adresse au 1er janvier 2028 selon son usage et son emprise BD TOPO (L171-5), parc de stationnement extérieur au-delà du seuil du territoire (1 500 m², 1 000 à La Réunion, 2 500 en Guyane, non fixé à Mayotte ; échéance 2026 ou 2028 selon la surface, loi APER art. 40 modifiée le 26 novembre 2025) ; à Chilly-Mazarin, bâtiment le plus proche résidentiel, hors de L171-5 | `lignes_obligations_solaires`, `ingestion/solaire.py` |
| Date de chargement de chaque source dans toutes les sorties ; pied de page des rapports avec la commande pour les refaire | `ligne_chargements`, `pied_de_page` |
| Prix de vente des locaux d'activité : médiane et quartiles au m² par commune (repli sur le département sous 5 ventes), cinq dernières années de ventes DVF (393 099 ventes de 2021 à 2025 chargées, 219 564 retenues) ; coût d'achat avec `--surface` ; affiché pour information, aucun loyer | `ingestion/extract_dvf.py`, `ventes_locaux`, `fct_prix_locaux`, `ligne_locaux` |
| Pays de l'UE-27 : prix Eurostat et carbone Ember du pays, face à la France à tranche égale ; pays trouvé par Nominatim | `europe`, `fct_pays_ue` |
| Mise à jour de toutes les sources, puis `dbt build` | `refresh` |
| Rapport mensuel carbone et créneaux Tempo, sur le calendrier réel de l'API RTE (vérifié le 25 octobre sur septembre 2026 et janvier 2026) | `tempo`, `report` |
| Profils de charge réels des points de plus de 250 kVA, industrie et tertiaire (Enedis, 12 mois, 843 072 demi-heures chargées le 29 octobre) : journée type pondérée par le nombre de points, chiffrée heure par heure comme le profil du seed | `ingestion/extract_enedis.py`, `profils_charge_sup36`, `profils_charge_reels`, `fct_cout_horaire_profil` |
| Signal Ecowatt (J à J+3) relevé à la demande par `ecowatt`, jours orange et rouges dans le rapport mensuel | `RteEcowattClient`, `rte_ecowatt`, `ecowatt_jours`, `ligne_ecowatt` |
| Modèle en étoile : 5 dimensions (`dim_commune` avec le centre de la commune, `dim_departement_region`, `dim_date` de 1930 à 2035, `dim_risque`, `dim_naf`) et 8 faits (conso des entreprises, prix des locaux, risques, plans de prévention, diplômés, recrutements techniques, continuité, mix journalier), clés étrangères testées sur le warehouse réel ; `diagnose`, `compare` et l'application y lisent les entreprises de la commune, risques et plans, continuité, recrutements et diplômés (sorties de référence inchangées) | `dbt/models/etoile/` |
| Exemples du README sur le persona : captures des deux vues et PDF de `diagnose` pour 12 MW et 70 000 MWh à Chilly-Mazarin, courbe d'exemple HTA de 12 MW (journée type Enedis de l'industrie mise à l'échelle) ; un seul nom, « Où brancher mon entreprise », sur la carte | `docs/exemples/` |
| Voisinage du site, à la demande par l'API Géorisques : argiles à l'adresse, cavités à moins de 500 m, établissements Seveso en exploitation à moins de 5 km (distance), installations classées sous autorisation ou enregistrement à moins de 1 km, sols pollués à moins de 500 m (SIS, SUP, sites en instruction, CASIAS) ; à Chilly-Mazarin : argiles fortes, SMCA (Seveso seuil bas) à 4,8 km, 2 installations classées, 11 anciens sites CASIAS | `GeorisquesClient.voisinage`, `completer_voisinage`, `lignes_voisinage` |
| Charte commune : papier clair et encre bleu nuit, vert pour le favorable, orange pour « à vérifier », Spectral et Inter embarquées (OFL) dans la carte, le PDF et les graphiques ; le PDF garde ₂, ≥ et → | `reporting/charte.py`, `reporting/polices/` |
| Carte du monde en tuiles : tous les pays en tuiles ternes, France et départements d'outre-mer en couleur et à leur place ; ouverture sur le monde, plongée vers l'adresse, tuile détaillée qui sort de la carte ; fiche en jauges et cartes chiffrées, détail et limites à la demande ; bâtiment 3D au liseré doré à la place de l'épingle, sans bandeau d'adresse ; vague de reverdissement et pousse des arbres, inspirées de Terra Nil, coupées si l'utilisateur limite les animations ; tuile dessinée en vectoriel en tête du PDF ; encre vert nuit, titres verts et ornement solarpunk jusque dans le PDF | `ingestion/hexagones.py`, `reporting/carte.html`, `reporting/tuile.py` |
| Ambiance sonore de la tuile ouverte, coupée à l'ouverture : bourdonnement à 50 Hz (thermique, nucléaire, bioénergies, géothermie), souffle (éolien), eau (hydraulique), ressac (énergies marines) quand la filière porte plus de la moitié de la puissance en service sur la case ; rien sinon, ni pour le solaire. Sons synthétisés par Web Audio, sans fichier ni requête, volume bas, suspendus quand l'onglet est caché ; aucun effet sur le PDF | `reporting/carte.html`, `test_navigateur.py` |
| Application locale : `app` sert la carte sur 127.0.0.1 et permet tout le parcours dans le navigateur : adresse, réglages du projet, adresse ambiguë résolue par le choix d'une commune, verdict épinglé, diagnostic (essentiel ou complet), rapport PDF, Markdown ou JSON téléchargé avec la commande qui le refait ; courbe de charge Enedis déposée dans « Projet » et comparaison de 2 à 6 adresses en tableau dans le panneau (CSV, Markdown, JSON), sans épingle sur la carte. Touches solarpunk sur la carte, reprises dans le PDF : lumière dorée, épingle au soleil, bosquets dans la vue Territoire, toitures végétalisées et solaires des sites clés en main, panneaux aux angles doux | `reporting/app.py`, `app` |
| Sortie réduite à la réponse (journal sous `--verbose`) ; avant tout chargement, la commande `refresh` à lancer | `ingestion/cli.py` |
| Pannes des sources à la demande : chacune des huit sources jouée contre huit pannes (délai dépassé, connexion coupée, 500, 404, corps vide, page de maintenance, JSON inattendu, liste vide) : le diagnostic aboutit, l'échec est dit dans sa section (pour les risques, par le repli sur l'export GASPAR), aucun critère du verdict ne change d'état ; mêmes pannes sur `diagnose`, `compare` et l'application entiers. Un JSON qui n'est pas un objet est un échec de la source ; un corps vide de Géorisques n'est plus lu comme « rien à signaler », sauf pour `rga` | `test_pannes.py` |
| Matrice des cas d'usage : 158 cas joués par la vraie ligne de commande sur la base synthétique, vérifiés sur des invariants : `essentiel` pour chaque activité à chaque palier de puissance (bornes de `seuils_raccordement` comprises), `diagnose` et `compare` dans tous leurs formats, les onze communes de la recette à 12 et 45 MW, chaque option, l'accise réduite selon l'activité, les projets refusés, le warehouse absent ; les quinze chaînes de chargement rejouées deux fois (rien de dupliqué, Parquet jamais écrasé) et les commandes d'entretien, `tempo`, `ecowatt`, `report` et `app` lancées par `cli.main` ; une consommation que la puissance ne permet pas (plus de 8 784 heures) est refusée en ligne de commande et dans l'application | `test_cas_usage.py`, `test_commandes.py`, `incoherence_projet` |
| Sorties de référence : douze sorties de `essentiel`, `diagnose` et `compare` et le rapport Markdown figés sur la base synthétique, comparés à chaque run ; un libellé changé fait échouer le test, `--maj-references` réécrit les références ; PDF relu par `pypdf` : pages, pied de page, sections, tuile, aucun caractère sans glyphe ; chaque maquette et chaque relief de la tuile dessinés ; couverture mesurée à chaque run (`pytest --cov`, lignes et branches : 94,6 %), échec sous 92,5 % ; courbe de charge illisible refusée avec le numéro de la ligne fautive. Essai réel : le PDF complet de Chilly-Mazarin (9 pages) n'a aucun caractère sans glyphe | `test_references.py`, `ingestion/tests/references/` |
| Parcours du néophyte : `app` ouvre une fenêtre d'accueil qui demande une adresse, une seconde si l'on veut comparer (deux au plus), le projet replié ; la carte s'ouvre cadrée sur les adresses (la tuile si elles la partagent, la région si elles en occupent deux bouts, le monde si l'une est outre-mer), chacune avec son bâtiment (liseré doré pour la première, vert pour la seconde) ; fiche adaptée : verdict et cartes chiffrées pour une adresse, mêmes lignes côte à côte et un onglet par adresse pour deux ; rapport PDF en un clic | `reporting/carte.html`, `reporting/app.py` |
| PDF court et visuel : 3 pages pour l'exemple de référence ; le verdict et sa tuile, puis une fiche par critère (échelle des domaines de tension, plans de prévention, jauge du carbone, parts de la facture et accise, distances et prix des locaux) avec trois phrases au plus, dont la limite qui pèse sur la décision et un conseil ; repères du site en tableau, deux graphiques du territoire | `reporting/implantation_pdf.py`, `reporting/visuels.py` |
| Essai réel élargi : `verifier.py` joue cinq cas sur le warehouse réel en 15 s environ et dit pour chacun ce qu'il attend (domaine de tension, raccordement du verdict, système électrique) : `essentiel` à 12 MW à Chilly-Mazarin (HTA), `diagnose --sans-risques` à 45 MW (HTB 1, RTE), `compare` de Chilly-Mazarin et Lyon, `essentiel` à Ajaccio (Corse, au-dessus du seuil carbone), `/api/analyse` du vrai serveur de l'application ; `--complet` ajoute un `diagnose` avec les sources à la demande et nomme celle qui ne répond pas | `.claude/scripts/verifier.py` |
| Application jouée dans un navigateur : sept parcours dans Chromium (`pytest-playwright`, groupe `e2e`, sautés sans le paquet) contre le vrai serveur de `app` et la base synthétique, seul le géocodage simulé, réseau coupé : accueil → adresse → verdict épinglé → PDF ; adresse ambiguë → choix d'une commune ; deux adresses → côte à côte → tableau détaillé → CSV ; courbe déposée → gardée après rechargement → retirée ; serveur arrêté → message ; page en `file://` → lecture seule ; « Son » → règle de la filière dominante, lecture au clic. Une erreur de console ou une requête hors de la machine fait échouer le parcours | `test_navigateur.py` |
| Main-d'œuvre dans `diagnose` et `compare`, sans effet sur le verdict : diplômés du supérieur et titulaires d'un CAP-BEP ou d'un bac des communes dont le centre est à moins de 45 min en voiture (isochrone de la Géoplateforme à la demande, base Diplômes-Formation 2023 de l'Insee) ; projets de recrutement et part jugée difficile des métiers de l'électricité, du froid et de la maintenance au département (BMO 2026, France Travail). À Chilly-Mazarin : 457 communes, 3,3 millions de diplômés du supérieur, 1,9 million de CAP-BEP ou bac ; 1 068 projets dans l'Essonne, dont 70,3 % difficiles. Mayotte « non disponible » (absente du fichier de l'Insee) | `ingestion/extract_main_oeuvre.py`, `ingestion/main_oeuvre.py`, `fct_diplomes_commune`, `fct_recrutements_techniques_departement`, seed `metiers_techniques_bmo` |
| Démonstration sans `refresh` : `demo` construit une fois la base synthétique des tests dans `data/demo/` (une minute environ, sans réseau), répond `essentiel` pour un hub de recharge fictif de 2 MW à Lyon, chiffres dits fictifs en première ligne, et ouvre la carte ; environ 2 s ensuite | `ingestion/demo.py`, `demo` |

**À surveiller** :
- **Géorisques** : depuis Python (`httpx`), les points du voisinage (`installations_classees`, `ssp/*`, `rga`,
  `cavites`) répondent en moins d'une seconde ; le rapport à l'adresse
  (`resultats_rapport_risque`) reste instable (un 200 en 11 s, puis « Server disconnected ») et
  `urllib` est toujours coupé. Le repli sur l'export GASPAR (maille commune) et sur le zonage
  sismique WFS joue ; l'inondation à l'adresse n'est donnée que quand le rapport répond. Taille de
  page maximale de l'API : 1 000 ; `rga` répond 200 sans corps hors carte (La Réunion, Ajaccio) :
  c'est le seul point dont un corps vide est accepté, vérifié le 9 novembre à Chilly-Mazarin,
  Saint-Denis de La Réunion et Ajaccio.
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
- obligations de solarisation : la surface de toiture des bâtiments existants (L171-5, 2028) attend
  son décret, et Mayotte son seuil de parking (loi APER, art. 40 VI) ; relire Légifrance avant
  chaque échéance (règles codées en constantes datées dans `ingestion/solaire.py`) ;
- zones de répartition des eaux : aucune n'est publiée en outre-mer dans le référentiel ZRPE du
  Sandre, et 31 des 118 zones n'ont pas de nom (8 novembre) ;
- **API Carto (urbanisme)** : le 12 novembre, tout `apicarto.ign.fr` répond 404 (module GPU,
  cadastre, documentation), sans annonce trouvée de l'IGN : panne ou déménagement, à éclaircir. Le
  diagnostic dit « zonage d'urbanisme non consulté » et le critère « foncier » reste « pour
  information » ; `verifier.py --complet` le signale ;
- EDF SEI publie jusqu'à fin 2024 seulement (2021-2024 « estimé »), sans Mayotte ; le jeu a
  quitté `opendata-corse-outremer.edf.fr` pour `opendata.edf.fr` (API data-fair).

## Suite

Les étapes gardent leur numéro. Chacune n'est cochée qu'après ruff, pytest (dont `dbt build`) et un
essai réel sur un projet de 1 à 50 MW (`python .claude/scripts/verifier.py`). Trois lots : A, à
prendre maintenant ; T, qualité des tests ; B, en attente d'une source, d'une date ou d'une
décision.

### Prochaines étapes, dans l'ordre

Le projet est un outil local, présenté à des recruteurs comme un travail de data engineering :
- **il se lance sur son poste** : dépôt GitHub, `demo` pour le voir sans rien charger, `refresh`
  pour les vraies données ; ni hébergement, ni exécution planifiée ;
- **identifiants personnels** : jamais dans le dépôt ; qui recharge les données crée ses propres
  comptes (RTE pour Tempo et Ecowatt, les seuls à en demander) dans `identifiants.local.txt`,
  ignoré par git ; l'application et le reste de `refresh` n'en demandent aucun ;
- **data engineering lisible** : médaillon (bronze Parquet + `MERGE`, silver et gold dbt) et
  schéma en étoile au-dessus du gold pour l'usage ; le code le moins verbeux possible ; un
  commentaire dit une règle métier, un piège de source ou un choix d'architecture, jamais ce que
  fait une ligne.

Le détail de chaque ligne est dans son lot, plus bas.

| Ordre | Étape | Condition ou date |
|---|---|---|
| 1 | **Lot T** Qualité des tests : CI sous Windows (T8) ; corriger le gestionnaire affiché en Corse et outre-mer | Le gestionnaire demande une source officielle |
| 2 | **Carte, lot B** : prototype three.js r186 hors ligne (une demi-journée), puis lots C et D | Le lot D épingle `compare` sur la carte |
| 3 | **Pistes retenues du lot A** : image datée du site, pièces du dossier de raccordement | Dans cet ordre ; chacune sa source, déjà lue ou à relire |
| 4 | **8, 30, 6** Prix au-delà de 150 GWh, modèle prédictif, international | Chacune sa condition (lot B) ; l'international en dernier |

Pas à faire maintenant : l'étape **19** (capacité en soutirage de RTE) et les mises à jour des barèmes et des sources attendent leur date, listée dans « À se rappeler aux dates ».

### Lot A : à prendre maintenant

Les commandes gardées hors de l'application, les limites gardées faute de source et les manques
à trier. Règle pour toute nouvelle source : son fait gold entre dans l'étoile avec ses clés vers
`dim_commune` (ou `dim_departement_region`) et `dim_date`.

**Application et ligne de commande.** L'application couvre
`essentiel`, `diagnose`, `compare`, `carte` et toutes les options de projet, `--courbe` comprise.
Restent hors de l'application, à dessein :

| Commande | Pourquoi elle reste en ligne de commande | Quand la reprendre |
|---|---|---|
| `compare` au-delà de deux adresses | L'application épingle et compare deux adresses ; six côte à côte ne tiennent pas dans la fiche | Lot D de la carte, si le besoin se confirme |
| `europe` | Moyennes nationales seulement, hors du diagnostic à l'adresse | Avec l'étape 6, en dernier |
| `refresh` | Une vingtaine de minutes et un seul writer DuckDB : l'application affiche la date des données et la commande à lancer | Pas prévu |
| `tempo`, `ecowatt`, `report` | Comptes RTE requis ; rapport mensuel sans rôle dans le choix d'un site | Pas prévu ; à retirer s'il ne sert pas |
| `demo` | Une commande pour voir l'outil sans rien charger, sans adresse à saisir | Pas prévu |

**Limites gardées**, faute de source : la composante d'injection-soutirage du TURPE est une option
réservée au stockage, et une alimentation de secours dépend de la partie dédiée propre au site
(seul son tarif de réservation est cité) ; le tarif réduit d'accise des secteurs exposés à la
concurrence internationale, fixés par arrêté, n'est pas identifié par l'outil ; l'échangeur
autoroutier n'est pas identifiable dans la BD TOPO (bretelles sans classement) et la Géoplateforme
n'a pas de profil poids lourd ; la structure des toitures n'est pas publique (la BDNB, Licence
Ouverte 2.0, donne au mieux le matériau) et la surface du parking n'est pas connue de l'outil ; le
débit ou le niveau de nappe du jour face à une référence d'étiage reste hors de l'outil (Hub'Eau
ne publie aucune référence, le QMNA5 se calcule station par station).

**Pistes retenues**, dans l'ordre : utiles au persona, une source officielle et ouverte déjà
identifiée, faisables sans chargement lourd.

| Ordre | Manque | Piste | Source et ce qui reste à vérifier |
|---|---|---|---|
| 1 | Friches déclaratives, aucune vue récente du site | Image datée du site dans le rapport, à la demande et sans stockage : [ORTHO-SAT](https://www.data.gouv.fr/datasets/ortho-sat/informations) (IGN, Pléiades et SPOT 6/7), puis [BD ORTHO](https://www.data.gouv.fr/datasets/bd-ortho-r) (aérien, 20 cm) si plus fine | IGN, Licence Ouverte 2.0 ; tuiles servies sans compte le 1er novembre (HTTP 200, images non regardées) ; date de prise de vue de la tuile à lire, sans elle pas d'image |
| 2 | Maturité du projet pour la file « premier prêt, premier servi » | Pièces d'un dossier de raccordement (autorisations, maîtrise du foncier) en liste de vérification, sans note | Procédure RTE de 2026 et délibération CRE 2026-32 à relire ; aucune donnée à charger |

Les autres pistes attendent leur condition (lot B, « Pistes reportées ») ; le photovoltaïque
détecté et la température de surface sont écartés.

### Lot T : qualité des tests

But : que chaque cas d'usage du persona soit joué par un test sans réseau, et que l'application
soit essayée par une machine plutôt qu'à la main. Chaque étape a son critère ; aucune n'ajoute de
tâche planifiée.

**Constat** :
- 945 tests sans réseau, dont sept parcours dans Chromium ;
  couverture **94,6 %** des lignes et des branches
  d'`ingestion/` et de `reporting/`, plancher à 92,5 % ;
- reste sous 90 % : `ingestion/pipeline.py` à 86 % ;
- **défaut connu, non corrigé** : en Corse et outre-mer, le verdict annonce
  « HTA, Enedis (ou ELD) » puis « HTB, RTE » et « étude RTE indispensable », alors que la section
  « Tension du réseau » dit que le réseau n'y est pas exploité par RTE ; le seed
  `seuils_raccordement` ne connaît pas les zones non interconnectées. À corriger après lecture
  d'une source officielle sur le gestionnaire de chaque territoire (test en `xfail` strict dans
  `test_cas_usage.py`, qui échouera le jour où c'est corrigé) ;
- le rendu 3D de la carte (vues, tuile, animations) n'est vérifié qu'à la main (skill
  `essai-carte`) : les parcours du navigateur contrôlent la fiche et les panneaux, pas l'image ;
- la CI ne tourne que sous Ubuntu alors que le poste est sous Windows (console cp1252, chemins) ;
- sous Windows, le parcours « deux adresses » de `test_navigateur.py` échoue par intermittence : l'analyse des deux
  adresses dépasse le délai de 5 s de Playwright avant que la fiche côte à côte ne s'affiche. À
  rendre robuste avec T8.

| Ordre | Étape | Contenu | Critère |
|---|---|---|---|
| T8 | CI sous Windows | Un second job `windows-latest` (Java 17, mêmes commandes) | Vert sur une PR ; les pièges d'encodage sont pris avant le poste |

Ordre : relever le plancher de couverture ; T8 après une mesure du temps de CI. Hors périmètre : tests
de charge (un seul utilisateur, en local) et tests sur les API réelles en CI (elles tombent sans
prévenir : l'essai réel reste lancé à la main).

### Lot B : en attente

Rien à coder tant que la condition n'est pas levée.

| Étape | Pourquoi pour ce persona | Condition pour s'y mettre |
|---|---|---|
| **Carte** Refonte (lots B à F, voir plus bas) | Une carte lisible imprimée, qui montre ce que la puissance demandée change | Lot B d'abord : prototype three.js r186 hors ligne |
| **8** Repère de prix au-delà de 150 000 MWh/an, après la fin de l'ARENH | Un projet de 20 MW constant dépasse la dernière tranche Eurostat (IG, sans borne) | Une source officielle et ouverte d'un prix à terme ; le classement pondéré dans `compare` reste écarté |
| **30** Modèle prédictif d'implantation (cadrage plus bas) : par zone d'emploi, probabilité d'implantations nouvelles à 3 ans, apprise sur l'historique | Signaler tôt les territoires qui montent, en complément des faits à l'adresse ; il dira où les projets vont, pas où ils seraient rentables ni si la puissance est disponible | Données fiabilisées (critères à écrire) ; à concilier avec la « comparaison sans classement » ; historique de la capacité en soutirage, absent à ce jour |
| **6** Extension internationale, voir plus bas | En dernier | Jeton ENTSO-E et contours des zones de prix |

**Pistes reportées**, tirées du lot A : chacune attend sa condition, aucune ne se code avant.

| Manque | Piste | Condition |
|---|---|---|
| Taux de charge réel : `--conso` doit être donnée, rien n'est déduit de `--puissance` (un data center n'utilise qu'environ 20 % de la puissance demandée, chiffre relayé) | Consommation par défaut déduite de `--puissance` et `--activite`, dite hypothèse | Une source officielle du taux de charge par activité ; sans elle, rien |
| Coût du raccordement, dont la réfaction des bornes de recharge (part prise en charge par le tarif) | Ordre de grandeur du coût selon le domaine de tension | Barème de raccordement d'Enedis et texte de la réfaction (arrêté et délibération CRE) ; rien sans texte |
| Périmètre des zones « prêtes à raccorder » de RTE | Les zones sont déjà sur la carte et dans « Raccorder » par un point de référence (seed `zones_raccordement_rte`) ; le contour en plus | Périmètres publiés par RTE |
| Artificialisation (zéro artificialisation nette) de la commune | Ligne « enveloppe restante » du guide de la DGE ; couverture et usage à l'adresse par [OCS GE](https://www.data.gouv.fr/datasets/ocs-ge) (IGN, Licence Ouverte 2.0) | Portail de l'artificialisation des sols (Cerema), licence ; OCS GE n'a pas de couche nationale en WFS (téléchargement par département, tuiles non testées) |
| Mouvements du sol, en plus des argiles et des cavités | [EGMS Ortho](https://land.copernicus.eu/en/products/european-ground-motion-service) (Copernicus, radar Sentinel-1, grille de 100 m, [accès libre, source à citer](https://land.copernicus.eu/en/data-policy)) | Un accès programmatique ; le téléchargement passe par l'Explorer seulement |
| Inondation à l'adresse quand le rapport Géorisques ne répond pas | Inondations observées : [GFM](https://extwiki.eodc.eu/GFM/PDD/Introduction) et [Rapid Mapping](https://mapping.emergency.copernicus.eu/terms-and-conditions/) (Copernicus, Sentinel-1), dites observées au passage du satellite : une absence ne prouve pas l'absence de risque | Licence de GFM à lire |
| Pente et bâti fin d'un terrain | Dérivés du [LiDAR HD](https://www.data.gouv.fr/datasets/nuages-de-points-lidar-hd) (IGN, Licence Ouverte 2.0) | Dalles de 0,5 à 2 Go : un accès limité à l'adresse ; couverture à vérifier |
| Besoin de recharge poids lourds au dépôt | Situer l'adresse dans une zone logistique à fort besoin de puissance (étude Enedis de décembre 2025) | Maille des zones logistiques (SDES) et sa licence |
| Climat futur du site | Projections climatiques (DRIAS, Explore2) | Licence de DRIAS ; date de la carte RGA 2026 |

### À se rappeler aux dates

Rien à faire avant ces dates ; le moment venu, à lancer à la main (aucune planification) :
- 1er janvier 2027, **étape 19**, capacité d'accueil en soutirage, première question du persona et sans réponse publique aujourd'hui : refonte de Caparéseau et éventuelle carte des
  capacités en soutirage de RTE (délibération CRE 2026-173), à intégrer dès publication ; la
  capacité d'Enedis en HTA reste réservée à son portail client. Le nouveau calendrier HTB de
  la même date est déjà codé : rien à faire ;
- 1er janvier et 1er février 2027 : tarifs réduits de la loi de finances 2027 et indexation du tarif
  normal de l'accise, lignes à clore et à ajouter au seed `accise_electricite` ;
- 1er août 2027 : nouvelle grille TURPE 7 HTA-BT et HTB, à reporter dans les seeds
  `turpe_soutirage` et `turpe_gestion_comptage` (les sorties le signalent une fois la date passée) ;
- avril et octobre : actualisation de DVF, puis `refresh` ;
- à chaque nouvelle publication de la base Diplômes-Formation (Insee) et de l'enquête BMO
  (France Travail, chaque printemps) : changer le millésime dans `diplomes_url` et `bmo_url`
  (`ingestion/config.py`), puis `refresh` ;
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

**Europe de l'Ouest, pays par pays** : ordre à suivre pour étendre le
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

## Refonte de la carte et direction artistique

La charte commune est en place ; le reste est une proposition. Elle garde les règles en
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
tension, gestionnaire, délai. Friches, zones d'activité, desserte et eau
ne sont aujourd'hui que dans `diagnose`, pas sur la carte (lot F).

### Direction artistique : « le relevé »

Ce que l'outil promeut : une décision d'implantation d'un projet de 1 à 50 MW fondée sur des faits
publics, vérifiables, avec leurs limites, et une électricité bas-carbone ; le lecteur est un
développeur de data center, un opérateur de recharge, un industriel ou un logisticien, souvent
ingénieur, qui présente le site à un comité de direction. La direction qui en découle est celle d'une
**carte d'état-major moderne** : relief ombré à la manière des cartes IGN et suisses (Imhof), encre
et papier, chiffres en avant, chaque couche signée de sa source comme un cartouche. Ni jeu vidéo,
ni tableau de bord néon : la sobriété est le message (le carbone, la péréquation, la capacité non
publique se disent calmement). Références étudiées : le relief des cartes de Swissgrid et le
« Landscape of Power Flows » de Nadieh Bremer pour Swissgrid (données réseau traitées en paysage),
les cartes en relief de la presse de données.

| Élément | Proposition | Pourquoi |
|---|---|---|
| Fond | En place : papier clair, encre vert nuit, mer en bleu gris désaturé (`reporting/charte.py`). Reste au lot E : relief en ombrage gris chaud | Même identité que le PDF (fond clair) ; lisible imprimé et en capture pour un comité de direction |
| Accent | Un seul : vert « décarboné » pour ce qui est favorable et sourcé ; orange brûlé pour « à vérifier » ; l'échelle rouge → blanc → vert reste réservée à la note des cases | Le vert porte le message bas-carbone |
| Typographie | En place : **Spectral** (Production Type, Paris ; SIL OFL) pour les titres, **Inter** (OFL, chiffres tabulaires) pour le texte et les nombres ; embarquées en base64 dans la page et enregistrées dans reportlab pour le PDF | Même rendu d'un poste à l'autre ; « ₂ », « ≥ » et « → » présents dans le PDF |
| Maquettes | Gardées (centrales, fret, sites) mais en « maquette d'architecte » : blanc cassé mat, une seule couleur de filière en touche, ombres douces | Elles restent la signature de la carte ; moins de couleurs, plus de lisibilité |
| Mouvement | Une arrivée de caméra (France → adresse, 1,5 s), transitions entre vues ; tout coupé sous `prefers-reduced-motion` | L'effet « avant-gardiste » vient de la mise en scène, pas de l'accumulation d'effets |
| Interdits | Ni Marianne, ni bloc-marque, ni police Marianne du système de design de l'État | Réservés à l'État (charte de la marque État, loi de 1918 sur les sceaux) : l'outil n'est pas un service public |

Pas encore faits : les maquettes en « maquette d'architecte » et l'arrivée de caméra continue.

### three.js : ce qui sert le persona

Chaque effet doit répondre à une question du persona ; sinon il n'entre pas.

| Fonction | Rendu three.js | Question servie | Données |
|---|---|---|---|
| « Raccorder » selon la puissance | Autour de l'épingle, un anneau par domaine de tension (HTA, zone grise, HTB) teinté selon `--puissance` ; zones RTE en colonnes de lumière dont la hauteur dit l'année de capacité ; contraintes RTE en voile sur les cases | Qui raccorde mes 12 MW, et quand ? | existant (`seuils_raccordement`, SDDR, contraintes) |
| Capacité en soutirage (étape 19) | Cases teintées par capacité restante dès que RTE la publie ; d'ici là, rien d'inventé | La puissance est-elle là ? | RTE, 1er janvier 2027 |
| `compare` sur la carte | Plusieurs épingles numérotées ; caméra qui passe d'un site à l'autre ; fiches côte à côte, **sans classement** | Le persona présélectionne 2 à 6 sites | existant |
| Foncier et contraintes | Sites clés en main, friches et zones d'activité en maquettes ; ICPE et Seveso en marqueurs discrets | Le terrain est-il prêt et constructible ? | `fct_friches`, `ingestion/zones_activite.py`, voisinage Géorisques |
| Desserte | Temps vers le port, l'aéroport, le rail-route, tracés en arcs depuis l'épingle | Logistique et industrie | `ingestion/acces_routier.py` |
| Relief continu | Un maillage de terrain par système électrique tiré de l'altimétrie IGN déjà chargée, ombrage calculé dans le shader, hexagones projetés dessus en filet | Remplace les cônes de montagne ; donne la lecture « carte d'état-major » | existant (`ingestion/hexagones.py`) |
| Carbone heure par heure (Corse, outre-mer) | Curseur de 24 h : lumière du soleil et couleur du réseau suivent `fct_carbone_horaire_zni` | Seul cas où l'heure de consommation change le carbone | existant |
| Export d'image | Bouton « Image » : PNG haute définition de la vue, avec cartouche (adresse, date, sources) | La carte finit dans une présentation | — |
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
| B | Prototype puis passage à three.js r186 (`WebGLRenderer`, `BatchedMesh` pour les maquettes) | carte sans requête réseau ; 60 images/s sur la métropole |
| C | « Raccorder » selon la puissance : anneaux des domaines de tension, zones RTE en colonnes datées | 1, 12, 30 et 50 MW à Chilly-Mazarin et Dunkerque : gestionnaire conforme à `seuils_raccordement` |
| D | `compare` épinglé sur la carte (le tableau est déjà dans l'application) ; export d'image ; fiche sans WebGL | `compare` à 3 sites |
| E | Relief continu et ombrage ; curseur horaire des îles | Ajaccio : 522 g vers 13 h, 681 g vers 5 h |
| F | Couches friches, zones d'activité et desserte (déjà calculées), voisinage Géorisques, puis capacité en soutirage à sa publication | une adresse contrôlée à la main par couche |

## Besoins des porteurs de projets

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
été trouvé (requêtes sans résultat) ; l'état des obligations de l'ADEME et de l'Arcep sur le WUE
et la chaleur fatale n'est pas établi.

## Ce que les recherches ont établi

- **Les besoins des entreprises**, dans l'ordre : de la puissance disponible à une date connue
  (premier critère des gros projets ; zones RTE P1 dès fin 2028, P2 dès 2029) ; du foncier
  viabilisé ; une électricité bas-carbone ; un prix prévisible, qui ne décide que pour les
  électro-intensifs et les data centers ; la connaissance des risques du site.
- **En France, ni le prix ni le carbone ne dépendent de l'adresse** : d'où un diagnostic de
  contexte par adresse plutôt qu'un « prix à l'adresse ».
- **Limites de l'open data** : capacité de raccordement non publiée ; postes RTE sans
  coordonnées (maille du département seulement) ; ~40 % des IRIS sous secret statistique
  depuis 2022.
- **Sources de données (toutes intégrées)** : RTE a retiré les coordonnées
  GPS de ses postes et de ses lignes « pour des raisons de sécurité publique » (jeux ODRÉ au 16 juin
  2026), et EDF a fait de même pour ses postes sources ; le seul jeu de postes sources Enedis
  géolocalisés est local (agglomération du Puy-en-Velay). Toute distance à un poste est donc
  exclue. Enedis et l'Agence ORE ont quitté l'API Opendatasoft pour data-fair
  (`/data-fair/api/v1/datasets`) : l'ancienne API répond 410. La capacité en soutirage d'Enedis
  reste réservée au portail client entreprise ; l'outil Toprac de RTE, cité par la CRE, n'a pas de
  version publique trouvée.
- **Ce qui peut bouger** : la CRE impose à RTE de refondre Caparéseau
  d'ici le 1er janvier 2027 et RTE étudie une carte de ses capacités en soutirage (délibération
  CRE 2026-173) : à intégrer dès sa publication. Enedis publie ses postes HTA/BT géolocalisés
  (licence ouverte) ; pas de jeu national des postes sources géolocalisés (voir ci-dessus).
- **Données géographiques (étapes 13 à 16, conclusions seulement)** : trois
  manques sans source ouverte, les loyers et annonces de locaux, la capacité d'accueil en
  soutirage, les parcelles et leurs propriétaires (fichiers fonciers du Cerema, réservés aux ayants
  droit sous convention). Imagerie 100 % ouverte au lancement (BD ORTHO 20 cm, LiDAR HD) ;
  l'imagerie commerciale (Pléiades Neo, 22 à 33 $/km² chez un revendeur) ne se justifie que pour un
  site précis.
- **Prix des locaux (étape 12)** : il n'existe pas de jeu officiel et ouvert
  des loyers d'activité par commune (la carte des loyers du ministère et de l'ANIL ne couvre que le
  logement ; Cushman & Wakefield, Geolocaux et Ayming sont privés) ; DVF (DGFiP) est la seule
  source officielle des prix de l'immobilier d'activité, d'où le prix de vente seul.
- **Imagerie satellite ouverte (pistes dans le lot A)** : aucune image ne dit
  la capacité de raccordement ; elle peut renforcer le foncier, l'artificialisation et les risques.
  Les produits déjà dérivés passent avant les images brutes, qui demandent une chaîne de traitement.
  Ordre proposé : OCS GE, ORTHO-SAT à la demande, puis EGMS. Hors de France (étape 6) :
  [HRL Imperméabilisation](https://land.copernicus.eu/en/products/high-resolution-layer-imperviousness)
  et [GHS-BUILT-S](https://registry.opendata.aws/ghs-built-s/) (CC BY 4.0) à la place d'OCS GE.
  [Sentinel-1 et 2](https://ewds.climate.copernicus.eu/licences/ec-sentinel) bruts seulement si aucun
  produit dérivé ne répond ([12 To sur 30 jours](https://documentation.dataspace.copernicus.eu/Quotas.html),
  compte requis pour ces quotas). Non retenus : Dinamis (utilisateurs non commerciaux, charte lue en
  extrait), OSO ([fiche sans fichier](https://www.data.gouv.fr/datasets/cartographie-de-loccupation-du-sol-oso),
  licence à confirmer auprès de Theia), SPOT World Heritage (licence contradictoire selon les pages,
  pas d'image récente), ESA WorldCover (2020 et 2021 seulement dans les résultats trouvés).
- **Main-d'œuvre qualifiée** : aucune source officielle française
  ne fixe de métrique ni de temps de trajet ; le décompte par temps de route et niveau de diplôme
  est une pratique de profils d'attractivité britanniques
  ([Cheshire West, 45 min](https://www.cheshirewestandchester.gov.uk/asset-library/statistics/labour-market-profiles/Labour-Market-Overview-Chester-2020.pdf),
  extrait de recherche, PDF non lu). Écartés comme métrique principale : la part de diplômés (une
  petite commune très diplômée pèse autant qu'un grand bassin), les
  [cadres des fonctions métropolitaines](https://www.insee.fr/fr/statistiques/1893116) (emplois
  « stratégiques », loin des métiers d'exploitation). La métrique compte des résidents, pas des
  candidats disponibles : ni concurrence entre employeurs, ni spécialité du diplôme. Licences :
  [Insee](https://www.insee.fr/fr/information/2008466) (Licence Ouverte 2.0), BMO (Licence Ouverte). Besoin des data centers en électriciens et
  techniciens CVC : [communiqué de Data4](https://data4group.com/wp-content/uploads/2024/11/D4FDCstudy_France-Best-Place-to-build-AI-ready-DC.pdf)
  et offres d'emploi, sans chiffre public français.
- **Modèle prédictif (étape 30, rien de décidé)** : « le plus intéressant »
  n'est pas observable ; la cible mesurable est l'implantation constatée. Cible candidate : surface
  de locaux non résidentiels autorisée à 3 ans ([Sitadel](https://www.data.gouv.fr/datasets/689c42fa521ccf80ce954f83/),
  depuis 2013, Licence Ouverte ; champs de surface et de destination à vérifier), recoupée avec les
  créations d'établissements ([Sirene](https://www.insee.fr/fr/information/3591226), historique
  depuis 1973). Maille : [306 zones d'emploi](https://www.insee.fr/fr/information/4652957), soit
  environ 3 000 lignes zone × année : un modèle simple et explicable, à comparer d'abord à la simple
  reconduction de la tendance passée, qu'il doit battre. Contrôle sur le passé par blocs de temps et
  d'espace : une séparation aléatoire
  [surestime les scores](https://meetingorganizer.copernicus.org/EGU23/EGU23-16096.html) et les
  [fuites d'information](https://arxiv.org/abs/2207.07048) sont le premier risque ; chaque variable
  doit être connue à sa date (zones RTE du SDDR 2025 et sites clés en main exclus des années
  antérieures). Limites : ce qui a été construit n'était pas forcément un bon choix (les projets
  abandonnés manquent) ; capacité de raccordement sans historique ; file d'attente changée fin 2026,
  donc un passé peu représentatif ; trop peu de data centers pour les apprendre seuls (huit raccordés
  à RTE, chiffre relayé). Une méthode publiée propre à ce sujet n'a pas été trouvée.

## Règles en vigueur

| Règle |
|---|
| Prix historiques par tranche Eurostat, pas de prix horaire ; scénarios à 3 ans |
| Comparaison sans classement |
| Verdict sur le facteur ADEME en cycle de vie, au seuil de 100 gCO₂e/kWh de la taxonomie |
| Local et à la demande uniquement : aucune exécution planifiée, aucun hébergement ; le projet se présente depuis le dépôt GitHub et se lance sur son poste |
| Sécurité : CI en lecture seule, actions figées par commit, HTTPS seulement, identifiants dans `identifiants.local.txt` (ignoré par git) |
| Carte : page HTML autonome, three.js embarqué, grille hexagonale maison plutôt que H3 |
| Outils Python et PySpark privilégiés : ingestion en PySpark (Java 17 requis, en local et en CI), silver et gold en dbt-duckdb |
| `--ca` sans seuil : le statut d'électro-intensif se juge sur la valeur ajoutée ; `--ca` exige `--conso` |
| Adresse refusée plutôt que devinée : score BAN sous 0,65, deux communes à moins de 0,05 l'une de l'autre, ou code postal demandé absent des résultats |
| Risques : rapport Géorisques à l'adresse d'abord, puis export GASPAR local (maille commune), puis API commune |
| Critère risques : « attention » seulement si un plan de prévention (PPRN, PPRT, PPRM) approuvé, anticipé, prescrit ou prorogé couvre la commune (export GASPAR, plans caducs écartés) ; sinon « info » avec les risques recensés ; « inconnu » si les plans ne sont pas chargés |
| Chaque sortie donne la date de chargement de ses sources ; au-delà de 45 jours, elle demande un `refresh` |
| Une étape n'est validée qu'après un test complet : ruff, pytest (dont `dbt build`), essai réel |
| `--va` : électro-intensité = consommation × accise au tarif normal du seed (26,35 €/MWh depuis le 1er août 2026) ÷ valeur ajoutée, paliers 0,5 %, 2,25 % et 6,75 % depuis le 1er janvier 2026 (CIBS L312-65, L312-71) |
| `--courbe` : export Enedis lu en Python à la demande, jamais stocké ; horodate = fin du pas ; tient lieu de `--conso` et `--puissance` s'ils manquent ; coût au barème HP/HC en vigueur, seulement jusqu'à 36 kW |
| Carbone horaire des îles : profil de la part thermique calé sur le facteur ADEME annuel, plutôt que des facteurs par filière non sourcés ; imports de la Corse comptés non thermiques |
| UE-27 : moyennes nationales seulement (Eurostat depuis 2024-S1, deux derniers semestres ; Ember, dernière année, production sans échanges) ; jointure des trois codes pays par le seed `dim_pays_ue` ; hors UE refusé |
| Commandes de question : journal masqué (niveau WARNING, message seul), chemins des fichiers écrits sur stderr ; warehouse absent ou gold incomplet → `DonneesAbsentesError` et commande `refresh` à lancer |
| Carte : une carte par système électrique, placées à 600 unités l'une de l'autre dans la même scène (le brouillard cache les autres) plutôt qu'une page par territoire |
| Pôles de production : registre ODRÉ agrégé à la source par commune et filière (installations en service d'au moins 10 MW ; les noms « Confidentiel » interdisent une clé par installation), centre de la commune comme position ; seuil 200 MW sur le continent, 20 MW en Corse et outre-mer ; stockage hors barrages exclu |
| `main` protégée par un ruleset GitHub : PR obligatoire (sans approbation), job CI `ruff + pytest + dbt build` vert sur une branche à jour, ni force-push ni suppression, aucun contournement |
| Accise au tarif normal dans le seed daté `accise_electricite` (ligne sans `date_fin` = barème en vigueur) ; passé le 1er février suivant sa date d'effet, `--va` demande de le mettre à jour |
| Fourchette de prix à 3 ans élargie pour contenir toujours le prix des 12 derniers mois ; dite « indicative » tant que moins de 20 périodes sont contrôlées sur le passé (variable dbt `prix_nb_periodes_validation`) |
| Bronze : un Parquet n'est jamais écrasé ; un même `run_id` le même jour prend le suffixe `-2`, `-3`… |
| Tempo hors `refresh` : il demande un compte RTE, que les autres commandes n'exigent pas ; `tempo --month` se lance avant `report` |
| Nouvelles sources : producteur officiel et licence ouverte seulement, méthode publiée ou bibliothèque maintenue et testée ; une copie tierce sert au mieux de repli signalé |
| Chaque nouvelle source suit l'architecture médaillon : extraction vers un Parquet bronze immuable puis `MERGE` DuckDB, silver nettoyé et gold en dbt, limite dans `limites_methodologiques` |
| Enedis par adresse : pages CSV data-fair de 10 000 lignes (le JSON prenait 9 min pour une année), dernière année publiée seulement ; adresse rapprochée sur « numéro + voie » normalisés, seulement si la BAN trouve le numéro ; une consommation à l'adresse prouve un raccordement, pas une puissance disponible |
| Carte : fausses couleurs réservées à « Raccorder » (rouge → blanc → vert, sans cônes de montagne) ; « Territoire » revient aux couleurs réelles et au relief IGN, l'encart donne carbone et prix moyen par tranche (`fct_prix_entreprise_tranche`) ; frontières régionales en gris sombre |
| Carte : sites clés en main en maquette posée sur la case (dalle, hangar, bureaux, mât au drapeau doré) ; centrales en maquettes aussi dans « Raccorder » ; survol d'un site : son nom |
| Carte : un son de tuile ne dit rien que la fiche ne dise ; il suit la filière dominante (plus de la moitié de la puissance en service sur la case), rien à défaut ; jamais de lecture sans clic sur « Son » |
| Acheminement : TURPE 7 HTA-BT (HTA à pointe fixe, BT > 36 kVA ; le tarif bleu l'inclut déjà) et HTB 1 à 3 (délibération CRE 2026-104, appliquée dès 40 MW, HTB 1 par défaut) ; une même puissance souscrite dans toutes les classes ; énergie répartie sur la courbe de charge, sinon sur toutes les heures de l'année ; heures creuses 22 h – 6 h, pointe 9 h – 11 h et 18 h – 20 h de décembre à février hors dimanche ; version courte ou longue utilisation au moindre coût ; gestion en contrat unique (Rf inclus), comptage communicant. Montant donné comme part déjà comprise dans le prix moyen Eurostat |
| Zonage sismique : couche WFS de Géorisques plutôt qu'un seed (le tableur de planseisme.fr n'est plus en ligne, le fichier wpd reste écarté) ; zone trouvée par l'adresse dans son polygone, ce qui évite le recalage sur le code officiel géographique ; pages triées par `gid` (sans tri, 84 polygones perdus) et nombre de polygones contrôlé contre le total annoncé ; sur une limite, la zone la plus forte ; le critère « risques » du verdict reste réglé par les seuls plans de prévention |
| Continuité d'alimentation : jeu d'Enedis, producteur, plutôt que la copie de l'Agence ORE ; colonnes d'années lues dans le schéma du jeu (une de plus par publication) ; comparaison au seuil de 3 % de l'arrêté du 24 décembre 2007 (article 3) et à la médiane des départements publiés la même année ; repère dans la section « Raccordement » de `diagnose`, sans entrer dans le verdict (moyenne départementale, pas la ligne du site) |
| Pôles de fret : ports sur les marchandises hors tare de la dernière année publiée (SDES), seuil 5 Mt sur le continent et 0,5 Mt en Corse et outre-mer ; aéroports sur le fret et la poste de la dernière année complète (DGAC), 20 000 t et 3 000 t ; chantiers rail-route de SNCF Réseau (ODbL, figé au 23 mars 2022) sur au moins 2 km de voies de manutention, dit repère de capacité (aucun tonnage publié) ; HAROPA placé au Havre ; ports et aéroports au centre de leur commune ; ports fluviaux écartés (pas de maille port). Carte seulement, sans ligne dans `limites_methodologiques` (réservées aux chiffres de `diagnose`) |
| Seeds recréés à chaque `dbt build` (`+full_refresh`) : un seed qui gagne une colonne bloquerait sinon le warehouse existant |
| Prix de vente des locaux d'activité (DVF) gardé en information ; **pas de loyer** (aucune source officielle et ouverte) ; « non disponible » sans ventes, jamais de chiffre inventé |
| DVF : ventes de locaux d'activité d'un seul bien (ni logement, ni plusieurs dispositions), surfaces de 15 à 5 000 m² et prix de 100 à 15 000 €/m² (variables dbt `locaux_*`) ; médiane sur les cinq dernières années de ventes, fiable à partir de 5 ventes (`locaux_min_ventes`), sinon le département ; arrondissements de Paris, Lyon et Marseille ramenés à la commune ; fichiers annuels lus en flux (~100 Mo chacun), le pilote Spark passe à 3 Go (400 000 lignes saturaient 1 Go) ; Bourg-en-Bresse 1 179 €/m², Lyon 3 298, Paris 7 760 |
| Documentation en français, dans README et ce plan seulement ; licence MIT. Commentaires réservés à la logique du code (règle, piège d'une source, raison d'un choix) : plus d'en-tête d'utilité au-dessus de chaque fonction ni de chaque modèle dbt ; code le plus simple possible, en Python, PySpark et dbt |
| Consolidation : `essentiel`, `diagnose` et `compare` partagent les mêmes options de projet (`--puissance`, `--conso`, `--ca`, `--va`, `--surface`, `--courbe`, puis `--activite`) et les mêmes messages d'erreur ; essai de bout en bout après `refresh` complet |
| Profils de charge réels : agrégat Enedis `conso-sup36-region`, plage « total > 250 kVA » (P7), industrie et tertiaire, 12 derniers mois publiés, filtré à la source (843 072 demi-heures au lieu de 34,6 M) ; les courbes masquées par le secret statistique sont écartées ; journée type par heure locale, jours ouvrés et week-end (jours fériés comptés ouvrés), moyenne des régions pondérée par le nombre de points ; `puissance_flexible_kw` à zéro (Enedis ne dit rien de la flexibilité) ; ajoutés à `fct_cout_horaire_profil` à côté du seed, qui reste le profil par défaut |
| Ecowatt : l'API répond (HTTP 200) depuis l'application Ecowatt ; elle ne publie que le jour et les trois suivants, sans historique : la commande `ecowatt` se lance à la main, hors `refresh` (compte RTE requis, comme `tempo`), et le dernier niveau relevé d'un jour remplace le précédent ; le rapport dit combien de jours ont été relevés plutôt que de les supposer verts |
| Accise au tarif normal : le formulaire DGFiP 2040-TIC-SD (2026) donne 26,35 €/MWh pour les entreprises à compter du 1er août 2026 (20,42 de tarif normal + 5,93 de majoration), après 26,58 du 1er février au 31 juillet 2026 |
| Zonage d'urbanisme : zone du point par l'API Carto de l'IGN (module GPU, sans clé), appelée à la demande par `diagnose` et `compare` sauf sous `--sans-risques` ; la plus contraignante des zones superposées (A ou N) passe devant ; type de zone seulement, la destination autorisée se lit au règlement (lien donné s'il est publié) ; zone A ou N : critère « foncier » à vérifier ; aucune zone : « document non publié ou non numérisé », jamais un chiffre inventé. Pas de stockage local (couverture et version du document changent) |
| Friches : fichier national « Sites référencés dans Cartofriches » (data.gouv.fr, lien permanent de la ressource, Cerema, Licence Ouverte v2, Windows-1252, séparateur « ; », « NA » pour l'absent) chargé par `refresh` (étape `friches`) ; retenues : surface d'unité foncière d'au moins 1 ha (variable dbt `friches_surface_min_m2`) et statut autre que « friche reconvertie » ; sites sans surface écartés plutôt que supposés ; distance à vol d'oiseau, rayon de 10 km ; inventaire déclaratif et incomplet, dit comme tel ; les zones d'activité n'y figurent pas |
| Réseaux de chaleur : archive mensuelle « Tracés des réseaux de chaleur et de froid » (France Chaleur Urbaine, Licence Ouverte 2.0), choisie dans la fiche data.gouv.fr du jeu (la ressource change de nom à chaque publication) et chargée par `refresh` (étape `reseaux-chaleur`) ; couches des réseaux de chaleur avec et sans tracé, ni froid ni en construction ; Lambert-93 converti en WGS84 dans le code (IGN, sans dépendance) ; un point gardé tous les 100 m de tracé et aux deux bouts de chaque ligne ; points hors de la métropole et de la Corse écartés (coordonnées fausses dans la source) ; nom du réseau en guise d'identifiant quand l'identifiant national manque ; silver limité au dernier chargement ; distance à vol d'oiseau, rayon de 10 km ; ni capacité, ni température, ni accord de l'exploitant connus |
| Zones d'activité : couche `zone_d_activite_ou_d_interet` de la BD TOPO (WFS de la Géoplateforme, Licence Ouverte), nature « Zone industrielle » (zones d'activité, industrielles et artisanales confondues) ; boîte de 5 km, distance au bord du polygone et surface calculées dans un plan local, plafond de 500 objets ; à la demande, comme le zonage ; ne vaut pas constructibilité ni disponibilité |
| Eau, arrêtés du jour : VigiEau (API publique du ministère de la Transition écologique, sans clé, profil « entreprise ») à la demande, sous `--sans-risques` coupé comme Géorisques et l'urbanisme ; niveau le plus haut des zones en vigueur (vigilance, alerte, alerte renforcée, crise) ; situation du jour, pas un risque futur ; aucun arrêté : dit ; volumes BNPE écartés (ils ne mesurent pas la tension). Sans effet sur le verdict : la ressource réellement utilisée par un data center ne se déduit pas de l'adresse |
| Accès routier : calcul d'itinéraire de la Géoplateforme (ressource `bdtopo-pgr`, profil voiture) vers les pôles de fret de `fct_poles_fret` (centre de la commune pour ports et aéroports, ordre de grandeur) ; pas de profil poids lourd, dit dans la limite ; l'échangeur autoroutier n'est pas identifiable dans la BD TOPO (bretelles sans classement) : seule la distance à vol d'oiseau jusqu'au tronçon « type autoroutier » classé autoroute (10 km au plus) est donnée ; à la demande |
| Bâtiment et solaire : bâtiment de la BD TOPO à 60 m au plus de l'adresse (le plus proche si l'adresse n'est pas dessus) et PVGIS 5.3 (JRC) pour un kWc de panneaux fixes, inclinaison et orientation optimales, 14 % de pertes ; aucun dimensionnement de centrale déduit de l'emprise (hypothèses non sourcées) |
| Étoile en schéma `etoile` au-dessus du gold, sans renommer les `fct_*` ; `dim_commune` reprend les communes des faits absentes du référentiel (clé étrangère jamais orpheline) et porte le centre de la commune. Le diagnostic lit l'étoile quand le grain est la commune, le département ou le jour ; il garde le gold pour les polygones, les distances et le repli du prix des locaux sur le département. Une année publiée devient le 1er janvier de l'année (`jour_reference`) pour sa clé vers `dim_date`. Dimension des départements nommée `dim_departement_region` : le seed `dim_departement` porte déjà le nom |
| Accise réduite selon l'activité : `--activite` choisit aussi le profil de charge de l'acheminement (journée type Enedis > 250 kVA pour l'industrie, puissance constante dite telle pour les autres) ; tarifs de la loi de finances 2026 (loi n° 2026-103) lus dans le formulaire DGFiP 2040-TIC-SD 2026, en mode « table » de pdftotext (le mode « layout » décalait d'une ligne libellés et montants ; alignement vérifié sur les tarifs normaux, dont le montant figure dans le libellé), et recoupés avec le texte consolidé du CIBS sur Légifrance : 5,5 €/MWh (grande consommatrice, dès 0,5 % d'électro-intensité), 3 (électro-sensible, dès 2,25 %), 0,5 (électro-intensive, dès 6,75 %), 0,5 (exposée à la concurrence internationale, L312-72), 10 pour les centres de stockage de données sur la fraction au-delà de 1 GWh par an (L312-64, L312-70) ; seed `accise_electricite` à une ligne par catégorie. Tarif réduit total, sans majoration ZNI à part (le formulaire n'en déclare pas). Industriel : seulement sans `--activite` ou avec `industrie` (sections B à E de la NACE) ; data center : refusé quand `--va` montre moins de 2,25 % (condition 8 de L312-70), les sept autres conditions dites, pas vérifiées ; aucune réduction pour la recharge ni l'entrepôt. Montants donnés à côté du prix moyen Eurostat, sans l'en retrancher (on ignore la part d'accise réduite dans la moyenne de la tranche) |
| Voisinage du site : API Géorisques à la demande, coupée par `--sans-risques`, en plus du rapport à l'adresse et indépendante de lui (l'un peut répondre sans l'autre). Rayons choisis par l'outil, aucun texte n'en fixe : argiles au point, cavités et sols pollués à 500 m (filtre de l'API, sans distance : SIS, SUP et instructions n'ont qu'un polygone), installations classées à 1 km, Seveso à 5 km ; seules les installations sous autorisation ou enregistrement « en exploitation avec titre » sont retenues (« Non ICPE » et « Autres régimes » n'ont ni titre ni état) ; un ancien site CASIAS n'est pas un sol pollué avéré. Le critère « risques » du verdict reste réglé par les plans de prévention (un Seveso seuil haut impose un PPRT, déjà compté) : le voisinage complète son détail (Seveso le plus proche, SIS) sans changer son état |
| Charte : direction « le relevé » sur fond papier clair (`#F4F1EA`), encre `#14202B`, vert `#2E7D5B`, orange `#C2571A`, dans `reporting/charte.py`, seule source des couleurs de la carte (variables CSS injectées, lues par la scène), du PDF et des graphiques ; Tempo garde ses couleurs. Spectral (titres) et Inter (texte, chiffres), licence OFL, depuis le dépôt Google Fonts, réduites au latin par fontTools, en base64 dans la page (`font-src data:`) et enregistrées dans reportlab et matplotlib ; l'échelle de note rouge → blanc → vert est gardée ; anneau des zones RTE et lien vers la zone à l'encre, plus en cyan additif (invisible sur papier) |
| Application (`app`) : serveur `http.server` de la bibliothèque standard, sans nouvelle dépendance, lié à 127.0.0.1 et lancé à la main (ni tâche planifiée ni service) ; la carte reste une page autonome qui s'ouvre aussi en `file://`, en lecture seule. Après une analyse, le serveur épingle le verdict dans `carte.html` et la page se recharge, plutôt que de rejouer l'épingle en JavaScript ; une carte refaite (données ou gabarit plus récents) reprend l'épingle de la précédente. Port 8770. Les rapports reprennent `generer_rapport_implantation` ; le Markdown est rendu sans graphiques, pour un seul fichier à télécharger. Les appels à la demande de `diagnose` sont regroupés dans `completer_en_ligne`, partagé par `diagnose`, `compare` et l'application. Touches solarpunk posées sur la direction « le relevé » sans la remplacer (`SOLEIL` et `FEUILLE` dans `reporting/charte.py`). L'épingle de l'adresse quitte le rouge, réservé au défavorable dans la note des cases |
| Démonstration : `demo` réutilise tel quel le jeu synthétique des tests (`ingestion/tests/medallion.py`), sans copie à tenir à jour ; base construite une fois dans `data/demo/`, effacée si la construction échoue. Projet fictif de 2 MW et 15 000 MWh à Lyon plutôt que l'exemple de référence : la base synthétique n'a ni Chilly-Mazarin ni la tranche IF. Aucun géocodage (adresse fixe) ni appel à la demande ; seule la carte télécharge contours et relief, et la commande réussit sans elle. La ligne « plus de 45 jours, relancer refresh » reste : l'avertissement de première ligne dit que la base est fictive |
| Calendrier HTB, distinct de la HTA (délibération CRE 2025-77, texte du tarif HTB 1) : week-ends et jours fériés entièrement creux ; jusqu'au 31 décembre 2026, creuses de 23 h à 7 h ; dès le 1er janvier 2027, 22 h - 6 h en saison haute, 2 h - 6 h et 12 h - 16 h en saison basse, sauf Nouvelle-Aquitaine et Occitanie (2 h - 4 h et 10 h - 16 h, puis 10 h - 18 h, pointe dès 7 h). Le régime suit la date de chaque heure : une année de grille à cheval sur le 1er janvier compte les deux, sans attendre la bascule. Les deux régimes de 2027 ont autant d'heures de chaque classe : à puissance constante, la région ne change rien ; seul un profil variable la fait apparaître. À 45 MW et 300 000 MWh : 3,79 M€. Délibération 2026-33 lue en recherche de ces plages : elle ne touche qu'aux heures méridiennes de la HTA-BT. Jours fériés légaux, sans ceux d'Alsace-Moselle (la délibération 2025-77 ne les cite pas) ; HTA-BT inchangée |
| Monde en tuiles : Natural Earth 5.1.2 (`ne_50m_admin_0_countries`, domaine public, déjà au projet pour les terres), préféré aux limites officielles de la Banque mondiale (CC BY 4.0, mais fichiers lourds à tous les niveaux administratifs, sans adresse stable). Producteur non étatique et frontières de fait : acceptable pour un fond sans donnée, sans ligne dans `limites_methodologiques` puisqu'aucun chiffre n'en sort ; la limite est dite dans la légende et le README. Pays étudiés dans `PAYS_ETUDIES` : un code et une grille pour en ajouter un. Seuls les cinq départements d'outre-mer ont des données ; les collectivités restent ternes. Antarctique exclu. Encarts d'outre-mer à leur vraie position, décalés au sud s'ils se chevauchent, en Mercator calé sur la métropole. Tuile du PDF dessinée en vectoriel par reportlab, sans capture de la 3D ni réseau. Captures de `docs/exemples/` : Chrome sans interface avec `--force-prefers-reduced-motion` et `--virtual-time-budget`, pour ne pas saisir l'animation en cours |
| Obligations de solarisation : textes lus sur Légifrance le 8 novembre (CCH L171-4 dans sa version du 28 mai 2026, loi n° 2026-403 ; L171-5 à venir au 1er janvier 2028 ; loi APER art. 40 dans sa version du 28 novembre 2025, loi n° 2025-1129 ; décret n° 2025-802 pour les seuils d'outre-mer). La loi du 26 novembre 2025 n'a pas reporté les échéances des parkings : elle admet les procédés mixtes (ombrières sur 35 % de la moitié, le reste végétalisé). Règles en constantes datées dans `ingestion/solaire.py` plutôt qu'en seed : quelques dates et seuils, lus par le seul diagnostic. Usages BD TOPO soumis : « Commercial et services », « Industriel », « Sportif » ; « Indifférencié » dit inconnu. Rappel de la règle sans effet sur le verdict ni avis juridique : exonérations motivées, exclusion possible des installations classées par arrêté et cas du data center, non cité en tant que tel, dits dans la limite. Structure des toitures écartée : aucune source publique de capacité portante ; BDNB (CSTB, Licence Ouverte 2.0) non intégrée, elle ne donne qu'un matériau. `compare` : seuil du parking et bâtiment existant au tableau, règle des toitures neuves une fois sous le tableau |
| Tension sur l'eau : référence retenue, les zones de répartition des eaux (code de l'environnement, R211-71 : ressource structurellement insuffisante face aux besoins, désignée par arrêté du préfet coordonnateur de bassin), couche WFS `sa:ZRE` du référentiel ZRPE du Sandre (Licence Ouverte), plutôt que des débits Hub'Eau rapportés à un QMNA5 que l'outil devrait calculer lui-même (aucune référence publiée par l'API). Zonage stable et national : chargé par `refresh` en médaillon, point dans polygone en Python, comme le zonage sismique ; une seule page (118 zones), nombre contrôlé contre le total annoncé. Conséquence lue sur Légifrance : en ZRE, un prélèvement non domestique est soumis à autorisation dès 8 m³/h de capacité, à déclaration en dessous (R214-1, rubrique 1.3.1.0, version du 29 août 2025). Sans effet sur le verdict, comme VigiEau : le prélèvement d'un data center ne se déduit pas de l'adresse. Une zone souterraine vise un aquifère précis (Albien, parfois profond), dit dans la limite. |
| Application, comparaison et courbe : la courbe est lue par le navigateur et envoyée en texte avec le projet, jamais stockée par le serveur ; gardée dans la session de l'onglet pour le rapport, oubliée si elle dépasse son quota. La comparaison calcule les diagnostics une fois et rend les trois fichiers (CSV, Markdown, JSON) avec le tableau ; une adresse ambiguë ou introuvable est nommée et tout s'arrête, comme en ligne de commande ; la carte garde l'épingle de la dernière analyse. `europe`, `refresh`, `tempo`, `ecowatt`, `report` et `demo` restent en ligne de commande (raisons dans « Suite ») |
| Tests : couverture mesurée par `pytest-cov` (lignes et branches, tests exclus), plancher dans `pyproject.toml` (`fail_under`), sous le relevé parce que la mesure est faite sous Windows et que la CI tourne sous Ubuntu ; il se relève à la main après chaque étape du lot T, jamais à la baisse. `verifier.py` ne mesure que la suite entière (un sous-ensemble de tests passerait sous le plancher). `cli.main` reconfigure la sortie en UTF-8 avec `errors="replace"` : sous pytest, ce flux relit la sortie des processus enfants, écrite dans la page de code de la console |
| Matrice des cas d'usage : des invariants, pas des valeurs (verdict à cinq critères, gestionnaire conforme au seed, parts de l'acheminement qui font le total, prix absent dit « non publié »). La base synthétique reste sans tranche IF, exprès : c'est elle qui joue le cas « non disponible ». Les tests qui écrivent un Parquet se passent de la fixture `no_network` (PySpark parle à sa JVM par une socket locale) et remplacent chaque extraction. Un défaut connu et pas encore corrigé s'écrit en `xfail` strict, avec sa raison |
| Projet incohérent refusé : consommation annuelle supérieure à la puissance × 8 784 heures (année bissextile), avant tout calcul, avec le plafond dans le message ; même règle dans `essentiel`, `diagnose`, `compare` et l'application |
| Pannes des sources à la demande : une réponse qui n'est pas l'objet JSON attendu est un échec de la source, dit dans sa section, jamais une trace Python ni un « rien à signaler ». Géorisques : corps vide accepté pour `rga` seulement (carte des argiles absente hors du continent), refusé pour les installations classées, les cavités et les sols pollués, où il ferait afficher zéro à tort. Une panne ne change l'état d'aucun critère du verdict |
| Sorties de référence : douze sorties de la vraie ligne de commande figées dans `ingestion/tests/references/` (`essentiel`, `diagnose` et `compare` de l'exemple de référence à Lyon, dans chaque format, plus un hub de recharge de 2 MW et une industrie de 45 MW pour les libellés que la tranche IF, absente de la base synthétique, ne montre pas) et le rapport Markdown. Le jour est figé au 15 juillet 2026 (fraîcheur des données, barèmes à revérifier, échéances solaires en dépendent). Les JSON sont comparés à dix chiffres significatifs : le dernier bit d'une somme peut différer entre Windows et la CI. Mise à jour par `--maj-references`, jamais en silence : le diff des références se relit dans la PR. Le PDF n'est pas figé (son texte extrait dépend de la mise en page) : nombre de pages borné, pied de page sur chaque page, titres de section du rapport Markdown retrouvés, chaque caractère présent dans Inter ou Spectral (un glyphe absent s'imprime en carré vide sans que l'extraction le montre). `pypdf` (BSD) entre au groupe `dev`, pour les tests seulement |
| Application pour un néophyte : deux adresses au plus (la ligne de commande garde 2 à 6) ; tout part d'une fenêtre d'accueil ; la seconde adresse voyage dans la ligne de la première (`autre`), pour que la page générée garde une seule ligne à remplacer ; la caméra cadre les adresses sans rien retirer de la carte ; `/api/analyse` rend une entrée par adresse et désigne par son rang celle qui est ambiguë |
| PDF : un rapport qui se regarde. Il ne garde que la limite qui pèse sur chacun des cinq critères, avec un conseil (`CONSEILS`, trois phrases au plus par critère) ; la règle « tout chiffre a sa limite » reste tenue par les sorties texte, Markdown et JSON, auxquelles le PDF renvoie. Visuels vectoriels reportlab, sans dépendance nouvelle ; les domaines de tension viennent du seed `seuils_raccordement`. |
| Main-d'œuvre : diplômés comptés dans les communes dont le centre est dans l'isochrone de 45 minutes en voiture, sens « arrivée » (Géoplateforme, ressource `bdtopo-valhalla`, à la demande, coupée par `--sans-risques`), sur la base Diplômes-Formation de l'Insee chargée par `refresh` (communes entières, tous sexes, dernier millésime ; valeur vide comptée zéro, car incluse dans une autre catégorie) ; deux chiffres, supérieur et CAP-BEP ou bac, parce que les électriciens et techniciens d'exploitation n'ont pas tous un diplôme du supérieur. Isochrone injoignable : dit dans la section, sans repli. Recrutements : enquête BMO de France Travail sommée au département (colonne `Dept`), faute de table officielle du bassin d'emploi vers la commune ; bassins sous secret statistique non comptés mais dénombrés ; classeur lu par `openpyxl` (MIT). Métiers retenus dans le seed `metiers_techniques_bmo`. Sans effet sur le verdict |
| Prix le plus haut et le plus bas d'une tranche : à prix égal, le semestre le plus récent (`fct_prix_entreprise_tranche`) (sans ordre, DuckDB départage au hasard) |

## Écarté

| Sujet | Pourquoi |
|---|---|
| Prix « à l'adresse » | Péréquation : identique partout |
| Carbone régional en métropole | Un seul réseau continental, pas de facteur régional opposable |
| Capacité de raccordement chiffrée, distance au poste RTE | Absentes de l'open data |
| Distance aux postes sources et aux lignes, y compris par détection sur images | Coordonnées retirées par RTE et EDF en 2026 (sécurité publique), qu'une détection contournerait ; jeu Enedis seulement local |
| Zonage sismique national publié par wpd (data.gouv.fr, 2018) | Producteur non officiel (un développeur éolien) : le décret et planseisme.fr font foi |
| Loyer des locaux d'activité, y compris estimé par un rendement brut | Aucune source officielle et ouverte ; l'hypothèse de rendement n'a pas de source |
| Classement pondéré des sites dans `compare` | Contraire à la comparaison sans classement |
| Ports fluviaux parmi les pôles de fret | Pas de maille port dans les données VNF du SDES |
| Photovoltaïque détecté (OpenPVMapper) et changements du site par détection sur l'ortho | Prépublication sans producteur officiel (CC-BY, environ 75 % de précision), hors de la règle des sources |
| Température de surface Landsat pour le refroidissement des data centers | Température du sol, pas de l'air ; aucune méthode de lecture sourcée pour ce besoin |
| Sons enregistrés pour l'ambiance de la tuile | Synthétisés dans la page : ni fichier, ni licence, ni requête |
| Spark pour silver et gold | dbt-duckdb suffit à ce volume |
| Exécution planifiée, Airflow, application hébergée en ligne | Hors périmètre : tout se lance à la main, sur son poste |
