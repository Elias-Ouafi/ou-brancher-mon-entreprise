# Pistes de recherche : étendre l'outil à d'autres pays

> Rédigé le 16 octobre 2026, **à instruire après le MVP français** ([feuille de route](feuille-de-route.md)).
> Ce sont des pistes, pas des vérifications : chaque source est à tester sur son API (volume,
> licence, fraîcheur) avant d'être retenue, comme l'a été l'open data français le 9 octobre
> ([cadrage, § 6](cadrage-besoins-entreprises.md)).

## Ce qui change hors de France

Le MVP français tient sur deux simplifications qui ne se transposent pas partout :

| Simplification française | Ailleurs |
|---|---|
| Prix identique partout (péréquation du TURPE) | Les tarifs de réseau varient par gestionnaire en **Allemagne** (Netzentgelte, plusieurs centaines de gestionnaires) ou par opérateur aux **États-Unis** : le prix y devient géographique |
| Un seul réseau continental, donc un seul carbone | Plusieurs **zones de prix** (bidding zones) en Italie, Suède, Norvège, Danemark : le carbone et le prix de gros diffèrent d'une zone à l'autre. Le Royaume-Uni publie même une intensité **régionale** |
| Un géocodeur national gratuit (BAN) | À remplacer par un géocodeur multi-pays |

L'architecture n'a pas à changer : chaque pays ajoute des jeux en bronze, des modèles silver
qui les conforment, et une colonne `code_pays` dans les tables gold.

## Prix moyen

| Piste | Couverture | Intérêt | Effort |
|---|---|---|---|
| **Eurostat `nrg_pc_205` (entreprises) et `nrg_pc_204` (ménages)** | UE-27, EEE, pays candidats | **Déjà ingéré** : retirer le filtre sur la France suffit. Même méthode, mêmes tranches | très faible |
| Tarifs de réseau régionaux (Allemagne : publications des gestionnaires, agrégées par la BNetzA) | un pays à la fois | Rend le prix géographique là où il l'est vraiment | élevé |
| EIA (États-Unis), API publique | par État et par secteur | Prix moyens mensuels, gratuits | faible |
| Prix de gros par zone (ENTSO-E) | Europe | Utile pour les gros consommateurs indexés sur le marché ; hors besoin du MVP (décision du 12 oct.) | moyen |

## Carbone

Du plus simple au plus précis :

| Piste | Couverture | Maille | Licence, accès | Usage proposé |
|---|---|---|---|---|
| **Ember** (données annuelles et mensuelles d'électricité) | ~200 pays | pays, annuel ou mensuel | CC BY 4.0, téléchargement libre | **Première extension** : un verdict annuel par pays, en seed ou en ingestion mensuelle |
| Électricité par filière, **ENTSO-E Transparency Platform** | Europe | zone de prix, horaire | gratuit, jeton sur inscription | Intensité horaire par zone, calculée avec des facteurs par filière (GIEC ou ADEME) : l'équivalent européen de `fct_mix` |
| **Carbon Intensity API** (opérateur britannique) | Grande-Bretagne | 14 régions, demi-heure | publique, sans clé | Seul cas européen d'intensité régionale officielle |
| EPA eGRID et EIA | États-Unis | sous-région du réseau, annuel | public | Le carbone y dépend vraiment de l'adresse |
| Electricity Maps | mondial | zone, horaire | offre gratuite limitée à l'usage non commercial | Référence de contrôle, pas une source à redistribuer |

Le seuil de verdict (100 gCO₂e/kWh proposé pour la France) est européen et vaut donc pour
toute l'UE ; hors UE, il faudra le justifier à nouveau.

## Adresse → territoire

| Besoin | Piste |
|---|---|
| Géocodage multi-pays | **Nominatim** (OpenStreetMap, gratuit, 1 requête par seconde) ou Photon, auto-hébergeable ; prévoir un cache des réponses |
| Point → zone de prix ou région carbone | Contours des zones publiés par le projet open source `electricitymaps-contrib` (licence à vérifier) ; découpage NUTS de GISCO (Eurostat) pour les régions européennes |
| Point-dans-polygone | Extension `spatial` de DuckDB : pas de nouvel outil |

## Volumes et outils

Une Europe horaire représente quelques dizaines de millions de lignes sur dix ans : DuckDB les
traite sur un ordinateur portable, sans cluster. Le stockage pourra passer à BigQuery derrière
`WarehouseLoader` si le besoin d'un entrepôt partagé apparaît.

## Ordre proposé

1. **UE-27, maille pays** : prix Eurostat (déjà là) et carbone Ember. Le géocodeur Nominatim
   ne sert qu'à trouver le pays.
2. **Zones de prix** : ENTSO-E et le point-dans-polygone, pour l'Italie et les pays nordiques.
3. **Royaume-Uni** (intensité régionale) et **États-Unis** (EIA et eGRID), où le prix et le
   carbone dépendent vraiment de l'adresse.

## Questions ouvertes

- Faut-il un verdict horaire (score 24/7) plutôt qu'annuel une fois plusieurs pays couverts ?
- Les tarifs de réseau allemands sont-ils assez ouverts et stables pour être industrialisés ?
- Les licences d'Ember et des contours de zones autorisent-elles un usage commercial ?
