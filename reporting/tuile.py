from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon
from reportlab.lib import colors

from ingestion.config import Settings
from ingestion.diagnostic import DonneesAbsentesError
from ingestion.extract_geo import CONTOURS_JEU, dernier_geojson, lire_altitudes
from ingestion.geocode import Territoire
from ingestion.hexagones import arrondir, construire_grilles, lire_contours
from reporting import charte
from reporting.carte import _nombre, lire_donnees_gold, reperer

MEDITERRANEE = {"06", "11", "13", "30", "34", "66", "83", "84", "2A", "2B"}
NOMS = {
    "Eolien": "Éolien",
    "Thermique non renouvelable": "Thermique fossile",
    "Energies Marines": "Énergies marines",
    "port": "Port maritime",
    "aeroport": "Aéroport",
    "rail": "Chantier rail-route",
    "site": "Site clé en main",
}


@dataclass(frozen=True, slots=True)
class Piece:
    # Filière de production, mode de fret ou `site`
    nature: str
    nom: str
    puissance_mw: float | None = None


@dataclass(frozen=True, slots=True)
class Tuile:
    departement: str
    lat: float
    lon: float
    rayon_km: float
    altitude_m: float
    altitude_max_m: float
    outre_mer: bool = False
    pieces: list[Piece] = field(default_factory=list)
    zone_rte: str | None = None


def lire_tuile(territoire: Territoire, settings: Settings) -> Tuile | None:
    # Sans contours chargés, le rapport se fait sans tuile : jamais d'appel réseau ici
    contours = dernier_geojson(CONTOURS_JEU, settings)
    if contours is None:
        return None
    grilles = construire_grilles(lire_contours(contours))
    lon, lat, dep = territoire.longitude, territoire.latitude, territoire.code_departement
    ici = reperer(grilles, lon, lat, dep)
    if ici is None:
        return None
    try:
        gold = lire_donnees_gold(Path(settings.duckdb_path))
    except DonneesAbsentesError:
        return None
    grille = grilles[ici[0]]
    case = next(k for k in grille.cases if (k.c, k.r) == (ici[1], ici[2]))
    altitudes = lire_altitudes(settings)
    mesures = [a for p in case.echantillons if (a := altitudes.get(arrondir(*p))) is not None]

    def sur_la_case(ligne: dict) -> bool:
        return reperer(grilles, ligne["lo"], ligne["la"], ligne.get("c")) == ici

    pieces = [
        Piece(p["f"], p["n"], p["mw"]) for p in gold.get("production", []) if sur_la_case(p)
    ] + [Piece(p["m"], p["n"]) for p in gold.get("fret", []) if sur_la_case(p)]
    pieces += [Piece("site", s["n"]) for s in gold["sites"] if sur_la_case(s)]
    zone = next((z["n"] for z in gold["zones"] if sur_la_case(z)), None)
    return Tuile(
        departement=case.departement or dep,
        lat=case.lat,
        lon=case.lon,
        rayon_km=grille.rayon_km,
        altitude_m=fmean(mesures) if mesures else 0.0,
        altitude_max_m=max(mesures) if mesures else 0.0,
        outre_mer=grille.code is not None,
        pieces=pieces,
        zone_rte=zone,
    )


def legende(tuile: Tuile) -> list[str]:
    lignes = [
        f"<b>{NOMS.get(p.nature, p.nature)}</b> · {p.nom}"
        + (f", {_nombre(p.puissance_mw)} MW" if p.puissance_mw else "")
        for p in tuile.pieces
    ]
    if tuile.zone_rte:
        lignes.append(f"<b>Zone prioritaire RTE</b> · {tuile.zone_rte}")
    if not lignes:
        lignes.append("Aucun pôle au-dessus des seuils sur cette case.")
    return lignes


def _melange(a: str, b: str, part: float) -> str:
    ca, cb = colors.HexColor(a), colors.HexColor(b)
    p = max(0.0, min(1.0, part))
    canaux = (
        round(255 * (x + (y - x) * p))
        for x, y in ((ca.red, cb.red), (ca.green, cb.green), (ca.blue, cb.blue))
    )
    return "#{:02X}{:02X}{:02X}".format(*canaux)


# Mêmes teintes que la vue Territoire de la carte
def teinte_sol(t: Tuile) -> str:
    base = "#5F8F3E" if t.lat > 48.6 else "#6C9A45" if t.lon < 0.5 else "#74A046"
    if t.lat < 45.2 and t.lon < 1.5:
        base = "#8EA24B"
    if t.departement in MEDITERRANEE:
        base = "#B0A35F"
    if t.outre_mer:
        base = "#2F6F3A" if t.departement == "973" else "#4F8F45"
    if t.altitude_m > 300:
        base = _melange(base, "#41632F", (t.altitude_m - 300) / 600)
    if t.altitude_m > 1100:
        base = _melange(base, "#8F8C82", min(0.85, (t.altitude_m - 1100) / 700))
    return base


def _hash(a: float, b: float) -> float:
    s = math.sin(a * 127.1 + b * 311.7) * 43758.5453
    return s - math.floor(s)


Point = tuple[float, float, float]


# Vue isométrique : x vers la droite et l'avant, y vers la gauche et l'avant, z vers le haut. On
# peint de l'arrière vers l'avant : les faces cachées n'ont pas besoin d'être calculées.
class _Isometrie:
    def __init__(self, largeur: float, hauteur: float, echelle: float) -> None:
        self.dessin = Drawing(largeur, hauteur)
        self.cx, self.cy, self.s = largeur / 2, hauteur * 0.42, echelle

    def point(self, x: float, y: float, z: float) -> tuple[float, float]:
        return self.cx + (x - y) * 0.866 * self.s, self.cy + (z - (x + y) * 0.5) * self.s

    def poly(self, points: Sequence[Point], teinte: str, trait: str | None = None) -> None:
        plats = [v for p in points for v in self.point(*p)]
        couleur = colors.HexColor(teinte)
        self.dessin.add(
            Polygon(
                plats,
                fillColor=couleur,
                strokeColor=colors.HexColor(trait) if trait else couleur,
                strokeWidth=0.35 if trait else 0.12,
            )
        )

    def boite(
        self, x: float, y: float, z: float, lx: float, ly: float, h: float, teinte: str
    ) -> None:
        x0, x1, y0, y1 = x - lx / 2, x + lx / 2, y - ly / 2, y + ly / 2
        self.poly(
            [(x1, y0, z), (x1, y1, z), (x1, y1, z + h), (x1, y0, z + h)],
            _melange(teinte, "#13281F", 0.3),
        )
        self.poly(
            [(x0, y1, z), (x1, y1, z), (x1, y1, z + h), (x0, y1, z + h)],
            _melange(teinte, "#13281F", 0.16),
        )
        self.poly(
            [(x0, y0, z + h), (x1, y0, z + h), (x1, y1, z + h), (x0, y1, z + h)],
            _melange(teinte, "#FFFFFF", 0.1),
        )

    def revolution(
        self, x: float, y: float, z: float, profil: list[tuple[float, float]], teinte: str
    ) -> None:
        r0, rf = profil[0][0], profil[-1][0]
        sx, sy = math.sqrt(0.5), -math.sqrt(0.5)

        def arc(r: float, h: float, a0: float, a1: float) -> list[Point]:
            pas = [a0 + (a1 - a0) * k / 12 for k in range(13)]
            return [(x + r * math.cos(a), y + r * math.sin(a), z + h) for a in pas]

        cote = [(x + r * sx, y + r * sy, z + h) for r, h in profil]
        autre = [(x - r * sx, y - r * sy, z + h) for r, h in reversed(profil)]
        devant = arc(r0, profil[0][1], math.radians(135), math.radians(-45))
        self.poly(devant + cote + autre, teinte)
        moitie = arc(r0, profil[0][1], math.radians(45), math.radians(-45))
        axe = [(x, y, z + profil[-1][1]), (x + r0 * 0.7071, y + r0 * 0.7071, z + profil[0][1])]
        self.poly(moitie + cote + axe, _melange(teinte, "#13281F", 0.18))
        if rf > 0.005:
            self.poly(
                arc(rf, profil[-1][1], 0, 2 * math.pi - 0.01),
                _melange(teinte, "#FFFFFF", 0.18),
            )

    def cylindre(self, x: float, y: float, z: float, r: float, h: float, teinte: str) -> None:
        self.revolution(x, y, z, [(r, 0), (r, h)], teinte)

    def bulle(self, x: float, y: float, z: float, r: float, teinte: str) -> None:
        cx, cy = self.point(x, y, z)
        self.dessin.add(
            Circle(cx, cy, r * self.s, fillColor=colors.HexColor(teinte), strokeColor=None)
        )

    def trait(self, a: Point, b: Point, teinte: str, epaisseur: float) -> None:
        self.dessin.add(
            Line(
                *self.point(*a),
                *self.point(*b),
                strokeColor=colors.HexColor(teinte),
                strokeWidth=epaisseur,
                strokeLineCap=1,
            )
        )


def _nucleaire(d: _Isometrie, x: float, y: float, e: float, piece: Piece) -> None:
    # Un réacteur pour 1 200 MW environ, 4 au plus, comme les maquettes de la carte
    n = max(1, min(4, round((piece.puissance_mw or 1200) / 1200)))
    pas, k = 0.26 * e, 0.8 if n > 2 else 1.0
    for j in range(n):
        dx = (j - (n - 1) / 2) * pas
        tx, ty = x - 0.26 * e + dx, y - 0.2 * e - dx
        d.revolution(
            tx,
            ty,
            0,
            [
                (0.13 * e * k, 0),
                (0.085 * e * k, 0.2 * e),
                (0.075 * e * k, 0.25 * e),
                (0.09 * e * k, 0.32 * e),
            ],
            "#E9DCB8",
        )
        d.bulle(tx, ty, 0.38 * e, 0.05 * e * k, "#F4F6F4")
        d.bulle(tx + 0.03 * e, ty - 0.03 * e, 0.44 * e, 0.035 * e * k, "#F4F6F4")
    for j in range(n):
        dx = (j - (n - 1) / 2) * pas
        rx, ry = x + 0.1 * e + dx, y + 0.1 * e - dx
        d.boite(rx - 0.1 * e, ry - 0.1 * e, 0, 0.12 * e * k, 0.12 * e * k, 0.06 * e, "#8F9AA3")
        d.cylindre(rx + 0.04 * e, ry + 0.04 * e, 0, 0.075 * e * k, 0.11 * e, "#CFC9BA")
        d.revolution(
            rx + 0.04 * e,
            ry + 0.04 * e,
            0.11 * e,
            [(0.075 * e * k, 0), (0.06 * e * k, 0.04 * e), (0.02 * e * k, 0.075 * e)],
            "#CFC9BA",
        )


def _hydraulique(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.revolution(x - 0.32 * e, y - 0.02 * e, 0, [(0.2 * e, 0), (0.01, 0.42 * e)], "#7D7466")
    d.revolution(x - 0.02 * e, y - 0.32 * e, 0, [(0.18 * e, 0), (0.01, 0.34 * e)], "#7D7466")
    d.poly(
        [
            (x - 0.32 * e, y + 0.28 * e, 0.16 * e),
            (x + 0.28 * e, y - 0.32 * e, 0.16 * e),
            (x - 0.02 * e, y - 0.32 * e, 0.16 * e),
            (x - 0.32 * e, y - 0.02 * e, 0.16 * e),
        ],
        "#3C87B5",
    )
    voute = [
        (x - 0.32 * e + 0.6 * e * k / 10 + 0.1 * e * math.sin(k * math.pi / 10),
         y + 0.28 * e - 0.6 * e * k / 10 + 0.1 * e * math.sin(k * math.pi / 10))
        for k in range(11)
    ]  # fmt: skip
    d.poly(
        [(a, b, 0) for a, b in voute] + [(a, b, 0.24 * e) for a, b in reversed(voute)], "#D6D2C6"
    )
    d.boite(x + 0.2 * e, y + 0.2 * e, 0, 0.14 * e, 0.1 * e, 0.06 * e, "#BDB8AB")


def _thermique(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.revolution(x - 0.26 * e, y - 0.24 * e, 0, [(0.06 * e, 0), (0.035 * e, 0.62 * e)], "#D8D4CB")
    d.cylindre(x - 0.26 * e, y - 0.24 * e, 0.46 * e, 0.045 * e, 0.05 * e, "#B0603F")
    d.bulle(x - 0.26 * e, y - 0.24 * e, 0.7 * e, 0.06 * e, "#C3C5C4")
    d.boite(x - 0.02 * e, y - 0.02 * e, 0, 0.24 * e, 0.24 * e, 0.28 * e, "#9A958A")
    d.boite(x + 0.2 * e, y + 0.04 * e, 0, 0.14 * e, 0.3 * e, 0.12 * e, "#8B867C")
    d.cylindre(x + 0.05 * e, y + 0.3 * e, 0, 0.08 * e, 0.08 * e, "#C9C3B4")


def _eolien(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    for dx, dy, k in ((-0.22, -0.2, 0.8), (0.0, 0.0, 1.0), (0.24, 0.18, 0.72)):
        px, py, h = x + dx * e, y + dy * e, 0.86 * e * k
        d.revolution(px, py, 0, [(0.03 * e * k, 0), (0.014 * e * k, h)], "#F4F6F4")
        for angle in (90 + 40 * dx, 210 + 40 * dx, 330 + 40 * dx):
            r = 0.34 * e * k
            a = math.radians(angle)
            bout = (px + r * math.cos(a) * 0.7, py - r * math.cos(a) * 0.7, h + r * math.sin(a))
            d.trait((px, py, h), bout, "#F4F6F4", 1.3 * d.s / 60 * k)
            d.trait((px, py, h), bout, "#D3D9D4", 0.35)
        d.bulle(px, py, h, 0.022 * e, "#BFC6C1")


def _solaire(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    for rang in (-0.28, 0.0, 0.28):
        for k in (-0.26, 0.0, 0.26):
            px, py = x + rang * e + k * e * 0.2, y - rang * e * 0.2 + k * e
            d.poly(
                [
                    (px - 0.11 * e, py - 0.12 * e, 0.16 * e),
                    (px + 0.11 * e, py - 0.12 * e, 0.04 * e),
                    (px + 0.11 * e, py + 0.12 * e, 0.04 * e),
                    (px - 0.11 * e, py + 0.12 * e, 0.16 * e),
                ],
                "#2F5F8F",
                "#A9C4DC",
            )


def _bio(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.cylindre(x + 0.28 * e, y - 0.22 * e, 0, 0.07 * e, 0.5 * e, "#8B877C")
    profil = [(0.32 * e * math.cos(a / 8), 0.3 * e * math.sin(a / 8)) for a in range(13)]
    d.revolution(x - 0.05 * e, y + 0.05 * e, 0, profil, "#6FBF73")


def _autre(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.revolution(x, y, 0, [(0.32 * e, 0), (0.01, 0.6 * e)], "#2F7FB3")


def _conteneurs(d: _Isometrie, x: float, y: float, z: float, e: float, n: int) -> None:
    teintes = ("#C0392B", "#2F78B8", "#E3B341", "#3D8B4F", "#D9822B")
    for k in range(n):
        d.boite(
            x + (k % 2) * 0.1 * e,
            y + (k // 2) * 0.1 * e,
            z,
            0.09 * e,
            0.09 * e,
            0.07 * e,
            teintes[k % 5],
        )


def _port(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.boite(x - 0.08 * e, y, 0, 0.5 * e, 0.8 * e, 0.04 * e, "#A9A59A")
    _conteneurs(d, x - 0.2 * e, y - 0.28 * e, 0.04 * e, e, 6)
    acier = "#3F8FD2"
    for dy in (-0.14, 0.14):
        d.boite(x + 0.1 * e, y + dy * e, 0.04 * e, 0.03 * e, 0.03 * e, 0.5 * e, acier)
    d.boite(x + 0.2 * e, y, 0.54 * e, 0.5 * e, 0.34 * e, 0.035 * e, acier)
    d.poly(
        [
            (x + 0.2 * e, y - 0.42 * e, 0.01),
            (x + 0.42 * e, y - 0.42 * e, 0.01),
            (x + 0.42 * e, y + 0.42 * e, 0.01),
            (x + 0.2 * e, y + 0.42 * e, 0.01),
        ],
        "#3C87B5",
    )


def _aeroport(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.boite(x - 0.2 * e, y - 0.28 * e, 0, 0.14 * e, 0.14 * e, 0.3 * e, "#E9E4D6")
    d.boite(x - 0.2 * e, y - 0.28 * e, 0.3 * e, 0.2 * e, 0.2 * e, 0.06 * e, "#7FC6E8")
    d.boite(x + 0.18 * e, y, 0, 0.2 * e, 0.92 * e, 0.012 * e, "#4A4D52")
    for k in range(-3, 4):
        d.boite(x + 0.18 * e, y + k * 0.12 * e, 0.012 * e, 0.012 * e, 0.07 * e, 0.002, "#F4F6F4")
    ax, ay, z = x - 0.02 * e, y + 0.12 * e, 0.09 * e
    d.poly([(ax - 0.28 * e, ay, z), (ax + 0.3 * e, ay, z), (ax + 0.3 * e, ay, z + 0.07 * e),
            (ax - 0.28 * e, ay, z + 0.07 * e)], "#F4F6F4", "#AEB6B1")  # fmt: skip
    aile = z + 0.03 * e
    d.poly([(ax - 0.05 * e, ay - 0.3 * e, aile), (ax + 0.06 * e, ay - 0.3 * e, aile),
            (ax + 0.06 * e, ay + 0.3 * e, aile), (ax - 0.05 * e, ay + 0.3 * e, aile)],
           "#E3E7E4", "#AEB6B1")  # fmt: skip
    d.poly([(ax - 0.27 * e, ay, z + 0.07 * e), (ax - 0.17 * e, ay, z + 0.07 * e),
            (ax - 0.27 * e, ay, z + 0.2 * e)], "#3F8FD2")  # fmt: skip


def _rail(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.boite(x, y, 0, 0.32 * e, 0.92 * e, 0.025 * e, "#8A8174")
    for dx in (-0.06, 0.06):
        d.boite(x + dx * e, y, 0.025 * e, 0.015 * e, 0.92 * e, 0.015 * e, "#5B5F66")
    for dy in (-0.24, 0.2):
        d.boite(x, y + dy * e, 0.04 * e, 0.14 * e, 0.34 * e, 0.03 * e, "#6F6C66")
        _conteneurs(d, x - 0.04 * e, y + (dy - 0.08) * e, 0.07 * e, e, 2)
    acier = "#9B7FD1"
    d.boite(x - 0.22 * e, y, 0, 0.03 * e, 0.03 * e, 0.42 * e, acier)
    d.boite(x + 0.22 * e, y, 0, 0.03 * e, 0.03 * e, 0.42 * e, acier)
    d.boite(x, y, 0.42 * e, 0.5 * e, 0.05 * e, 0.04 * e, acier)


def _site(d: _Isometrie, x: float, y: float, e: float, _: Piece) -> None:
    d.boite(x, y, 0, 0.84 * e, 0.66 * e, 0.025 * e, "#CDC6B0")
    d.boite(x - 0.08 * e, y, 0.025 * e, 0.42 * e, 0.34 * e, 0.17 * e, "#B9C2C8")
    d.boite(x - 0.08 * e, y, 0.195 * e, 0.45 * e, 0.37 * e, 0.018 * e, charte.FEUILLE)
    for dy in (-0.11, 0.0, 0.11):
        d.boite(x - 0.08 * e, y + dy * e, 0.215 * e, 0.3 * e, 0.075 * e, 0.012 * e, "#2F5F8F")
    d.boite(x + 0.25 * e, y - 0.18 * e, 0.025 * e, 0.16 * e, 0.16 * e, 0.12 * e, "#E9E4D6")
    d.trait((x + 0.3 * e, y + 0.24 * e, 0), (x + 0.3 * e, y + 0.24 * e, 0.46 * e), "#8F8C82", 0.7)
    d.poly(
        [
            (x + 0.3 * e, y + 0.24 * e, 0.46 * e),
            (x + 0.3 * e, y + 0.09 * e, 0.42 * e),
            (x + 0.3 * e, y + 0.24 * e, 0.36 * e),
        ],
        charte.SOLEIL,
    )


MAQUETTES = {
    "Nucléaire": _nucleaire,
    "Hydraulique": _hydraulique,
    "Thermique non renouvelable": _thermique,
    "Eolien": _eolien,
    "Solaire": _solaire,
    "Bioénergies": _bio,
    "port": _port,
    "aeroport": _aeroport,
    "rail": _rail,
    "site": _site,
}


def _hexagone(z: float) -> list[Point]:
    return [
        (math.sin(math.radians(30 + 60 * k)), math.cos(math.radians(30 + 60 * k)), z)
        for k in range(6)
    ]


def _socle(d: _Isometrie, sol: str) -> None:
    haut, bas = _hexagone(0.0), _hexagone(-0.42)
    milieu = _hexagone(-0.12)
    aretes = sorted(range(6), key=lambda k: haut[k][0] + haut[k][1] + haut[(k + 1) % 6][0])
    for k in aretes:
        s = (k + 1) % 6
        if haut[k][0] + haut[k][1] + haut[s][0] + haut[s][1] < 0:
            continue
        ombre = 0.18 if haut[k][0] + haut[s][0] > 0 else 0.0
        d.poly([bas[k], bas[s], milieu[s], milieu[k]], _melange("#8A6F4D", "#13281F", ombre))
        d.poly([milieu[k], milieu[s], haut[s], haut[k]], _melange(sol, "#13281F", 0.2 + ombre))
    d.poly(haut, sol, _melange(sol, "#FFFFFF", 0.4))


def _montagnes(d: _Isometrie, t: Tuile) -> None:
    hauteur = 0.5 + 0.9 * math.sqrt(max(t.altitude_max_m, 900) / 4800)
    for x, y, k in ((-0.45, -0.3, 0.85), (-0.1, -0.5, 1.0), (0.3, -0.55, 0.7)):
        h = hauteur * k
        d.revolution(x, y, 0, [(0.34 * k, 0), (0.01, h)], "#8B877C")
        if t.altitude_max_m >= 2300:
            d.revolution(x, y, h * 0.66, [(0.34 * k * 0.34, 0), (0.01, h * 0.34)], "#F1F3F4")


def _arbres(d: _Isometrie, t: Tuile) -> None:
    haut = t.altitude_m > 900
    teinte = "#2F5530" if haut else "#7D8C45" if t.departement in MEDITERRANEE else "#3F6F35"
    positions = []
    for j in range(16):
        a, r = _hash(t.lat + j, t.lon) * 6.283, 0.28 + 0.5 * _hash(t.lon, t.lat + j)
        positions.append((r * math.cos(a), r * math.sin(a), 0.1 + 0.08 * _hash(j, t.lat)))
    for x, y, s in sorted(positions, key=lambda p: p[0] + p[1]):
        d.cylindre(x, y, 0, s * 0.13, s * 0.9, "#6B4F2A")
        nuance = _melange(teinte, "#FFFFFF", 0.14 * _hash(x, y))
        if haut:
            d.revolution(x, y, s * 0.5, [(s * 0.55, 0), (0.005, s * 1.7)], nuance)
        else:
            d.bulle(x, y, s * 1.15, s * 0.62, nuance)


def _epingle(d: _Isometrie) -> None:
    x, y = 0.56, -0.2
    d.trait((x, y, 0), (x, y, 0.72), charte.FEUILLE, 1.1)
    px, py = d.point(x, y, 0.84)
    for k in range(8):
        a = k * math.pi / 4
        r0, r1 = 0.15 * d.s, 0.21 * d.s
        d.dessin.add(
            Line(
                px + r0 * math.cos(a),
                py + r0 * math.sin(a),
                px + r1 * math.cos(a),
                py + r1 * math.sin(a),
                strokeColor=colors.HexColor(charte.SOLEIL),
                strokeWidth=0.9,
                strokeLineCap=1,
            )
        )
    d.bulle(x, y, 0.84, 0.115, charte.SOLEIL)


def dessin_tuile(t: Tuile, largeur: float = 170.0, hauteur: float = 170.0) -> Drawing:
    d = _Isometrie(largeur, hauteur, largeur / 2.55)
    halo = colors.HexColor(_melange(charte.SOLEIL, charte.ENCART, 0.72))
    d.dessin.add(
        Circle(largeur / 2, hauteur * 0.5, largeur * 0.44, fillColor=halo, strokeColor=None)
    )
    d.dessin.add(
        Circle(
            largeur / 2,
            hauteur * 0.5,
            largeur * 0.34,
            fillColor=colors.HexColor(_melange(charte.SOLEIL, charte.ENCART, 0.88)),
            strokeColor=None,
        )
    )
    _socle(d, teinte_sol(t))
    if t.zone_rte:
        anneau = _hexagone(0.005)
        for k in range(6):
            d.trait(anneau[k], anneau[(k + 1) % 6], charte.ENCRE, 1.2)
    if t.altitude_m >= 900 or t.altitude_max_m >= 2000:
        _montagnes(d, t)
    n = len(t.pieces)
    if not n and t.altitude_m <= 1700:
        _arbres(d, t)
    # Les pièces se partagent la tuile côte à côte, de l'arrière vers l'avant
    e = 1.35 if n == 1 else 2.0 / (1.0 + n / 2)
    rayon = 0.0 if n == 1 else 0.5
    places = []
    for k, piece in enumerate(t.pieces):
        a = k * 2 * math.pi / max(n, 1) + 0.8
        places.append((rayon * math.cos(a), rayon * math.sin(a), piece))
    for x, y, piece in sorted(places, key=lambda p: p[0] + p[1]):
        MAQUETTES.get(piece.nature, _autre)(d, x, y, e, piece)
    _epingle(d)
    return d.dessin
