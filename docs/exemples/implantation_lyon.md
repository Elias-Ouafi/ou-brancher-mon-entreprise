# Diagnostic d'implantation — 20 Rue de la République 69002 Lyon

*Commune Lyon (69123) · Rhône (69) · Auvergne-Rhône-Alpes (84)*  

Établi le 10/10/2026 à partir des données ouvertes de RTE et d'ODRÉ. Chaque indicateur est suivi de sa limite méthodologique.

## 1. Pression industrielle locale (sites raccordés au réseau de transport, 2021)

- Commune : 1 site(s), 49,6 GWh/an
- Département : 23 site(s) dans 19 commune(s), 1 309 GWh/an — rang 11 sur 90 départements

> **Limite.** Ne compte que les sites raccordés au réseau de transport (HTB) : les consommateurs raccordés à Enedis n'y figurent pas. Depuis 2022, environ 40 % des IRIS sont masqués par le secret statistique : le millésime retenu est donc le dernier publié en entier, au prix de quelques années d'ancienneté.

![Pression industrielle des départements](implantation_lyon_graphiques/pression_departements.png)

## 2. Tension du réseau régional (01/2025 – 12/2025, 12 mois)

- Production 119 231 GWh, consommation 62 405 GWh : couverture 191 % → région excédentaire
- Rang de dépendance : 10 sur 13 (1 = la plus déficitaire)

> **Limite.** Le réseau est interconnecté : une région déficitaire importe sans difficulté de ses voisines. Le solde décrit une dépendance structurelle, pas une pénurie ni un risque de coupure.

- Contraintes d'évacuation (RTE) : 14 575 MW d'EnR installés ou en projet, 187 MW à compenser, 2 777 MWh non évacués par an

> **Limite.** Contraintes d'évacuation de la production renouvelable (injection), données à titre indicatif par RTE. Elles ne mesurent pas la capacité disponible pour un consommateur (soutirage). Pas de donnée pour la Corse ni l'outre-mer.

![Couverture de la consommation par la production, par région](implantation_lyon_graphiques/couverture_regions.png)

## 3. Raccordement

- Puissance 12 MW → HTA, Enedis (ou ELD) : Raccordement en moyenne tension (20 kV), souvent sur un départ dédié au-delà de quelques MW

> **Limite.** Indicatif : 1 kVA est assimilé à 1 kW. Le domaine de tension réel est fixé par le gestionnaire lors de l'étude de raccordement.

> **Limite.** La capacité de raccordement disponible pour la consommation n'est pas publiée en open data (Caparéseau ne couvre que l'injection). Seule une étude de raccordement RTE ou Enedis fait foi.

## 4. Coût horaire d'un profil type (identique sur tout le territoire)

- Bureau tertiaire, 36 kVA, septembre 2026, option Heures Creuses du tarif bleu non résidentiel
- 9 600 kWh, dont 15 % en heures creuses : 1 586 € HTVA, soit 0,1652 €/kWh ; 22 gCO₂/kWh en moyenne
- Décaler 528 kWh flexibles vers les heures creuses : 22 € économisés, mais 1,4 kgCO₂ émis en plus : les heures creuses de nuit sont plus carbonées que la mi-journée solaire

> **Limite.** Le prix est identique en tout point du territoire (péréquation tarifaire) : ce coût ne dépend pas de l'adresse. Profil illustratif, heures creuses supposées de 22 h à 6 h (en réalité fixées localement par le gestionnaire de réseau), jours fériés comptés comme ouvrés, abonnement exclu. Carbone : intensité nationale, faute d'intensité régionale opposable.

![Courbe journalière du profil type](implantation_lyon_graphiques/profil_journalier.png)

## Sources

- Géocodage : Base Adresse Nationale (api-adresse.data.gouv.fr).
- Pression industrielle : ODRÉ, consommation-annuelle-par-iris (RTE, GRTgaz, Teréga).
- Tension régionale : ODRÉ, equilibre-regional-mensuel-prod-conso-brute et contraintes-regionales (RTE).
- Prix : barèmes du tarif bleu non résidentiel, délibérations de la CRE n° 2026-06 et n° 2026-147. Carbone : éCO2mix national (RTE).
