from __future__ import annotations

import base64
from functools import cache
from pathlib import Path

# Direction « le relevé » (PLAN, refonte de la carte) : papier clair et encre bleu nuit, un seul
# accent vert pour ce qui est favorable et sourcé, orange brûlé pour « à vérifier »
PAPIER = "#F4F1EA"
ENCART = "#EBE6DA"
ENCRE = "#14202B"
DISCRET = "#5B6670"
FILET = "#D6D0C2"
MER = "#9DB0BC"
VERT = "#2E7D5B"
ORANGE = "#C2571A"
GRIS = "#C9CCC8"
# Tempo garde ses couleurs : ce sont les noms des jours, pas une note
TEMPO = {"bleu": "#2F6DB5", "blanc": "#B8BEC6", "rouge": "#D0312D"}

POLICES = Path(__file__).with_name("polices")
TITRE = "Spectral"
TEXTE = "Inter"
FICHIERS = {
    (TITRE, False): "Spectral-Regular.ttf",
    (TITRE, True): "Spectral-SemiBold.ttf",
    (TEXTE, False): "Inter-Regular.ttf",
    (TEXTE, True): "Inter-Bold.ttf",
}


def police(famille: str, gras: bool = False) -> Path:
    return POLICES / FICHIERS[(famille, gras)]


def nom_reportlab(famille: str, gras: bool = False) -> str:
    return f"{famille}-Gras" if gras else famille


# Helvetica n'a ni « ₂ » ni « ≥ » : les polices embarquées les ont, et rendent pareil partout
@cache
def enregistrer_reportlab() -> None:
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for (famille, gras), _ in FICHIERS.items():
        pdfmetrics.registerFont(TTFont(nom_reportlab(famille, gras), police(famille, gras)))
    for famille in (TITRE, TEXTE):
        normal, gras = nom_reportlab(famille), nom_reportlab(famille, True)
        # `<b>` dans un Paragraph cherche la variante grasse par ce tableau
        for est_gras, nom in ((0, normal), (1, gras)):
            addMapping(normal, est_gras, 0, nom)
            addMapping(normal, est_gras, 1, nom)


@cache
def enregistrer_matplotlib() -> None:
    from matplotlib import font_manager

    for famille, gras in FICHIERS:
        font_manager.fontManager.addfont(str(police(famille, gras)))


def css() -> str:
    faces = "\n".join(
        f'@font-face {{ font-family: "{famille}"; font-weight: {700 if gras else 400};'
        f" font-display: block; src: url(data:font/ttf;base64,"
        f'{base64.b64encode(police(famille, gras).read_bytes()).decode()}) format("truetype"); }}'
        for famille, gras in FICHIERS
    )
    variables = {
        "papier": PAPIER,
        "encart": ENCART,
        "encre": ENCRE,
        "discret": DISCRET,
        "filet": FILET,
        "mer": MER,
        "vert": VERT,
        "orange": ORANGE,
        "gris": GRIS,
        "f-titre": f'"{TITRE}", Georgia, serif',
        "f-texte": f'"{TEXTE}", system-ui, sans-serif',
    }
    racine = " ".join(f"--{nom}: {valeur};" for nom, valeur in variables.items())
    return f"{faces}\n:root {{ {racine} }}"
