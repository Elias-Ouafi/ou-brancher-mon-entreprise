from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Circle, Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ingestion import comparaison
from ingestion.config import Settings
from ingestion.diagnostic import (
    ACTIVITES,
    ETATS_CRITERE,
    LIBELLES_ACCISE,
    Critere,
    Diagnostic,
    _distance,
    _euros_arrondis,
    entete,
    phrase_verdict,
    verdict,
)
from reporting import charte, implantation_charts, visuels
from reporting.carte import _nombre, resume_adresse
from reporting.implantation import Comparaisons, pied_de_page, texte_compact
from reporting.pdf import (
    ACCENT,
    ALERTE,
    DISCRET,
    ENCRE,
    FILET,
    FOND,
    LARGEUR,
    STYLES,
    TEXTE_GRAS,
    TITRE_GRAS,
    TITRES,
    _image,
    _pied_de_page,
)
from reporting.tuile import Tuile, dessin_tuile, legende, lire_tuile

# Un rapport qui se regarde : le verdict, puis un dessin et trois phrases au plus par critère.
# Les limites de chaque indicateur restent dans les sorties texte, Markdown et JSON
TEINTES_ETAT = {
    "ok": ACCENT,
    "attention": ALERTE,
    "info": colors.HexColor(charte.SOLEIL),
    "inconnu": DISCRET,
}
LARGEUR_TUILE = 58 * mm
LARGEUR_VISUEL = LARGEUR - 24
NOTE_TUILE = (
    "Tuile de la carte, large de {km} km : illustration de ce que porte la case, pas un plan"
    " du site."
)
# La limite qui pèse sur la décision et ce qu'on peut faire pour la lever, en une phrase
CONSEILS = {
    "Raccordement": "La capacité disponible n'est pas publique : demander tôt une étude au"
    " gestionnaire, elle seule fait foi.",
    "Risques": "Un plan couvre une partie de la commune, pas forcément l'adresse : son zonage"
    " se lit sur georisques.gouv.fr.",
    "Carbone": "C'est la moyenne annuelle du réseau, pas celle d'un contrat : une offre à"
    " garanties d'origine se juge à part.",
    "Facture": "C'est le prix moyen national de la tranche : un contrat peut s'en écarter"
    " nettement, comparer plusieurs offres.",
    "Foncier": "Distances à vol d'oiseau et inventaires déclaratifs : confirmer disponibilité"
    " et constructibilité auprès de la collectivité.",
}
NB_PHRASES_DETAIL = 2
NB_PLANS_DESSINES = 4
# Déjà dessinés ou dits plus haut dans le rapport
REPERES_ECARTES = {
    "Territoire",
    "Géocodage",
    "Zone prioritaire RTE",
    "Site clé en main (France 2030)",
    "Électricité décarbonée",
    "Locaux d'activité (vente DVF)",
    "Friches (Cartofriches)",
    "Brancher ici ?",
}
SOURCES = (
    "Sources : RTE et ODRÉ, Enedis, Eurostat, ADEME, CRE, Géorisques, DGFiP (DVF), Cerema, IGN,"
    " Base Adresse Nationale."
)
STYLE_CRITERE = ParagraphStyle(
    "critere", parent=STYLES["texte"], fontName=TITRE_GRAS, fontSize=12.5, textColor=TITRES
)
STYLE_VALEUR = ParagraphStyle(
    "valeur", parent=STYLES["texte"], fontName=TEXTE_GRAS, fontSize=10.5, alignment=2
)
STYLE_COURT = ParagraphStyle("court", parent=STYLES["texte"], spaceAfter=0, textColor=ENCRE)


def _pdf(texte: str) -> str:
    return escape(texte_compact(texte))


def _pastille(etat: str) -> Drawing:
    dessin = Drawing(7, 7)
    dessin.add(Circle(3.5, 3.5, 2.8, fillColor=TEINTES_ETAT.get(etat, DISCRET), strokeColor=None))
    return dessin


# Les premières phrases du détail, puis la limite et son conseil : trois phrases au plus
def _trois_phrases(critere: Critere) -> str:
    phrases = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9])", critere.detail.strip())
    return " ".join([*phrases[:NB_PHRASES_DETAIL], CONSEILS[critere.nom]])


# Même lecture que l'encart de la carte : la tuile à gauche, le résumé à droite
def _carte_adresse(diagnostic: Diagnostic, tuile: Tuile | None) -> list:
    largeur_info = LARGEUR - (LARGEUR_TUILE if tuile else 0)
    lignes = [
        [
            _pastille(ligne["etat"]),
            Paragraph(_pdf(ligne["nom"]), STYLES["cellule_discrete"]),
            Paragraph(f"<b>{_pdf(ligne['valeur'])}</b>", STYLES["cellule_droite"]),
        ]
        for ligne in resume_adresse(diagnostic)
    ]
    tableau = Table(
        lignes, colWidths=[12, largeur_info * 0.34, largeur_info - 12 - largeur_info * 0.34 - 20]
    )
    tableau.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.35, FILET),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    info = [
        Paragraph("Brancher ici ?", STYLES["h1_serre"]),
        Paragraph(_pdf(phrase_verdict(verdict(diagnostic))), STYLES["texte"]),
        tableau,
    ]
    if tuile is None:
        cellules, largeurs = [[info]], [LARGEUR]
    else:
        gauche = [dessin_tuile(tuile, LARGEUR_TUILE - 8, LARGEUR_TUILE - 8)]
        gauche += [Paragraph(ligne, STYLES["cellule_note"]) for ligne in legende(tuile)]
        cellules, largeurs = [[gauche, info]], [LARGEUR_TUILE, largeur_info]
    carte = Table(cellules, colWidths=largeurs, hAlign="LEFT", cornerRadii=[8, 8, 8, 8])
    carte.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), FOND),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    blocs: list = [carte]
    if tuile is not None:
        km = _nombre(tuile.rayon_km * 2, 1 if tuile.rayon_km < 5 else 0)
        blocs += [Spacer(1, 3), Paragraph(NOTE_TUILE.format(km=km), STYLES["note"])]
    return [*blocs, Spacer(1, 6)]


def _projet(d: Diagnostic) -> str:
    morceaux = []
    if d.activite:
        morceaux.append(ACTIVITES[d.activite].libelle)
    if d.seuil is not None:
        morceaux.append(
            f"{_nombre(d.seuil.puissance_mw, 0 if d.seuil.puissance_mw >= 1 else 3)} MW"
        )
    if d.conso_mwh is not None:
        morceaux.append(f"{_nombre(d.conso_mwh)} MWh par an")
    return "Projet : " + (" · ".join(morceaux) or "non précisé (puissance et consommation)")


def _visuel_raccordement(d: Diagnostic, comparaisons: Comparaisons) -> list:
    if not comparaisons.seuils:
        return []
    puissance = d.seuil.puissance_mw if d.seuil else None
    return [visuels.echelle_raccordement(comparaisons.seuils, puissance, LARGEUR_VISUEL)]


def _visuel_risques(d: Diagnostic, _: Comparaisons) -> list:
    plans = (d.plans_prevention or [])[:NB_PLANS_DESSINES]
    if not plans:
        return []
    lignes = []
    for plan in plans:
        date_etat = plan.date_approbation if plan.etat == "approuvé" else plan.date_prescription
        etat = f"{plan.etat} en {date_etat.year}" if date_etat else plan.etat
        lignes.append(
            [
                Paragraph(f"<b>{_pdf(plan.libelle_plan)}</b>", STYLES["cellule"]),
                Paragraph(_pdf((plan.risques or plan.type_plan).lower()), STYLES["cellule"]),
                Paragraph(_pdf(etat), STYLES["cellule_droite"]),
            ]
        )
    tableau = Table(lignes, colWidths=[LARGEUR_VISUEL * 0.46, LARGEUR_VISUEL * 0.3, None])
    tableau.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, ALERTE),
                ("LINEBELOW", (0, 0), (-1, -2), 0.35, FILET),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [tableau]


def _visuel_carbone(d: Diagnostic, _: Comparaisons) -> list:
    c = d.carbone
    if c is None:
        return []
    seuil = c.seuil_decarbone_g_co2e_kwh
    dessins = [visuels.jauge_carbone(c.intensite_acv_g_co2e_kwh, seuil, seuil * 2, LARGEUR_VISUEL)]
    if c.profil_horaire:
        dessins.append(visuels.profil_carbone(c.profil_horaire, LARGEUR_VISUEL))
    return dessins


def _visuel_facture(d: Diagnostic, comparaisons: Comparaisons) -> list:
    dessins: list = []
    a, p = d.acheminement, d.prix
    if p is not None and p.conso_mwh is not None and a is not None:
        facture = p.facture_eur(p.prix_reference_eur_kwh)
        parts = [
            ("Acheminement par le réseau (TURPE 7)", a.total_eur, ENCRE),
            ("Énergie, taxes et contributions", facture - a.total_eur, TEINTES_ETAT["info"]),
        ]
        dessins.append(visuels.barre_parts(parts, LARGEUR_VISUEL, _euros_arrondis))
    elif a is not None:
        parts = [
            ("Acheminement : puissance souscrite", a.puissance_eur, ENCRE),
            ("Acheminement : énergie acheminée", a.energie_eur, TEINTES_ETAT["info"]),
            ("Acheminement : gestion et comptage", a.gestion_comptage_eur, DISCRET),
        ]
        dessins.append(visuels.barre_parts(parts, LARGEUR_VISUEL, _euros_arrondis))
    if (r := d.accise_reduite) is not None:
        lignes = [
            ("Accise au tarif normal", r.accise_normale_eur, DISCRET),
            (f"Accise réduite ({LIBELLES_ACCISE[r.categorie]})", r.accise_reduite_eur, ACCENT),
        ]
        dessins += [
            Spacer(1, 4),
            visuels.barres(
                [(nom, eur, _euros_arrondis(eur), teinte) for nom, eur, teinte in lignes],
                LARGEUR_VISUEL,
                marge=200,
            ),
        ]
    if (png := implantation_charts.prix_tranche(d, comparaisons)) is not None:
        dessins += [Spacer(1, 4), _image(png, LARGEUR_VISUEL)]
    return dessins


def _visuel_foncier(d: Diagnostic, _: Comparaisons) -> list:
    distances = []
    if d.site_cle_en_main is not None:
        site = d.site_cle_en_main
        distances.append((f"Site clé en main ({site.commune})", site.distance_km, ACCENT))
    if d.zone_rte is not None and not d.est_zni:
        distances.append(
            (f"Zone prioritaire RTE ({d.zone_rte.zone})", d.zone_rte.distance_km, ENCRE)
        )
    if d.friches:
        friche = d.friches[0]
        nom = f"Friche de {_nombre(friche.surface_m2 / 10_000, 1)} ha la plus proche"
        distances.append((nom, friche.distance_km, TEINTES_ETAT["info"]))
    dessins: list = []
    if distances:
        dessins.append(
            visuels.barres(
                [(nom, km, _distance(km)[2:], teinte) for nom, km, teinte in distances],
                LARGEUR_VISUEL,
                marge=230,
            )
        )
    if (locaux := d.prix_locaux) is not None:
        lieu = d.territoire.commune if locaux.niveau == "commune" else d.departement
        dessins += [
            Spacer(1, 4),
            Paragraph(
                f"Prix de vente des locaux d'activité à {_pdf(lieu)} ({locaux.nb_ventes} ventes,"
                " DVF)",
                STYLES["cellule_discrete"],
            ),
            visuels.fourchette(
                locaux.prix_m2_q1, locaux.prix_m2_median, locaux.prix_m2_q3, LARGEUR_VISUEL, "€/m²"
            ),
        ]
    return dessins


VISUELS = {
    "Raccordement": _visuel_raccordement,
    "Risques": _visuel_risques,
    "Carbone": _visuel_carbone,
    "Facture": _visuel_facture,
    "Foncier": _visuel_foncier,
}


def _fiche(d: Diagnostic, critere: Critere, comparaisons: Comparaisons) -> Table:
    teinte = TEINTES_ETAT.get(critere.etat, DISCRET)
    couleur = teinte.hexval().replace("0x", "#")
    etat = f'<font color="{couleur}"><b>{ETATS_CRITERE[critere.etat]}</b></font>'
    tete = Table(
        [
            [
                Paragraph(
                    f"{_pdf(critere.nom)} &nbsp; <font size=8.5>{etat}</font>", STYLE_CRITERE
                ),
                Paragraph(_pdf(critere.valeur), STYLE_VALEUR),
            ]
        ],
        colWidths=[LARGEUR_VISUEL * 0.42, LARGEUR_VISUEL * 0.58],
    )
    tete.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    contenu = [tete, Spacer(1, 3), *VISUELS[critere.nom](d, comparaisons), Spacer(1, 4)]
    contenu.append(Paragraph(_pdf(_trois_phrases(critere)), STYLE_COURT))
    fiche = Table([[contenu]], colWidths=[LARGEUR], hAlign="LEFT")
    fiche.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, -1), 3, teinte),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return fiche


def _reperes(d: Diagnostic) -> list:
    lignes = [
        [
            Paragraph(_pdf(ligne.indicateur), STYLES["cellule_discrete"]),
            Paragraph(f"<b>{_pdf(ligne.valeurs[0])}</b>", STYLES["cellule"]),
        ]
        for ligne in comparaison.lignes([d])
        if ligne.indicateur not in REPERES_ECARTES and ligne.valeurs[0] != "—"
    ]
    if not lignes:
        return []
    tableau = Table(lignes, colWidths=[LARGEUR * 0.32, LARGEUR * 0.68], hAlign="LEFT")
    tableau.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -2), 0.35, FILET),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [KeepTogether([Paragraph("Autres repères du site", STYLES["h1"]), tableau])]


def _territoire(d: Diagnostic, comparaisons: Comparaisons) -> list:
    images = [
        _image(png, LARGEUR * 0.7)
        for graphique in (
            implantation_charts.pression_departements,
            implantation_charts.couverture_regions,
        )
        if (png := graphique(d, comparaisons)) is not None
    ]
    if not images:
        return []
    titre = "Le territoire face aux autres (en sombre : celui de l'adresse)"
    return [KeepTogether([Paragraph(titre, STYLES["h1"]), images[0]]), *images[1:]]


def _ornement(canvas, _document) -> None:
    canvas.saveState()
    haut, droite = A4[1] - 8 * mm, A4[0] - 18 * mm
    canvas.setStrokeColor(colors.HexColor(charte.FEUILLE))
    canvas.setFillColor(colors.HexColor(charte.FEUILLE))
    canvas.setLineWidth(0.8)
    canvas.line(18 * mm, haut, droite - 62, haut)
    canvas.bezier(
        droite - 62, haut, droite - 44, haut, droite - 34, haut + 7, droite - 14, haut + 9
    )
    for x, y, dx, dy in ((-44, 2, 9, 7), (-34, 5, 10, 4), (-24, 7, 7, 8)):
        chemin = canvas.beginPath()
        chemin.moveTo(droite + x, haut + y)
        chemin.curveTo(
            droite + x + dx * 0.2, haut + y + dy, droite + x + dx, haut + y + dy,
            droite + x + dx, haut + y + dy,
        )  # fmt: skip
        chemin.curveTo(
            droite + x + dx, haut + y + dy * 0.3, droite + x + dx * 0.6, haut + y,
            droite + x, haut + y,
        )  # fmt: skip
        canvas.drawPath(chemin, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor(charte.SOLEIL))
    canvas.setStrokeColor(colors.HexColor(charte.SOLEIL))
    canvas.circle(droite - 4, haut + 9, 3.6, stroke=0, fill=1)
    canvas.restoreState()


def rendre(
    diagnostic: Diagnostic,
    comparaisons: Comparaisons,
    chemin: Path,
    *,
    now: datetime,
    commande: str | None = None,
    settings: Settings | None = None,
) -> Path:
    titre = "Diagnostic d'implantation électrique"
    document = SimpleDocTemplate(
        str(chemin),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"{titre} — {diagnostic.territoire.adresse}",
        subject="Raccordement, risques, carbone, facture et foncier à l'adresse",
        author="Où brancher mon entreprise",
    )
    blocs: list = [
        Paragraph(titre, STYLES["titre"]),
        Paragraph(_pdf(diagnostic.territoire.adresse), STYLES["sous_titre"]),
    ]
    tuile = lire_tuile(diagnostic.territoire, settings) if settings else None
    blocs += _carte_adresse(diagnostic, tuile)
    lieu = " · ".join(entete(diagnostic))
    blocs.append(Paragraph(_pdf(f"{_projet(diagnostic)} · {lieu}"), STYLES["note"]))
    blocs.append(Paragraph("Les cinq critères", STYLES["h1"]))
    for critere in verdict(diagnostic):
        blocs += [KeepTogether([_fiche(diagnostic, critere, comparaisons)]), Spacer(1, 8)]
    blocs += _reperes(diagnostic)
    blocs += _territoire(diagnostic, comparaisons)
    fin = [
        Spacer(1, 8),
        Paragraph(
            f"Établi le {now:%d/%m/%Y} à partir de données publiques. Ce rapport ne garde que la"
            " limite qui pèse sur chacun des cinq critères ; toutes les limites méthodologiques"
            " sont dans le rapport Markdown (--format md). " + SOURCES,
            STYLES["note"],
        ),
    ]
    fin += [
        Paragraph(_pdf(ligne), STYLES["note"]) for ligne in pied_de_page(diagnostic, commande)[1:]
    ]
    blocs.append(KeepTogether(fin))
    pied = _pied_de_page(f"{titre} — {diagnostic.territoire.commune}")

    def premiere_page(canvas, doc) -> None:
        _ornement(canvas, doc)
        pied(canvas, doc)

    document.build(blocs, onFirstPage=premiere_page, onLaterPages=pied)
    return chemin
