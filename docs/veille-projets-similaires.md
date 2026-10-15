# Veille : projets similaires sur GitHub

> Établie le 15 octobre 2026. But : repérer les projets proches d'« Où brancher mon
> entreprise » et noter les fonctionnalités qui valent d'être reprises.

## Méthode et limites

- Environ 45 recherches de dépôts GitHub (`gh search repos`), en français et en anglais :
  éCO2mix, ODRÉ, Tempo, RTE, Enedis, raccordement, Caparéseau, TURPE, prix de
  l'électricité, Eurostat, ENTSO-E, intensité carbone, *site selection*, *hosting
  capacity*, *grid capacity*, *datacenter siting*, etc.
- Lecture des README (et des décisions d'architecture quand elles existaient) des
  dépôts pertinents. Les étoiles et dates sont celles du 15 octobre 2026.
- Chaque source de données évoquée ci-dessous a été **interrogée pour de vrai** avant
  d'être recommandée ; le statut est indiqué.
- Limite : la recherche GitHub ne voit que les noms, descriptions et sujets des
  dépôts. Un projet mal décrit, ou privé, a pu échapper.

## Constat

**Aucun dépôt ne fait la même chose** : un diagnostic d'implantation électrique,
par adresse française, en ligne de commande, avec ses limites méthodologiques. Les
voisins se rangent en quatre familles :

| Famille | Ce qu'on peut en tirer |
|---|---|
| Modèles d'aptitude de site pour data centers (États-Unis, Espagne) | La structure d'un diagnostic multicritère : indicateurs normalisés, critères éliminatoires, pondérations, sensibilité, comparaison de sites |
| API carbone française (`carbon-fr`) | La rigueur de méthode : méthodologie versionnée, backtests, garde-fous de neutralité |
| Outils de raccordement et de tarifs (Enedis, comparateurs) | Le coût de raccordement indicatif, l'import d'une vraie courbe de charge |
| Intégrations domotiques (Tempo, EcoWatt, Linky, éCO2mix) | Rien de structurant : clients d'API déjà couverts chez nous |

## Dépôts étudiés

| Dépôt | ★ | Ce que c'est | Intérêt |
|---|---|---|---|
| [Kovelt/carbon-fr](https://github.com/Kovelt/carbon-fr) | 6 | API REST d'intensité carbone française (Rust), sur éCO2mix via ODRÉ | **Fort** : méthode versionnée, intensité régionale estimée, backtests, garde-fous de neutralité |
| [simonhansedasi/wa-datacenter-suitability](https://github.com/simonhansedasi/wa-datacenter-suitability) | 0 | Aptitude des terrains aux data centers, État de Washington | **Fort** : 10 indicateurs publics, 2 critères éliminatoires, notes 0–1 |
| [sonnetx/datacenter-map](https://github.com/sonnetx/datacenter-map) | 0 | Classement des 50 États américains pour les data centers | **Fort** : priorités pondérées, sensibilité du rang, scénario reproductible |
| [marcserranos/siting-model](https://github.com/marcserranos/siting-model) | 0 | Plateforme d'implantation de data centers en Espagne | **Fort** : niveau de confiance par donnée, fourchette quand les sources divergent, contrôle de réalité |
| [FEPPN/tool-raccordement](https://github.com/FEPPN/tool-raccordement) | 0 | Questionnaire d'estimation du coût de raccordement Enedis | **Moyen** : bonne grille de questions, mais calcul absent du dépôt |
| [JC144/EDF_Simulateur_Prix](https://github.com/JC144/EDF_Simulateur_Prix) | 238 | Comparateur de tarifs à partir d'un export de consommation Linky | **Moyen** : import de courbe de charge réelle, traitement 100 % local |
| [gridsuite/odre-server](https://github.com/gridsuite/odre-server) | 7 | Service RTE d'import des postes et lignes depuis ODRÉ | **Moyen** : piste « distance au poste », mais les coordonnées ont disparu du jeu (voir plus bas) |
| [HodgeLab/datacenter-siting-model](https://github.com/HodgeLab/datacenter-siting-model) | 0 | Optimisation d'implantation à l'échelle des comtés américains | Faible : modèle d'optimisation, hors de notre échelle |
| [jbromain/rte-tempo](https://github.com/jbromain/rte-tempo), [hekmon/rtetempo](https://github.com/hekmon/rtetempo) | 56, 208 | Accès au calendrier Tempo | Faible : déjà couvert par `extract_rte.py` |
| [kamaradclimber/rte-ecowatt](https://github.com/kamaradclimber/rte-ecowatt) | 72 | Signal EcoWatt de RTE dans Home Assistant | Faible : signal national et de court terme |
| [electricitymaps/electricitymaps-contrib](https://github.com/electricitymaps/electricitymaps-contrib) | 4 044 | Parseurs de données électriques mondiaux | Faible : éCO2mix déjà ingéré directement |

## Fonctionnalités à reprendre

Classées par priorité. Toutes restent compatibles avec les choix du projet : une
commande, pas d'application ; aucun chiffre sans sa limite.

### Priorité 1 — forte valeur, données vérifiées

**1. Risques naturels du site, à l'adresse exacte (API Géorisques).**
*Inspiré de* `wa-datacenter-suitability` (sismicité, inondation comme indicateurs de
risque) et de ses critères éliminatoires.
C'est le seul indicateur **réellement propre à l'adresse** : le prix est national et la
tension régionale. Nouveau bloc « Risques du site » : zone de sismicité, inondation,
remontée de nappe, mouvements de terrain, installations classées à proximité.
*Données* : **vérifié le 15 oct.** — l'API Géorisques répond sans clé, par coordonnées
(`/api/v1/gaspar/risques?latlon=…` : 17 risques pour Lyon 2e ;
`/api/v1/zonage_sismique?latlon=…` : zone 2, faible). Les coordonnées viennent déjà de
la BAN.
*Effort* : ½ à 1 journée.

**2. Calibrer les scénarios de prix par backtest.** — **fait le 15 octobre 2026** (feuille de route, 3.8) : l'éventail n'encadrait le prix réel que 3 à 9 fois sur 17 ; il est désormais calibré sur les erreurs passées de la méthode (3.9).
*Inspiré de* `carbon-fr` (prévisions gardées par backtest) et `siting-model` (contrôle
de réalité du score).
Rejouer la méthode sur l'historique : à chaque semestre passé, calculer l'éventail
bas–haut à 3 ans avec les seules données disponibles alors, puis mesurer combien de
fois le prix réel est tombé dedans. Afficher ce taux à côté des scénarios (« sur
2010-2023, le prix réel est tombé dans l'éventail N fois sur M »). Répond directement à
la question de méthode ouverte le 12 octobre : le scénario bas presque toujours en
hausse.
*Données* : déjà en base (`silver.prix_entreprises`).
*Effort* : ½ journée, pur dbt.

**3. Importer la vraie courbe de charge de l'entreprise (`--courbe export.csv`).**
*Inspiré de* `EDF_Simulateur_Prix` (formats d'export EDF, Enedis, TotalEnergies ;
traitement local, aucune donnée envoyée).
Remplace le profil illustratif du bloc 5 par la consommation réelle : coût et carbone
heure par heure, gain de décalage réel. La consommation annuelle en découle, donc la
tranche Eurostat aussi, sans saisir `--conso`.
*Données* : export Enedis au pas horaire ou 30 min (format à relever sur un vrai
fichier). Rien ne quitte la machine.
*Effort* : 1 journée.

**4. Contraintes du réseau de transport à proximité de l'adresse.**
*Découvert pendant cette veille*, dans le catalogue ODRÉ.
Lister les ouvrages contraints dans un rayon de N km : nom, postes concernés,
fréquence, durée, pérennité.
*Données* : **vérifié le 15 oct.** — jeu ODRÉ `contraintes-region`, 407 ouvrages
géolocalisés (forme et centroïde), vision prospective à 3–5 ans, hors Corse. Il
complète `energies-et-puissances-regionales-liees-au-contraintes`, déjà ingéré.
*Limite à afficher* : contraintes liées à l'**injection** d'énergies renouvelables,
données à titre indicatif par RTE ; elles ne mesurent pas la capacité disponible en
soutirage.
*Effort* : ½ à 1 journée.

**5. Comparer plusieurs adresses (`compare --adresse A --adresse B …`).**
*Inspiré de* `datacenter-map` (priorités pondérées, comparaison côte à côte, « le
changement de note qui fait le plus bouger le rang »), de `wa-datacenter-suitability`
(notes 0–1, critères éliminatoires) et de `siting-model` (pondérations ajustables).
Un tableau côte à côte des indicateurs, un classement selon des priorités choisies,
et des critères éliminatoires explicites (exemples : zone inondable, puissance en zone
grise HTA/HTB). C'est l'usage réel d'une entreprise qui hésite entre des sites.
*Données* : celles du diagnostic.
*Effort* : 1 à 2 jours. Les pondérations sont un choix produit à valider ensemble.

### Priorité 2 — rigueur et confiance

**6. Méthodologie versionnée et niveau de confiance par indicateur.**
*Inspiré de* `carbon-fr` (chaque mesure porte `methodology` + `methodology_version`,
jamais de changement silencieux) et de `siting-model` (confiance haute / moyenne /
basse affichée, « l'incertitude est préservée, pas blanchie »).
Ajouter `version_methode` et `confiance` au seed `limites_methodologiques`, et les
reporter dans la sortie JSON et le rapport.
*Effort* : 2 heures.

**7. Rapport reproductible : la commande et le millésime des données en pied de page.**
*Inspiré de* `datacenter-map` (tout le scénario tient dans le lien) et de `siting-model`
(« chaque chiffre remonte à sa source »).
Le rapport imprime la commande exacte qui le régénère et le millésime de chaque bloc
(année IRIS, mois d'équilibre, dernier semestre Eurostat, mois du profil).
*Effort* : 2 heures.

**8. Densité de postes RTE autour du site.**
*Inspiré de* `gridsuite/odre-server` et de l'indicateur « proximité des lignes HT » de
`wa-datacenter-suitability`.
*Données* : **vérifié le 15 oct.** — `postes-electriques-rte` (5 042 postes, tension et
état) **ne publie plus de coordonnées** : seulement le département. La distance au
poste le plus proche n'est donc **pas calculable** avec ce jeu. Reste possible : le
nombre de postes par département et par tension (`postes-electriques-rte`,
`postes-electriques-region-tension`), en repère du bloc raccordement.
*Effort* : ½ journée.

**9. Coût de raccordement indicatif.**
*Inspiré de* `tool-raccordement` : sa grille de questions est la bonne (puissance par
paliers ≤ 36, 36–120, 120–250, > 250 kVA ; distance au réseau ou au poste ;
agglomération de plus de 100 000 habitants).
*Données* : **à trouver**. Le calcul n'est pas dans le dépôt ; il faudrait partir du
barème de raccordement publié par Enedis et approuvé par la CRE, avec la réfaction.
*Effort* : 1 journée, une fois la source officielle trouvée.

### Priorité 3 — décision à prendre ensemble

**10. Intensité carbone régionale, estimée et étiquetée comme telle.**
`carbon-fr` dérive une intensité régionale en appliquant les facteurs ADEME au mix de
production de chaque région (méthode `acv-ademe`), en le disant clairement. Notre
cadrage l'avait écartée comme non opposable, et ça reste vrai : ce serait une
**estimation**, pas un facteur officiel. Demande aussi d'ingérer
`eco2mix-regional-cons-def` (2,86 M lignes, reporté jusqu'ici).
*À trancher* : vaut-il mieux une estimation étiquetée, ou rien ?

## À ne pas reprendre

| Idée | Où | Pourquoi |
|---|---|---|
| Carte interactive, interface web | Tous les modèles de data centers | Choix du projet : une commande, pas d'application |
| API REST, webhooks, flux temps réel | `carbon-fr` | Hors périmètre : le diagnostic est ponctuel |
| Prix spot horaire (ENTSO-E) | `carbon-fr`, intégrations domotiques | Décision du 12 octobre : prix fixes et historiques |
| Signal EcoWatt | `rte-ecowatt` | National et à 4 jours : ne différencie pas les sites |
| Optimisation de capacité | `HodgeLab` | Hors de notre échelle (une adresse, pas un parc) |
| Comparatif coût de production (LCOE) | `carbon-fr` ADR-0024 | Débat politique, sans lien avec le choix d'un site |

## Ordre proposé

1. ~~Backtest des scénarios de prix (2)~~ — fait le 15 octobre.
2. Risques naturels Géorisques (1), le gain le plus net pour un diagnostic « par adresse ».
3. Méthodologie versionnée et rapport reproductible (6, 7), peu coûteux.
4. Contraintes réseau à proximité (4), puis comparaison d'adresses (5).
5. Courbe de charge réelle (3), coût de raccordement (9) une fois sa source trouvée.
