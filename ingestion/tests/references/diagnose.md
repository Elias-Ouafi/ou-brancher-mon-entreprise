# Diagnostic d'implantation — Place Bellecour 69002 Lyon

*Commune Lyon (69123) · Rhône (69) · Auvergne-Rhône-Alpes (84)*  
*Données chargées le 01/07/2026.*  

Établi le 15/07/2026 à partir de données publiques ouvertes (RTE, ODRÉ, Eurostat, Géorisques). Chaque indicateur est suivi de sa limite méthodologique.

## L'essentiel : prix et carbone à cette adresse

- **Prix moyen : non publié par Eurostat pour 70 000 MWh/an**
- **Acheminement : environ 1 370 000 € HTVA par an, soit 19,6 €/MWh, déjà compris dans le prix moyen (TURPE 7 HTA, longue utilisation, la moins chère ici ; 12 000 kVA souscrits, puissance constante)**
- **Électricité décarbonée : oui (France continentale), 51,9 gCO₂e/kWh en cycle de vie, sous le seuil de 100 gCO₂e/kWh**
- **Activité : data center, profil de charge : puissance constante, faute de courbe publiée. Régime d'accise : centres de stockage de données (E16, CIBS L312-64 et L312-70) : tarif réduit sur la fraction au-delà de 1 GWh par an, sous huit conditions cumulatives attestées au fournisseur, dont l'électro-sensibilité (2,25 % de la valeur ajoutée : voir --va), la valorisation de la chaleur fatale et la limitation de l'eau de refroidissement.**
- **Locaux d'activité : 3 250 €/m² à la vente à Lyon (médiane de 6 ventes, quartiles 2 625 à 3 875 ; dernière vente en 07/2025 ; DVF)**
- **À savoir : Le prix ne dépend pas de l'adresse mais de la consommation (péréquation, Corse et outre-mer compris). Le carbone est la moyenne annuelle du réseau de ce territoire, pas celle d'un contrat de fourniture.**
- **Brancher ici ? À vérifier avant de s'engager : risques (non évalué : facture).**

## 1. Pression industrielle locale (sites raccordés au réseau de transport, 2021)

- Commune : 1 site(s), 40,0 GWh/an
- Département : 3 site(s) dans 2 commune(s), 100 GWh/an — rang 1 sur 4 départements

> **Limite.** Ne compte que les sites raccordés au réseau de transport (HTB) : les consommateurs raccordés à Enedis n'y figurent pas. Depuis 2022, environ 40 % des IRIS sont masqués par le secret statistique : le millésime retenu est donc le dernier publié en entier, au prix de quelques années d'ancienneté.

- Entreprises raccordées à Enedis : 5 site(s), 0,4 GWh/an en 2025, dont industrie 0,3 GWh (2 site(s)) — rang 2 sur 2 communes du département ; premier secteur : Métallurgie (0,3 GWh)

> **Limite.** Ne compte que les entreprises raccordées au réseau Enedis : les sites raccordés à RTE (voir la pression industrielle) et aux entreprises locales de distribution (Strasbourg, Grenoble, Metz…) n'y figurent pas, ni la Corse et l'outre-mer. Adresses fournies par Enedis à titre indicatif : une adresse libellée autrement que dans la BAN n'est pas retrouvée, et l'absence de consommation à l'adresse ne prouve pas qu'elle n'est pas raccordée. Une consommation à l'adresse montre un raccordement existant, pas la puissance disponible.

## 2. Tension du réseau régional (01/2025 – 12/2025, 12 mois)

- Production 2,4 GWh, consommation 1,2 GWh : couverture 200 % → région excédentaire
- Rang de dépendance : 3 sur 3 (1 = la plus déficitaire)

> **Limite.** Le réseau est interconnecté : une région déficitaire importe sans difficulté de ses voisines. Le solde décrit une dépendance structurelle, pas une pénurie ni un risque de coupure.

- Contraintes d'évacuation (RTE) : 14 575 MW d'EnR installés ou en projet, 187 MW à compenser, 2 777 MWh non évacués par an

> **Limite.** Contraintes d'évacuation de la production renouvelable (injection), données à titre indicatif par RTE. Elles ne mesurent pas la capacité disponible pour un consommateur (soutirage). Pas de donnée pour la Corse ni l'outre-mer.

- Contraintes prospectives (RTE, d'ici 3 à 5 ans) : 3 sur des ouvrages reliés aux postes du département, dont 2 plus de 75 fois par an.
- Les plus fréquentes : LIAISON LYON2LYON3 (très forte : plus de 150 fois par an, épisodes de 4 à 6 h, pérennité moyenne, en journée) ; LIAISON BOURGLYON1 (forte : entre 75 et 150 fois par an, épisodes de 4 à 6 h, pérennité elevee) ; LIAISON LYON2LYON3 (faible : entre 5 et 25 fois par an, épisodes de 4 à 6 h, pérennité faible)

> **Limite.** Contraintes de transit liées à l'accueil des énergies renouvelables (injection), que RTE prévoit de gérer par de la flexibilité plutôt que par de nouveaux ouvrages ; données indicatives et non engageantes. Elles ne mesurent pas la capacité disponible pour un consommateur. Ni les ouvrages ni les postes ne sont géolocalisés en open data : la maille est le département, pas l'adresse, et une liaison entre deux départements compte dans les deux.

## 3. Raccordement

- Puissance 12 MW → HTA, Enedis (ou ELD) : Raccordement en moyenne tension (20 kV), souvent sur un départ dédié au-delà de quelques MW

> **Limite.** Indicatif : 1 kVA est assimilé à 1 kW. Le domaine de tension réel est fixé par le gestionnaire lors de l'étude de raccordement.

- Acheminement, détail : 437 000 € pour la puissance souscrite, 932 000 € pour l'énergie, 833 € de gestion et comptage par an

> **Limite.** Même grille dans toute la France (péréquation, Corse et outre-mer compris) : seul le domaine de tension et la puissance souscrite la font varier. Calcul indicatif : une seule puissance souscrite pour toutes les classes temporelles (1 kVA assimilé à 1 kW), heures creuses supposées de 22 h à 6 h et pointe de 9 h à 11 h et de 18 h à 20 h (fixées localement en réalité), jours fériés comptés comme ouvrés, énergie répartie sur les heures de l'année faute de courbe de charge ; version tarifaire retenue au moindre coût, sans la pointe mobile. Ne comptent ni les dépassements de puissance souscrite, ni l'énergie réactive, ni le raccordement lui-même (devis du gestionnaire). Au-delà de 40 MW, grille HTB 1 de RTE (la tension réelle, HTB 1, 2 ou 3, est fixée par RTE ; HTB 2 coûte moins en puissance souscrite), sur le calendrier HTB de la délibération n° 2025-77 : week-ends et jours fériés légaux entièrement creux ; jusqu'au 31 décembre 2026, heures creuses de 23 h à 7 h ; dès le 1er janvier 2027, de 22 h à 6 h en saison haute et de 2 h à 6 h puis de 12 h à 16 h en saison basse, sauf en Nouvelle-Aquitaine et en Occitanie (de 2 h à 4 h et de 10 h à 16 h, puis de 10 h à 18 h ; pointe de 7 h à 9 h et de 18 h à 20 h). Plages lues pour HTB 1 ; la journée type d'une activité reste celle d'un jour ouvré les jours fériés. Gestion et comptage du contrat d'accès au réseau, compteur détenu par RTE. Ne comptent ni la composante d'injection-soutirage (option réservée au stockage), ni les alimentations de secours (réservation de 3,78 €/kW/an en HTB 1 selon la délibération n° 2026-104, plus la partie dédiée, propre au site) ou complémentaires, ni les dépassements ponctuels.

- Qualité de fourniture (Enedis, 2024) : 2,5 % des clients du département au-delà des seuils de coupure (médiane des départements : 2,5 %), sous le seuil réglementaire de 3 % ; 2,2 % en moyenne sur cinq ans, seuil dépassé 1 année(s) sur cinq.

> **Limite.** Part des clients HTA et BT d'Enedis dont les coupures ont dépassé dans l'année 6 coupures longues (plus de 3 minutes), 35 coupures brèves ou 13 heures cumulées. C'est une moyenne départementale, pas la qualité au poste ou à la ligne qui desservira le site ; elle varie nettement entre zones urbaines et rurales d'un même département. Une année isolée au-dessus de 3 % suit souvent une tempête ; la moyenne sur cinq ans dit mieux la fiabilité structurelle. Ni la Corse, ni l'outre-mer, ni les territoires des entreprises locales de distribution ne sont couverts.

- Postes RTE en exploitation dans le département : 3, dont 1 en HTB1 (63 à 150 kV), 1 en HTB2 (225 kV) et 1 en HTB3 (400 kV) ; 1 en projet

> **Limite.** Repère de densité du réseau de transport, pas une capacité de raccordement : un poste proche peut être saturé. Les postes ne sont pas géolocalisés en open data, et environ 3 % d'entre eux n'ont pas de département publié.

- Zone industrielle prioritaire de RTE la plus proche : Vallée de la chimie (P2), à 7 km (Saint-Fons) : capacité supplémentaire possible à partir de 2029, si les projets industriels de la zone se concrétisent ; ouvrages prévus : nouveau poste 225 kV « Rhôna » raccordé au poste 400 kV de Mions

> **Limite.** Zones et années cibles du schéma décennal de RTE, sous réserve des autorisations administratives et, pour les zones P2, de la concrétisation des projets industriels ; les zones P3 sont de simples exemples, sans date. Distance à vol d'oiseau jusqu'à la commune de référence de la zone, pas jusqu'au poste. Être proche d'une zone ne donne aucun droit d'accès au réseau : la capacité est attribuée selon les procédures de RTE. Concerne surtout les raccordements au réseau de transport (au-delà de quelques dizaines de MW).

- Site industriel clé en main (France 2030) le plus proche : Salaise-sur-Sanne, à 46 km ; annoncé prêt en 2026 - 2027

> **Limite.** Distance à vol d'oiseau jusqu'au point de la commune, pas jusqu'au site lui-même. La période de maturité est celle annoncée pour un site prêt à accueillir une activité ; elle ne garantit ni la disponibilité du foncier à la date voulue, ni la puissance électrique raccordable, à confirmer auprès de l'aménageur et du gestionnaire de réseau.

> **Limite.** La capacité de raccordement disponible pour la consommation n'est pas publiée en open data (Caparéseau ne couvre que l'injection). Seule une étude de raccordement RTE ou Enedis fait foi.

## 4. Prix de l'électricité pour l'entreprise (identique sur tout le territoire)

- Aucun prix publié par Eurostat pour la tranche de 70 000 MWh/an.

## 5. Carbone du réseau et coût horaire d'un profil type

- Électricité du réseau décarbonée (France continentale) : 51,9 gCO₂e/kWh en cycle de vie (ADEME, année 2024), sous le seuil de 100 gCO₂e/kWh de la taxonomie européenne ; émissions directes observées par RTE sur 12 mois, jusqu'au 30/06/2026 : 22 gCO₂/kWh

> **Limite.** Intensité moyenne du réseau sur l'année, en cycle de vie (méthode location-based) : elle ne dit rien du contrat de fourniture ni des garanties d'origine achetées (méthode market-based), ni des heures de consommation, l'intensité variant fortement d'une heure à l'autre. Toutes les adresses du continent partagent la même valeur : le réseau y est interconnecté. Le seuil de 100 gCO2e/kWh est celui de la taxonomie européenne pour la production d'électricité ; « décarbonée » qualifie le réseau, pas l'entreprise, et ne permet pas à elle seule une allégation environnementale.

- Aucun mois complet et tarifé : charger les données nationales récentes.

> **Limite.** Le prix est identique en tout point du territoire (péréquation tarifaire) : ce coût ne dépend pas de l'adresse. Profil illustratif, heures creuses supposées de 22 h à 6 h (en réalité fixées localement par le gestionnaire de réseau), jours fériés comptés comme ouvrés, abonnement exclu. Carbone : intensité nationale, faute d'intensité régionale opposable.

## 6. Risques et contraintes du site : Géorisques et eau (arrêtés de restriction)

- Plans de prévention en vigueur dans la commune : Vallée de la chimie (risque industriel, approuvé en 2016), PPRI du Grand Lyon (inondation, approuvé en 2009)

> **Limite.** Un plan approuvé ou prescrit couvre une partie de la commune, pas forcément l'adresse : seul son zonage, consultable en mairie ou sur georisques.gouv.fr, dit si la parcelle est constructible et sous quelles conditions. Un plan prescrit n'a pas encore de règlement opposable, sauf application anticipée.

- Non consulté (option --sans-risques).
- Tension sur la ressource : en zone de répartition des eaux, ressource reconnue insuffisante face aux besoins : Nappe de test (eaux souterraines) ; un prélèvement non domestique dans la ressource visée est soumis à autorisation dès 8 m³/h, à déclaration en dessous (R214-1, rubrique 1.3.1.0)

> **Limite.** Une zone de répartition des eaux dit un déficit structurel reconnu par arrêté, pas le débit ni le niveau de nappe du jour (voir les restrictions d'eau). Une zone souterraine vise un aquifère précis, parfois profond (Albien en Île-de-France) : le prélèvement n'est concerné que s'il puise dans cet aquifère. Hors zone, un prélèvement peut rester soumis à déclaration ou autorisation selon son volume (autres rubriques de R214-1) et aux arrêtés de restriction. La liste des communes classées est arrêtée par chaque préfet de département (R211-72) : le contour du Sandre peut en différer à la marge. Aucune zone n'est publiée en outre-mer dans ce référentiel.

## 7. Foncier, desserte, main-d'œuvre et prix des locaux d'activité (DVF)

- Obligation solaire : bâtiment neuf, extension ou rénovation lourde de plus de 500 m² au sol (industrie, entrepôt, commerce, bureaux) : énergie renouvelable ou végétalisation sur 40 % de la toiture au moins, 50 % à compter du 1er juillet 2027 (CCH L171-4)
- Obligation solaire : parc de stationnement extérieur de plus de 1 500 m² : ombrières d'énergie renouvelable sur la moitié de sa surface (ou ombrières sur 35 % de cette moitié, le reste végétalisé), au plus tard le 1er juillet 2026 dès 10 000 m², le 1er juillet 2028 en dessous (loi APER, art. 40, modifiée le 26 novembre 2025)

> **Limite.** Rappel de la règle, pas un avis juridique. La surface du parking n'est pas connue de l'outil : l'obligation est donnée par seuil. Un data center n'est pas cité en tant que tel parmi les usages : à qualifier avec le service instructeur. Le texte prévoit des exonérations motivées (contraintes techniques, de sécurité, architecturales ou patrimoniales, coût économiquement inacceptable, parking ombragé par des arbres) et un arrêté peut écarter ou adapter l'obligation pour une installation classée. La part de toiture des bâtiments existants est fixée par décret, non chiffrée ici. La capacité portante de la toiture n'est pas publique : la BDNB (CSTB, Licence Ouverte 2.0) donne au mieux le matériau de toiture, pas la structure.

- Recrutements : 400 projets de recrutement en 2026 pour les métiers de l'électricité, du froid et de la maintenance dans le département, dont 75,0 % jugés difficiles par les employeurs ; 1 des 4 croisements bassin-métier sont sous secret statistique et ne sont pas comptés

> **Limite.** Intentions d'embauche déclarées par les employeurs pour l'année, pas des embauches réalisées. Métiers retenus par l'outil (électricité, froid et conditionnement d'air, maintenance : seed metiers_techniques_bmo). Le département remplace le bassin d'emploi, faute de table officielle du bassin vers la commune ; les bassins sous secret statistique ne sont pas comptés, ce qui minore les projets.

- Friches : 2 friches de 1 ha ou plus à moins de 10 km ; les plus proches : Site F1 à Lyon (friche industrielle, 5,0 ha, zone U, friche sans projet) à 2 km ; Site F2 à Lyon (friche industrielle, 3,0 ha, zone U, friche avec projet) à 4 km

> **Limite.** Inventaire déclaratif et incomplet : une friche non référencée n'y figure pas et une friche listée peut être déjà occupée ou en cours de projet (statut donné). Seules les friches de 1 ha ou plus non reconverties sont retenues, d'après la surface d'unité foncière quand elle est renseignée (les sites sans surface sont écartés). Ni la propriété, ni le prix, ni la constructibilité ne sont garantis : à confirmer auprès de la collectivité, de l'établissement public foncier et au règlement de la zone. Les zones d'activité ne sont pas dans cette source.

- 3 250 €/m² à la vente à Lyon (médiane de 6 ventes, quartiles 2 625 à 3 875 ; dernière vente en 07/2025 ; DVF)

> **Limite.** Prix de vente au m² des locaux industriels, commerciaux ou assimilés : DVF ne distingue pas les bureaux des commerces et entrepôts, et ne contient ni loyers ni locaux loués. Médiane des ventes d'un seul local, sans logement ni autre disposition dans l'acte, entre 15 et 5 000 m² et de 100 à 15 000 €/m² ; la valeur foncière comprend le terrain. Les ventes les plus récentes manquent de quelques mois de publication. Alsace, Moselle et Mayotte ne sont pas publiées. Ordre de grandeur pour comparer des villes, pas l'estimation d'un local précis.

## 8. Brancher ici ? Les cinq critères

- Raccordement (favorable) : HTA, Enedis (ou ELD). Raccordement courant au réseau de distribution ; délai et coût fixés par l'étude du gestionnaire.
- Risques (à vérifier) : 2 plans de prévention dans la commune. Vallée de la chimie (risque industriel, approuvé en 2016), PPRI du Grand Lyon (inondation, approuvé en 2009). Le règlement du plan peut interdire ou conditionner la construction selon la zone : le consulter en mairie avant de s'engager.
- Carbone (favorable) : 51,9 gCO₂e/kWh. France continentale : sous le seuil de 100 gCO₂e/kWh de la taxonomie européenne.
- Facture (non évalué) : prix non disponible. non publié par Eurostat pour 70 000 MWh/an
- Foncier (pour information) : site clé en main à 46 km. Salaise-sur-Sanne, annoncé prêt en 2026 - 2027. 2 friche(s) de 1 ha ou plus à moins de 10 km.
- À vérifier avant de s'engager : risques (non évalué : facture).

## Sources

- Géocodage : Base Adresse Nationale, via la Géoplateforme de l'IGN (data.geopf.fr).
- Pression industrielle : ODRÉ, consommation-annuelle-par-iris (RTE, GRTgaz, Teréga).
- Tension régionale : ODRÉ, equilibre-regional-mensuel-prod-conso-brute et contraintes-regionales (RTE).
- Prix de l'électricité des entreprises : Eurostat, nrg_pc_205 (semestriel, par tranche).
- Risques à l'adresse : Géorisques (ministère de la Transition écologique, BRGM), rapport de risques ; à défaut, base GASPAR et zonage sismique de la commune.
- Profil type : barèmes du tarif bleu non résidentiel, délibérations de la CRE n° 2026-06 et n° 2026-147. Carbone : éCO2mix national (RTE).

---

*Données chargées le 01/07/2026.*  
*Pour le refaire : python -m ingestion.cli diagnose --adresse Lyon --puissance 12 --conso 70000 --activite data-center --sans-risques --sans-carte --format md --sans-graphiques*  
