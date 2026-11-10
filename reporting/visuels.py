from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors

from reporting import charte
from reporting.carte import _nombre
from reporting.pdf import ACCENT, ALERTE, DISCRET, ENCRE, FILET, FOND, TEXTE, TEXTE_GRAS

# Visuels vectoriels du PDF : ce qu'un critère dit se lit sur un dessin avant de se lire en phrase
SOLEIL = colors.HexColor(charte.SOLEIL)
ROUGE = colors.HexColor("#A8321F")
BLANC = colors.HexColor(charte.PAPIER)


def _mot(
    dessin: Drawing,
    x: float,
    y: float,
    texte: str,
    *,
    taille: float = 7.5,
    teinte: colors.Color = DISCRET,
    gras: bool = False,
    ancre: str = "start",
) -> None:
    dessin.add(
        String(
            x,
            y,
            texte,
            fontName=TEXTE_GRAS if gras else TEXTE,
            fontSize=taille,
            fillColor=teinte,
            textAnchor=ancre,
        )
    )


def _repere(dessin: Drawing, x: float, y: float, texte: str) -> None:
    dessin.add(Polygon([x - 4, y + 7, x + 4, y + 7, x, y], fillColor=ENCRE, strokeColor=None))
    _mot(dessin, x, y + 10, texte, taille=8.5, teinte=ENCRE, gras=True, ancre="middle")


def _puissance(mw: float) -> str:
    return f"{_nombre(mw * 1000)} kVA" if mw < 1 else f"{_nombre(mw)} MW"


# Les domaines de tension du seed, à largeur égale : le projet s'y place selon sa puissance.
# Vert : distribution ; doré : Enedis ou RTE ; orange : RTE et ses délais
def echelle_raccordement(
    seuils: Sequence[tuple[str, float, float | None, str]],
    puissance_mw: float | None,
    largeur: float,
) -> Drawing:
    dessin = Drawing(largeur, 62)
    pas = largeur / len(seuils)
    for k, (domaine, mini, maxi, gestionnaire) in enumerate(seuils):
        teinte = ALERTE if gestionnaire == "RTE" else SOLEIL if "RTE" in gestionnaire else ACCENT
        x = k * pas
        dessin.add(Rect(x, 22, pas - 2, 16, fillColor=teinte, strokeColor=None, rx=3, ry=3))
        _mot(dessin, x + pas / 2 - 1, 27, domaine, teinte=BLANC, gras=True, ancre="middle")
        _mot(dessin, x + pas / 2 - 1, 11, gestionnaire, ancre="middle")
        if k:
            _mot(dessin, x - 1, 1, _puissance(mini), taille=7, ancre="middle")
        if puissance_mw is not None and mini <= puissance_mw < (maxi or math.inf):
            # Dans le dernier domaine, sans borne haute, le repère reste au quart
            part = 0.25 if maxi is None else (puissance_mw - mini) / (maxi - mini)
            _repere(dessin, x + part * (pas - 2), 40, _puissance(puissance_mw))
    return dessin


# Même lecture que la jauge de la carte : le seuil au milieu, vert en dessous, orange puis rouge
def jauge_carbone(valeur: float, seuil: float, maximum: float, largeur: float) -> Drawing:
    dessin = Drawing(largeur, 46)
    maximum = max(maximum, valeur, seuil * 2)
    pas = largeur / 60
    for k in range(60):
        part = k / 59
        teinte = (
            ACCENT if part < 0.5 else colors.linearlyInterpolatedColor(ALERTE, ROUGE, 0.5, 1, part)
        )
        dessin.add(Rect(k * pas, 14, pas + 0.5, 8, fillColor=teinte, strokeColor=None))
    position = (
        valeur / seuil * 0.5
        if valeur <= seuil
        else 0.5 + (valeur - seuil) / (maximum - seuil) * 0.5
    )
    _repere(dessin, position * largeur, 24, f"{_nombre(valeur, 1)} gCO₂e/kWh")
    dessin.add(Line(largeur / 2, 10, largeur / 2, 26, strokeColor=ENCRE, strokeWidth=0.8))
    _mot(dessin, 0, 2, "0")
    _mot(dessin, largeur / 2, 2, f"seuil {_nombre(seuil)}", ancre="middle")
    _mot(dessin, largeur, 2, _nombre(maximum), ancre="end")
    return dessin


# Barre empilée : chaque part (libellé, montant, teinte) à sa proportion, légende dessous
def barre_parts(
    parts: Sequence[tuple[str, float, colors.Color]],
    largeur: float,
    montant_en_texte: Callable[[float], str],
) -> Drawing:
    dessin = Drawing(largeur, 22 + 11 * len(parts))
    total = sum(montant for _, montant, _ in parts) or 1.0
    haut = 11 * len(parts) + 6
    x = 0.0
    for rang, (libelle, montant, teinte) in enumerate(parts):
        longueur = montant / total * largeur
        dessin.add(Rect(x, haut, max(longueur - 1, 0.5), 14, fillColor=teinte, strokeColor=None))
        x += longueur
        y = haut - 12 - 11 * rang
        dessin.add(Rect(0, y, 7, 7, fillColor=teinte, strokeColor=None))
        _mot(dessin, 11, y + 0.5, libelle, teinte=ENCRE)
        _mot(
            dessin,
            largeur,
            y + 0.5,
            f"{montant_en_texte(montant)} · {_nombre(montant / total * 100)} %",
            teinte=ENCRE,
            gras=True,
            ancre="end",
        )
    return dessin


# Barres horizontales comparées (libellé, valeur, texte de la valeur, teinte) sur la même échelle
def barres(
    lignes: Sequence[tuple[str, float, str, colors.Color]], largeur: float, marge: float = 150
) -> Drawing:
    dessin = Drawing(largeur, 15 * len(lignes) + 2)
    maximum = max((valeur for _, valeur, _, _ in lignes), default=0) or 1.0
    piste = largeur - marge - 90
    for rang, (libelle, valeur, texte, teinte) in enumerate(lignes):
        y = 15 * (len(lignes) - 1 - rang) + 3
        _mot(dessin, 0, y + 1.5, libelle, teinte=ENCRE)
        dessin.add(Rect(marge, y, piste, 9, fillColor=FOND, strokeColor=None, rx=2, ry=2))
        longueur = max(valeur / maximum * piste, 2)
        dessin.add(Rect(marge, y, longueur, 9, fillColor=teinte, strokeColor=None, rx=2, ry=2))
        _mot(dessin, marge + piste + 6, y + 1.5, texte, teinte=ENCRE, gras=True)
    return dessin


# Premier quartile, médiane et troisième quartile d'un prix : la médiane seule cacherait l'écart
def fourchette(q1: float, mediane: float, q3: float, largeur: float, unite: str) -> Drawing:
    dessin = Drawing(largeur, 40)
    debut, fin = q1 * 0.6, q3 * 1.25
    x = lambda v: (v - debut) / (fin - debut) * largeur  # noqa: E731
    dessin.add(Line(0, 18, largeur, 18, strokeColor=FILET, strokeWidth=1))
    dessin.add(Rect(x(q1), 13, x(q3) - x(q1), 10, fillColor=SOLEIL, strokeColor=None, rx=3, ry=3))
    dessin.add(Circle(x(mediane), 18, 4.5, fillColor=ENCRE, strokeColor=BLANC, strokeWidth=1))
    _mot(dessin, x(mediane), 29, f"médiane {_nombre(mediane)} {unite}", taille=8.5, teinte=ENCRE,
         gras=True, ancre="middle")  # fmt: skip
    _mot(dessin, x(q1), 2, f"un quart sous {_nombre(q1)}", ancre="middle")
    _mot(dessin, x(q3), 2, f"un quart au-dessus de {_nombre(q3)}", ancre="middle")
    return dessin


# Carbone heure par heure (Corse et outre-mer) : les heures sous la moyenne en vert
def profil_carbone(profil: Sequence[tuple[int, float]], largeur: float) -> Drawing:
    dessin = Drawing(largeur, 58)
    maximum = max(g for _, g in profil)
    moyenne = sum(g for _, g in profil) / len(profil)
    pas = largeur / 24
    for heure, g in profil:
        teinte = ACCENT if g <= moyenne else ALERTE
        dessin.add(
            Rect(heure * pas + 1, 12, pas - 2, g / maximum * 38, fillColor=teinte, strokeColor=None)
        )
    for heure in (0, 6, 12, 18):
        _mot(dessin, heure * pas + 1, 2, f"{heure} h")
    plus_bas = min(profil, key=lambda p: p[1])
    _mot(dessin, largeur, 2, f"plus bas vers {plus_bas[0]} h : {_nombre(plus_bas[1])} g",
         teinte=ENCRE, gras=True, ancre="end")  # fmt: skip
    return dessin
