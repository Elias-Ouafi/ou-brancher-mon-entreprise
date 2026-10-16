"""Mise en page PDF du rapport d'implantation (reportlab).

Mêmes styles que le rapport mensuel. Les polices standard du PDF ne couvrent
que le jeu Windows-1252 : les flèches et le « ≤ » du texte terminal sont
remplacés, et le « 2 » de CO2 passe par la balise `<sub>`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from ingestion.diagnostic import Diagnostic, entete, essentiel, sections
from reporting import implantation_charts
from reporting.implantation import GRAPHIQUES, SOURCES, Comparaisons, texte_compact
from reporting.pdf import ALERTE, STYLES, _encadre, _image, _kpis, _pied_de_page

#: Caractères hors Windows-1252, et leur équivalent lisible en Helvetica.
SUBSTITUTIONS = {"→": "—", "↳": "", "≤": "&lt;=", "≥": "&gt;=", "₂": "<sub>2</sub>"}


def _pdf(texte: str) -> str:
    """Texte prêt pour un `Paragraph` : échappé, puis débarrassé des glyphes absents."""
    texte = escape(texte_compact(texte))
    for caractere, remplacement in SUBSTITUTIONS.items():
        texte = texte.replace(caractere, remplacement)
    return texte


def _nombre(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def _synthese(d: Diagnostic) -> list:
    """Bandeau de quatre indicateurs, ceux qu'on cherche en premier."""
    cartes = []
    if d.rang_pression_departement:
        cartes.append(
            (
                f"{d.rang_pression_departement}e / {d.nb_departements_classes}",
                f"pression industrielle du département ({d.annee_pression})",
            )
        )
    else:
        cartes.append(("aucun site", "raccordé au transport dans le département"))
    if d.taux_couverture_region is not None:
        cartes.append(
            (
                f"{_nombre(d.taux_couverture_region * 100)} %",
                f"couverture régionale — rang de dépendance {d.rang_dependance_region}"
                f" sur {d.nb_regions_classees}",
            )
        )
    else:
        cartes.append(("n.d.", "équilibre régional non publié"))
    if d.seuil is not None:
        cartes.append((d.seuil.domaine_tension, f"raccordement indicatif, {d.seuil.gestionnaire}"))
    else:
        cartes.append(("n.d.", "domaine de tension : préciser --puissance"))
    if d.prix is not None:
        cartes.append(
            (
                f"{_nombre(d.prix.prix_reference_eur_kwh, 4)} €/kWh",
                f"prix HTVA des 12 derniers mois, tranche {d.prix.code_tranche},"
                " identique sur tout le territoire",
            )
        )
    elif d.cout is not None:
        cartes.append(
            (
                f"{_nombre(d.cout.prix_moyen_htva_eur_kwh, 4)} €/kWh",
                "coût moyen HTVA du profil type, identique sur tout le territoire",
            )
        )
    return [_kpis(cartes, par_ligne=2), Spacer(1, 8)]


def rendre(
    diagnostic: Diagnostic, comparaisons: Comparaisons, chemin: Path, *, now: datetime
) -> Path:
    """Écrit le rapport PDF et renvoie son chemin."""
    titre = "Diagnostic d'implantation électrique"
    document = SimpleDocTemplate(
        str(chemin),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"{titre} — {diagnostic.territoire.adresse}",
        subject="Contexte électrique, raccordement et coût d'un profil type",
        author="Où brancher mon entreprise",
    )
    blocs: list = [
        Paragraph(titre, STYLES["titre"]),
        Paragraph(_pdf(diagnostic.territoire.adresse), STYLES["sous_titre"]),
    ]
    blocs += [Paragraph(_pdf(ligne), STYLES["texte"]) for ligne in entete(diagnostic)]
    blocs.append(
        Paragraph(
            f"Établi le {now:%d/%m/%Y} à partir de données publiques ouvertes (RTE, ODRÉ, Eurostat,"
            " Géorisques). Chaque"
            " indicateur est suivi de sa limite méthodologique.",
            STYLES["note"],
        )
    )
    blocs.append(Spacer(1, 6))
    resume = essentiel(diagnostic)
    blocs.append(Paragraph(_pdf(resume.titre), STYLES["h1"]))
    blocs += [Paragraph(f"<b>{_pdf(c)}</b>", STYLES["texte"]) for _, c in resume.elements]
    blocs.append(Spacer(1, 6))
    blocs += _synthese(diagnostic)

    for numero, section in enumerate(sections(diagnostic), start=1):
        contenu: list = [Paragraph(_pdf(section.titre), STYLES["h1"])]
        for nature, element in section.elements:
            if nature == "limite":
                contenu.append(_encadre(f"<b>Limite.</b> {_pdf(element)}", ALERTE))
                contenu.append(Spacer(1, 4))
            else:
                contenu.append(Paragraph(_pdf(element), STYLES["texte"]))
        # Le titre ne reste jamais seul en bas de page.
        blocs.append(KeepTogether(contenu[:2]))
        blocs += contenu[2:]
        if numero in GRAPHIQUES:
            png = getattr(implantation_charts, GRAPHIQUES[numero][0])(diagnostic, comparaisons)
            if png is not None:
                blocs += [Spacer(1, 4), _image(png), Spacer(1, 4)]

    blocs.append(Paragraph("Sources", STYLES["h1"]))
    blocs += [Paragraph(_pdf(source), STYLES["note"]) for source in SOURCES]
    pied = _pied_de_page(f"{titre} — {diagnostic.territoire.commune}")
    document.build(blocs, onFirstPage=pied, onLaterPages=pied)
    return chemin
