from __future__ import annotations

import bisect
import gzip
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

KM_PAR_DEGRE_LAT = 110.57
KM_PAR_DEGRE_LON = 111.32

# Rayon d'une case dans la scène 3D : toutes les grilles y ont la même taille
RAYON_SCENE = 1.2
RAYON_METROPOLE_KM = 12.0

LON0_METROPOLE, LAT0_METROPOLE = 2.4, 46.6

# Départements d'outre-mer ; les collectivités (977, 984…) restent hors carte
DEPARTEMENTS_OUTRE_MER = ("971", "972", "973", "974", "976")

# Cases terrestres visées par encart d'outre-mer, quelle que soit sa superficie
CASES_PAR_ENCART = 70
MARGE_METROPOLE, MARGE_ENCART = 3, 2

# Écart entre deux cartes dans la scène : au-delà du brouillard, une seule carte se voit à la fois
ECART_CARTES = 600.0

# Échantillons par case : le centre puis six points à 60 % du rayon
FRACTION_ECHANTILLON = 0.6
# Une case dont le centre est en mer reste terrestre si 3 de ses 7 échantillons sont à terre
SEUIL_TERRE = 3

Anneau = tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Departement:
    code: str
    nom: str
    region: str
    # Tous les anneaux (extérieurs et trous) en (lon, lat) : la règle pair-impair suffit
    anneaux: tuple[Anneau, ...]

    @property
    def outre_mer(self) -> bool:
        return self.code in DEPARTEMENTS_OUTRE_MER


# Projection équirectangulaire locale, en km ; z croît vers le sud comme dans la scène
@dataclass(frozen=True, slots=True)
class Projection:
    lon0: float
    lat0: float

    @property
    def _kx(self) -> float:
        return KM_PAR_DEGRE_LON * math.cos(math.radians(self.lat0))

    def km(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon - self.lon0) * self._kx, -(lat - self.lat0) * KM_PAR_DEGRE_LAT

    def geo(self, x: float, z: float) -> tuple[float, float]:
        return x / self._kx + self.lon0, -z / KM_PAR_DEGRE_LAT + self.lat0


@dataclass(slots=True)
class Case:
    c: int
    r: int
    lon: float
    lat: float
    departement: str | None = None
    etranger: bool = False
    # Échantillons tombés à terre, en (lon, lat) : ils servent au relief
    echantillons: list[tuple[float, float]] = field(default_factory=list)


@dataclass(slots=True)
class Grille:
    nom: str
    code: str | None
    projection: Projection
    rayon_km: float
    x0_km: float
    z0_km: float
    colonnes: int
    lignes: int
    cases: list[Case]
    scene_x0: float = 0.0
    scene_z0: float = 0.0

    @property
    def echelle(self) -> float:
        return RAYON_SCENE / self.rayon_km

    def centre_km(self, c: int, r: int) -> tuple[float, float]:
        largeur = math.sqrt(3) * self.rayon_km
        x = self.x0_km + c * largeur + (r & 1) * largeur / 2
        return x, self.z0_km + r * 1.5 * self.rayon_km

    def case_terrestre(self, lon: float, lat: float, departement: str | None = None) -> Case | None:
        x, z = self.projection.km(lon, lat)
        candidates = [
            case
            for case in self.cases
            if case.departement and (departement is None or case.departement == departement)
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda case: math.dist((x, z), self.centre_km(case.c, case.r)),
        )


def _lire_geojson(path: Path) -> list[dict]:
    ouvrir = gzip.open if path.suffix == ".gz" else open
    with ouvrir(path, "rt", encoding="utf-8") as fichier:
        return json.load(fichier)["features"]


def _anneaux(geometrie: dict) -> tuple[Anneau, ...]:
    polygones = (
        [geometrie["coordinates"]] if geometrie["type"] == "Polygon" else geometrie["coordinates"]
    )
    return tuple(
        tuple((float(point[0]), float(point[1])) for point in anneau)
        for polygone in polygones
        for anneau in polygone
    )


# Départements lus dans les contours Etalab
def lire_contours(path: Path) -> list[Departement]:
    return [
        Departement(
            code=str(feature["properties"]["code"]),
            nom=feature["properties"]["nom"],
            region=str(feature["properties"].get("region") or ""),
            anneaux=_anneaux(feature["geometry"]),
        )
        for feature in _lire_geojson(path)
    ]


# Côtes du monde (Natural Earth) : la carte ne pose pas de case en mer
def lire_terres(path: Path) -> list[Anneau]:
    return [anneau for feature in _lire_geojson(path) for anneau in _anneaux(feature["geometry"])]


def _aire(anneau: list[tuple[float, float]]) -> float:
    return (
        abs(
            sum(
                x1 * z2 - x2 * z1
                for (x1, z1), (x2, z2) in zip(anneau, anneau[1:] + anneau[:1], strict=True)
            )
        )
        / 2
    )


def _centroide(anneau: list[tuple[float, float]]) -> tuple[float, float]:
    a = cx = cz = 0.0
    for (x1, z1), (x2, z2) in zip(anneau, anneau[1:] + anneau[:1], strict=True):
        croix = x1 * z2 - x2 * z1
        a += croix
        cx += (x1 + x2) * croix
        cz += (z1 + z2) * croix
    if abs(a) < 1e-12:
        return sum(x for x, _ in anneau) / len(anneau), sum(z for _, z in anneau) / len(anneau)
    return cx / (3 * a), cz / (3 * a)


# Département de chaque point, par balayage ligne à ligne (les points partagent peu de z)
def _localiser(
    points: list[tuple[float, float]], polygones: dict[str, list[list[tuple[float, float]]]]
) -> list[str | None]:
    resultat: list[str | None] = [None] * len(points)
    par_ligne: dict[float, list[int]] = defaultdict(list)
    for i, (_, z) in enumerate(points):
        par_ligne[z].append(i)
    lignes = sorted(par_ligne)
    for code, anneaux in polygones.items():
        if not anneaux:
            continue
        aretes = [
            (x1, z1, x2, z2)
            for anneau in anneaux
            for (x1, z1), (x2, z2) in zip(anneau, anneau[1:] + anneau[:1], strict=True)
            if z1 != z2
        ]
        zmin = min(z for anneau in anneaux for _, z in anneau)
        zmax = max(z for anneau in anneaux for _, z in anneau)
        for z in lignes[bisect.bisect_left(lignes, zmin) : bisect.bisect_right(lignes, zmax)]:
            xs = sorted(
                x1 + (z - z1) * (x2 - x1) / (z2 - z1)
                for x1, z1, x2, z2 in aretes
                if (z1 > z) != (z2 > z)
            )
            if not xs:
                continue
            for i in par_ligne[z]:
                x = points[i][0]
                if xs[0] <= x <= xs[-1] and bisect.bisect(xs, x) % 2:
                    resultat[i] = code
    return resultat


# Grille hexagonale (pointe en haut, lignes impaires décalées) couvrant des départements
def _grille(
    nom: str,
    code: str | None,
    departements: list[Departement],
    rayon_km: float,
    marge: int,
    projection: Projection | None = None,
    terres: list[Anneau] | None = None,
) -> Grille:
    if projection is None:
        lons = [lon for d in departements for a in d.anneaux for lon, _ in a]
        lats = [lat for d in departements for a in d.anneaux for _, lat in a]
        projection = Projection((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)
    polygones = {
        d.code: [[projection.km(lon, lat) for lon, lat in anneau] for anneau in d.anneaux]
        for d in departements
    }
    xs = [x for anneaux in polygones.values() for a in anneaux for x, _ in a]
    zs = [z for anneaux in polygones.values() for a in anneaux for _, z in a]
    largeur, hauteur = math.sqrt(3) * rayon_km, 1.5 * rayon_km
    x0, z0 = min(xs) - marge * largeur, min(zs) - marge * hauteur
    colonnes = math.ceil((max(xs) + marge * largeur - x0) / largeur) + 1
    lignes = math.ceil((max(zs) + marge * hauteur - z0) / hauteur) + 1
    grille = Grille(nom, code, projection, rayon_km, x0, z0, colonnes, lignes, [])

    decalages = [(0.0, 0.0)] + [
        (
            FRACTION_ECHANTILLON * rayon_km * math.sin(math.radians(60 * k)),
            FRACTION_ECHANTILLON * rayon_km * math.cos(math.radians(60 * k)),
        )
        for k in range(6)
    ]
    points = []
    for r in range(lignes):
        for c in range(colonnes):
            cx, cz = grille.centre_km(c, r)
            lon, lat = projection.geo(cx, cz)
            grille.cases.append(Case(c, r, lon, lat))
            points.extend((cx + dx, cz + dz) for dx, dz in decalages)
    codes = _localiser(points, polygones)

    for n, case in enumerate(grille.cases):
        bloc = codes[n * 7 : n * 7 + 7]
        terrestres = [code for code in bloc if code]
        if bloc[0] or len(terrestres) >= SEUIL_TERRE:
            case.departement = bloc[0] or Counter(terrestres).most_common(1)[0][0]
            case.echantillons = [
                projection.geo(*points[n * 7 + j]) for j, code in enumerate(bloc) if code
            ]

    # Hors de France, le centre de la case dit si c'est une terre voisine ou la mer
    if terres:
        lon_min, lat_min = projection.geo(x0 - largeur, z0 + lignes * hauteur)
        lon_max, lat_max = projection.geo(x0 + colonnes * largeur, z0 - hauteur)
        proches = [
            [projection.km(lon, lat) for lon, lat in anneau]
            for anneau in terres
            if min(lon for lon, _ in anneau) <= lon_max
            and max(lon for lon, _ in anneau) >= lon_min
            and min(lat for _, lat in anneau) <= lat_max
            and max(lat for _, lat in anneau) >= lat_min
        ]
        hors_france = [case for case in grille.cases if not case.departement]
        centres = [grille.centre_km(case.c, case.r) for case in hors_france]
        for case, code in zip(hors_france, _localiser(centres, {"terre": proches}), strict=True):
            case.etranger = code is not None

    # Un département plus petit qu'une case en reçoit tout de même une, prise à un voisin
    compte = Counter(case.departement for case in grille.cases if case.departement)
    aires = {code: sum(_aire(a) for a in anneaux) for code, anneaux in polygones.items()}
    for code in sorted(aires, key=aires.get):
        if compte[code]:
            continue
        principal = max(polygones[code], key=_aire)
        cible = _centroide(principal)
        candidates = [
            case
            for case in grille.cases
            if case.departement is None or compte[case.departement] > 1
        ]
        case = min(candidates, key=lambda k: math.dist(cible, grille.centre_km(k.c, k.r)))
        if case.departement:
            compte[case.departement] -= 1
        else:
            case.echantillons = [projection.geo(*cible)]
        case.departement = code
        compte[code] = 1
    return grille


# Une carte par système électrique : métropole et Corse, puis chaque département d'outre-mer
def construire_grilles(
    departements: list[Departement],
    *,
    terres: list[Anneau] | None = None,
    rayon_km: float = RAYON_METROPOLE_KM,
) -> list[Grille]:
    metropole = [d for d in departements if len(d.code) == 2]
    largeur_s, hauteur_s = math.sqrt(3) * RAYON_SCENE, 1.5 * RAYON_SCENE
    grille = _grille(
        "France métropolitaine",
        None,
        metropole,
        rayon_km,
        MARGE_METROPOLE,
        Projection(LON0_METROPOLE, LAT0_METROPOLE),
        terres,
    )
    grilles = [grille]

    encarts = []
    for departement in sorted((d for d in departements if d.outre_mer), key=lambda d: d.code):
        projection = Projection(
            *[
                sum(v) / len(v)
                for v in zip(*[p for a in departement.anneaux for p in a], strict=True)
            ]
        )
        aire = sum(
            _aire([projection.km(lon, lat) for lon, lat in anneau])
            for anneau in departement.anneaux
        )
        rayon = max(0.5, round(math.sqrt(aire / (2.598 * CASES_PAR_ENCART)) * 2) / 2)
        encarts.append(
            _grille(
                departement.nom,
                departement.code,
                [departement],
                rayon,
                MARGE_ENCART,
                projection,
                terres,
            )
        )

    grilles += encarts
    # Chaque carte centrée sur son propre point, loin des autres
    for i, g in enumerate(grilles):
        g.scene_x0 = i * ECART_CARTES - (g.colonnes - 1) * largeur_s / 2
        g.scene_z0 = -(g.lignes - 1) * hauteur_s / 2
    return grilles


# Échantillons terrestres de toutes les grilles, arrondis au mètre près
def points_terrestres(grilles: list[Grille]) -> list[tuple[float, float]]:
    return sorted(
        {
            arrondir(lon, lat)
            for grille in grilles
            for case in grille.cases
            for lon, lat in case.echantillons
        }
    )


# Coordonnées arrondies : clé stable d'une case entre deux refresh
def arrondir(lon: float, lat: float) -> tuple[float, float]:
    return round(lon, 5), round(lat, 5)
