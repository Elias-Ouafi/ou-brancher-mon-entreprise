from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from ingestion.diagnostic import Diagnostic
from reporting.charts import ENCRE, GRILLE, _png
from reporting.implantation import NB_DEPARTEMENTS_AFFICHES, Comparaisons

# L'adresse diagnostiquée ressort en couleur, les autres territoires en gris
ICI = "#2F6DB5"
AUTRES = "#C9D3DD"
HC = "#E8EEF6"
FLEXIBLE = "#E08A00"


# Barres horizontales, la première valeur en haut
def _barres(ax: plt.Axes, libelles: list[str], valeurs: list[float], ici: list[bool]) -> None:
    positions = range(len(valeurs))[::-1]
    ax.barh(list(positions), valeurs, color=[ICI if i else AUTRES for i in ici], height=0.7)
    ax.set_yticks(list(positions), libelles)
    for etiquette, est_ici in zip(ax.get_yticklabels(), ici, strict=True):
        if est_ici:
            etiquette.set_fontweight("bold")
            etiquette.set_color(ICI)
    ax.grid(axis="y", visible=False)


# Taux de couverture de chaque région, 100 % = production égale à la consommation
def couverture_regions(diagnostic: Diagnostic, comparaisons: Comparaisons) -> bytes | None:
    if not comparaisons.regions:
        return None
    regions = comparaisons.regions[::-1]
    fig, ax = plt.subplots(figsize=(7.2, 0.2 * len(regions) + 0.7))
    _barres(
        ax,
        [r for r, _ in regions],
        [t * 100 for _, t in regions],
        [r == diagnostic.region for r, _ in regions],
    )
    ax.axvline(100, color=ENCRE, linestyle="--", linewidth=0.8)
    ax.annotate(
        "production = consommation",
        xy=(100, 1.0),
        xycoords=("data", "axes fraction"),
        xytext=(4, -2),
        textcoords="offset points",
        va="top",
        fontsize=7,
        color=ENCRE,
    )
    ax.set_xlabel("couverture de la consommation par la production régionale (%)")
    return _png(fig)


# Départements les plus consommateurs ; celui de l'adresse ajouté s'il est plus loin
def pression_departements(diagnostic: Diagnostic, comparaisons: Comparaisons) -> bytes | None:
    if not comparaisons.departements:
        return None
    affiches = comparaisons.departements[:NB_DEPARTEMENTS_AFFICHES]
    codes = [code for code, _, _ in affiches]
    libelles = [f"{nom} ({code})" for code, nom, _ in affiches]
    valeurs = [gwh for _, _, gwh in affiches]
    ici = [code == diagnostic.code_departement for code in codes]
    if diagnostic.code_departement not in codes:
        ici.append(True)
        nom = f"{diagnostic.departement} ({diagnostic.code_departement})"
        if diagnostic.rang_pression_departement:
            libelles.append(f"{diagnostic.rang_pression_departement}e : {nom}")
            valeurs.append(diagnostic.conso_industrielle_departement_gwh)
        else:
            libelles.append(f"{nom} : aucun site")
            valeurs.append(0.0)

    fig, ax = plt.subplots(figsize=(7.2, 0.2 * len(valeurs) + 0.7))
    _barres(ax, libelles, valeurs, ici)
    ax.set_xlabel(
        f"consommation des sites raccordés au transport, GWh/an ({diagnostic.annee_pression})"
    )
    return _png(fig)


# Puissance appelée heure par heure, jour ouvré et week-end, part flexible en couleur
def profil_journalier(diagnostic: Diagnostic, comparaisons: Comparaisons) -> bytes | None:
    if not comparaisons.profil or diagnostic.cout is None:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.4), sharey=True)
    for ax, (type_jour, titre) in zip(
        axes, (("ouvre", "jour ouvré"), ("week_end", "week-end")), strict=True
    ):
        plages = [p for p in comparaisons.profil if p.type_jour == type_jour]
        ax.axvspan(0, 6, color=HC, linewidth=0)
        ax.axvspan(22, 24, color=HC, linewidth=0)
        for p in plages:
            largeur = p.heure_fin - p.heure_debut
            socle = p.puissance_kw - p.puissance_flexible_kw
            ax.bar(p.heure_debut, socle, width=largeur, align="edge", color=AUTRES,
                   edgecolor="white", linewidth=0.5)  # fmt: skip
            if p.puissance_flexible_kw:
                ax.bar(p.heure_debut, p.puissance_flexible_kw, bottom=socle, width=largeur,
                       align="edge", color=FLEXIBLE, edgecolor="white", linewidth=0.5)  # fmt: skip
        ax.set_title(titre, fontsize=9, color=ENCRE)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 6), [f"{h} h" for h in range(0, 25, 6)])
        ax.grid(axis="x", visible=False)
        ax.spines["bottom"].set_color(GRILLE)
    axes[0].set_ylabel("puissance appelée (kW)")
    fig.legend(
        handles=[
            Patch(color=AUTRES, label="consommation"),
            Patch(color=FLEXIBLE, label="part déplaçable"),
            Patch(color=HC, label="heures creuses (22 h–6 h)"),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=3,
        fontsize=7,
        frameon=False,
    )
    return _png(fig)


# Prix semestriel de la tranche depuis 2007, puis l'éventail des scénarios à 3 ans
def prix_tranche(diagnostic: Diagnostic, comparaisons: Comparaisons) -> bytes | None:
    prix = diagnostic.prix
    if prix is None or not comparaisons.historique_prix:
        return None
    dates = [d for d, _ in comparaisons.historique_prix]
    valeurs = [v for _, v in comparaisons.historique_prix]

    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    ax.plot(dates, valeurs, color=ICI, linewidth=1.4, marker="o", markersize=2.5,
            label="prix constaté (Eurostat, HTVA)")  # fmt: skip
    if comparaisons.scenarios_prix and prix.prix_3_ans_central_eur_kwh is not None:
        depart = dates[-1]
        horizons = [depart] + [
            depart.replace(year=depart.year + h) for h, *_ in comparaisons.scenarios_prix
        ]
        reference = prix.prix_reference_eur_kwh
        bas = [reference] + [b for _, b, _, _ in comparaisons.scenarios_prix]
        centre = [reference] + [c for _, _, c, _ in comparaisons.scenarios_prix]
        haut = [reference] + [t for _, _, _, t in comparaisons.scenarios_prix]
        ax.fill_between(horizons, bas, haut, color=FLEXIBLE, alpha=0.18, linewidth=0,
                        label="scénarios bas – haut (10e – 90e centile)")  # fmt: skip
        ax.plot(horizons, centre, color=FLEXIBLE, linestyle="--", linewidth=1.2,
                label="scénario central (médiane)")  # fmt: skip
        # Point de départ : la moyenne des deux derniers semestres, pas le dernier seul
        ax.plot([depart], [reference], color=FLEXIBLE, marker="D", markersize=4,
                linestyle="none", label="départ : moyenne des 12 derniers mois")  # fmt: skip
    ax.annotate(
        f"plus haut : {prix.prix_max_eur_kwh:.3f} €/kWh".replace(".", ","),
        xy=(dates[valeurs.index(prix.prix_max_eur_kwh)], prix.prix_max_eur_kwh),
        xytext=(6, 0),
        textcoords="offset points",
        va="center",
        fontsize=7,
        color=ENCRE,
    )
    ax.set_ylabel("€/kWh HTVA")
    ax.set_ylim(bottom=0)
    ax.set_title(
        f"Tranche {prix.code_tranche} ({prix.libelle_tranche})", fontsize=9, color=ENCRE, loc="left"
    )
    ax.legend(loc="upper left", fontsize=7, frameon=False)
    return _png(fig)
