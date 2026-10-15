# Comparaison de 3 adresses

*Sans classement : chaque indicateur est donné tel quel, avec sa limite en fin de document.*

| Indicateur | 20 Rue de la République 69002 Lyon | 1 Place du Général de Gaulle 59800 Lille | 1 Place du Maréchal Leclerc 86000 Poitiers |
|---|---|---|---|
| Territoire | Lyon (69123), Rhône, Auvergne-Rhône-Alpes | Lille (59350), Nord, Hauts-de-France | Poitiers (86194), Vienne, Nouvelle-Aquitaine |
| Géocodage | numéro, score 0,97 | numéro, score 0,98 | numéro, score 0,97 |
| Industrie : commune | 1 site(s), 49,6 GWh/an | aucun site raccordé au transport | aucun site raccordé au transport |
| Industrie : département | 23 site(s), 1 309 GWh/an, rang 11/90 | 49 site(s), 8 720 GWh/an, rang 1/90 | 13 site(s), 127 GWh/an, rang 64/90 |
| Région : couverture | 191 %, excédentaire ; dépendance 10/13 | 108 %, excédentaire ; dépendance 8/13 | 143 %, excédentaire ; dépendance 9/13 |
| Contraintes RTE (3-5 ans) | aucune | 4, dont 0 plus de 75 fois par an | 21, dont 8 plus de 75 fois par an |
| Postes RTE du département | 91 (HTB1 64, HTB2 19, HTB3 3) | 103 (HTB1 54, HTB2 45, HTB3 3) | 55 (HTB1 49, HTB2 3, HTB3 3) |
| Zone prioritaire RTE | Vallée de la chimie (P2), à 7 km ; capacité 2029, si les projets se concrétisent | Valenciennes (P2), à 44 km ; capacité 2030, si les projets se concrétisent | Loire-Estuaire (P2), à 202 km ; capacité 2030, si les projets se concrétisent |
| Site clé en main (France 2030) | Salaise-sur-Sanne, à 47 km ; prêt en 2026 - 2027 | Noyelles-Godault, à 24 km ; prêt en 2027 - 2030 | Alloinay, à 56 km ; prêt en 2024 |
| Risques naturels | Inondation, Remontée de nappe, Séisme (faible), Retrait-gonflement des argiles (modéré), Radon (faible) | Remontée de nappe, Séisme (faible), Retrait-gonflement des argiles (modéré), Radon (faible) | Inondation, Séisme (modéré), Mouvements de terrain, Retrait-gonflement des argiles (modéré), Radon (faible) |
| Risques technologiques | Pollution des sols | Pollution des sols | Pollution des sols |
| Non évalués à l'adresse | Rupture de barrage | Inondation, Mouvements de terrain | Remontée de nappe |

## Identique pour toutes les adresses

- Puissance 12 MW → HTA, Enedis (ou ELD) : Raccordement en moyenne tension (20 kV), souvent sur un départ dédié au-delà de quelques MW
- Prix de l'électricité pour l'entreprise (identique sur tout le territoire)
- Consommation 60 000 MWh/an → tranche IE (de 20 000 à 70 000 MWh par an), prix moyen constaté par Eurostat
- 12 derniers mois (S2 2025 – S1 2026) : 0,0916 €/kWh HTVA, dont 0,0079 €/kWh de taxes non récupérables → facture d'environ 5 500 000 € par an
- Repères depuis S2 2007 : moyenne sur 5 ans 0,1122 €/kWh, plus bas 0,0553 (S2 2007), plus haut 0,1676 (S1 2023)
- Prix moyen des 3 prochaines années, d'après l'historique de la tranche et les erreurs passées de la méthode : de 0,0762 à 0,1413 €/kWh, 0,0961 au centre → facture de 4 570 000 € à 8 480 000 € par an (5 760 000 € au centre)
- Contrôle sur le passé : appliquée à chaque semestre de S2 2019 à S1 2023, la méthode a encadré le prix moyen réel des 3 années suivantes 1 fois sur 8 (12 %, contre 80 % pour une méthode bien calibrée) ; il a dépassé le scénario haut 6 fois et est passé sous le scénario bas 1 fois. Ces périodes de 3 ans se chevauchent : ce ne sont pas des essais indépendants. Leurs écarts font désormais partie des erreurs qui calibrent la fourchette affichée.
- Coût horaire d'un profil type (identique sur tout le territoire)
- Bureau tertiaire, 36 kVA, septembre 2026, option Heures Creuses du tarif bleu non résidentiel
- 9 600 kWh, dont 15 % en heures creuses : 1 586 € HTVA, soit 0,1652 €/kWh ; 22 gCO₂/kWh en moyenne
- Décaler 528 kWh flexibles vers les heures creuses : 22 € économisés, mais 1,4 kgCO₂ émis en plus : les heures creuses de nuit sont plus carbonées que la mi-journée solaire

## Limites

- Ne compte que les sites raccordés au réseau de transport (HTB) : les consommateurs raccordés à Enedis n'y figurent pas. Depuis 2022, environ 40 % des IRIS sont masqués par le secret statistique : le millésime retenu est donc le dernier publié en entier, au prix de quelques années d'ancienneté.
- Le réseau est interconnecté : une région déficitaire importe sans difficulté de ses voisines. Le solde décrit une dépendance structurelle, pas une pénurie ni un risque de coupure.
- Contraintes de transit liées à l'accueil des énergies renouvelables (injection), que RTE prévoit de gérer par de la flexibilité plutôt que par de nouveaux ouvrages ; données indicatives et non engageantes. Elles ne mesurent pas la capacité disponible pour un consommateur. Ni les ouvrages ni les postes ne sont géolocalisés en open data : la maille est le département, pas l'adresse, et une liaison entre deux départements compte dans les deux.
- Repère de densité du réseau de transport, pas une capacité de raccordement : un poste proche peut être saturé. Les postes ne sont pas géolocalisés en open data, et environ 3 % d'entre eux n'ont pas de département publié.
- Zones et années cibles du schéma décennal de RTE, sous réserve des autorisations administratives et, pour les zones P2, de la concrétisation des projets industriels ; les zones P3 sont de simples exemples, sans date. Distance à vol d'oiseau jusqu'à la commune de référence de la zone, pas jusqu'au poste. Être proche d'une zone ne donne aucun droit d'accès au réseau : la capacité est attribuée selon les procédures de RTE. Concerne surtout les raccordements au réseau de transport (au-delà de quelques dizaines de MW).
- Distance à vol d'oiseau jusqu'au point de la commune, pas jusqu'au site lui-même. La période de maturité est celle annoncée pour un site prêt à accueillir une activité ; elle ne garantit ni la disponibilité du foncier à la date voulue, ni la puissance électrique raccordable, à confirmer auprès de l'aménageur et du gestionnaire de réseau.
- Croisement de la position géocodée avec les zonages réglementaires et les cartographies d'aléas disponibles : le résultat dépend de la précision du géocodage et de la couverture des cartes, et un risque « non évalué » à l'adresse n'est pas un risque absent. Ce n'est ni l'état des risques réglementaire, obligatoire pour une vente ou une location, ni une étude de sol ou de vulnérabilité du site.
- Indicatif : 1 kVA est assimilé à 1 kW. Le domaine de tension réel est fixé par le gestionnaire lors de l'étude de raccordement.
- La capacité de raccordement disponible pour la consommation n'est pas publiée en open data (Caparéseau ne couvre que l'injection). Seule une étude de raccordement RTE ou Enedis fait foi.
- Prix moyen national constaté pour la tranche, hors TVA et taxes récupérables : identique en tout point du territoire (péréquation), il ne dépend pas de l'adresse. C'est une moyenne déclarée par les fournisseurs : un contrat donné peut s'en écarter nettement selon sa date de signature, sa durée et le profil du site. Les scénarios à 3 ans ne sont pas une prévision : le scénario central applique au prix des 12 derniers mois la variation médiane observée sur la tranche, et la fourchette reprend les erreurs passées de cette méthode (10e centile, médiane, 90e centile de l'écart entre prix réel et scénario central), crise de 2022-2023 comprise. Une rupture jamais observée la dépasserait encore.
- Le prix est identique en tout point du territoire (péréquation tarifaire) : ce coût ne dépend pas de l'adresse. Profil illustratif, heures creuses supposées de 22 h à 6 h (en réalité fixées localement par le gestionnaire de réseau), jours fériés comptés comme ouvrés, abonnement exclu. Carbone : intensité nationale, faute d'intensité régionale opposable.
