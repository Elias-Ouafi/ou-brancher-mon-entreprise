"""Rapport d'implantation par adresse : Markdown, PDF ou JSON.

Le contenu est celui de `diagnose` en terminal, bloc pour bloc et limite pour
limite : les trois formats partent des mêmes `sections` de
`ingestion.diagnostic`. Le rapport y ajoute trois graphiques de mise en
perspective, lus dans la couche gold en lecture seule :

* la couverture de chaque région (production / consommation), la région de
  l'adresse surlignée ;
* les départements à plus forte pression industrielle, celui de l'adresse
  surligné même s'il est hors du classement affiché ;
* la courbe journalière du profil type chiffré, heures creuses en fond.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import duckdb

from ingestion.config import Settings, get_settings
from ingestion.diagnostic import PROFIL_DEFAUT, Diagnostic, diagnostic_en_dict, entete, sections

logger = logging.getLogger(__name__)

Format = Literal["md", "pdf", "json"]
FORMATS: tuple[Format, ...] = ("md", "pdf", "json")

#: Nombre de départements affichés dans le graphique de pression industrielle.
NB_DEPARTEMENTS_AFFICHES = 15

SOURCES = [
    "Géocodage : Base Adresse Nationale (api-adresse.data.gouv.fr).",
    "Pression industrielle : ODRÉ, consommation-annuelle-par-iris (RTE, GRTgaz, Teréga).",
    "Tension régionale : ODRÉ, equilibre-regional-mensuel-prod-conso-brute et "
    "contraintes-regionales (RTE).",
    "Prix : barèmes du tarif bleu non résidentiel, délibérations de la CRE n° 2026-06 et "
    "n° 2026-147. Carbone : éCO2mix national (RTE).",
]


@dataclass(frozen=True, slots=True)
class PlageProfil:
    """Une plage du profil type : puissance appelée sur `[heure_debut, heure_fin)`."""

    type_jour: str
    heure_debut: int
    heure_fin: int
    puissance_kw: float
    puissance_flexible_kw: float


@dataclass(frozen=True, slots=True)
class Comparaisons:
    """Séries nationales qui situent l'adresse parmi les autres territoires."""

    #: (région, taux de couverture), de la plus déficitaire à la plus excédentaire.
    regions: list[tuple[str, float]]
    #: (code, nom, GWh/an), par rang national de pression industrielle.
    departements: list[tuple[str, str, float]]
    profil: list[PlageProfil]


def lire_comparaisons(connection: duckdb.DuckDBPyConnection) -> Comparaisons:
    regions = connection.execute(
        """
        select region, taux_couverture
        from gold.fct_tension_reseau_region
        where taux_couverture is not null
        order by taux_couverture
        """
    ).fetchall()
    departements = connection.execute(
        """
        select code_departement, departement, consommation_electricite_mwh / 1000
        from gold.fct_pression_industrielle_departement
        where rang_national is not null
        order by rang_national
        """
    ).fetchall()
    profil = connection.execute(
        """
        select type_jour, heure_debut, heure_fin, puissance_kw, puissance_flexible_kw
        from gold.profils_consommation
        where profil = $profil
        order by type_jour, heure_debut
        """,
        {"profil": PROFIL_DEFAUT},
    ).fetchall()
    return Comparaisons(
        regions=[(r, float(t)) for r, t in regions],
        departements=[(c, n, float(g)) for c, n, g in departements],
        profil=[PlageProfil(*ligne) for ligne in profil],
    )


def nom_fichier(diagnostic: Diagnostic, format: Format) -> str:
    """`implantation_69123_20-rue-de-la-republique-69002-lyon.pdf` : stable et lisible."""
    ascii_ = (
        unicodedata.normalize("NFKD", diagnostic.territoire.adresse)
        .encode("ascii", "ignore")
        .decode()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:60].rstrip("-")
    return f"implantation_{diagnostic.code_insee_commune}_{slug}.{format}"


def generer_rapport_implantation(
    diagnostic: Diagnostic,
    *,
    format: Format,
    settings: Settings | None = None,
    output: Path | None = None,
    graphiques: bool = True,
    now: datetime | None = None,
) -> Path:
    """Écrit le rapport d'une adresse déjà diagnostiquée et renvoie son chemin.

    `graphiques` ne concerne que le Markdown : le PDF embarque toujours les
    siens, le JSON n'en a pas.
    """
    if format not in FORMATS:
        raise ValueError(f"format inconnu : {format!r} (attendu : {', '.join(FORMATS)})")
    settings = settings or get_settings()
    chemin = output or settings.reports_dir / nom_fichier(diagnostic, format)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    genere_le = now or datetime.now()

    if format == "json":
        chemin.write_text(en_json(diagnostic), encoding="utf-8")
    else:
        with duckdb.connect(str(settings.duckdb_path), read_only=True) as connection:
            comparaisons = lire_comparaisons(connection)
        if format == "md":
            rendre_markdown(diagnostic, comparaisons, chemin, graphiques=graphiques, now=genere_le)
        else:
            # Import paresseux : reportlab n'est utile qu'au PDF.
            from reporting import implantation_pdf

            implantation_pdf.rendre(diagnostic, comparaisons, chemin, now=genere_le)
    logger.info(
        "rapport d'implantation genere format=%s commune=%s path=%s",
        format,
        diagnostic.code_insee_commune,
        chemin,
    )
    return chemin


def en_json(diagnostic: Diagnostic) -> str:
    return json.dumps(diagnostic_en_dict(diagnostic), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def texte_compact(contenu: str) -> str:
    """Retire l'alignement en colonnes du terminal (« Commune     : »)."""
    return " ".join(contenu.split())


#: Graphique placé après chaque section, par numéro de section (1 à 4).
GRAPHIQUES = {
    1: ("pression_departements", "Pression industrielle des départements"),
    2: ("couverture_regions", "Couverture de la consommation par la production, par région"),
    4: ("profil_journalier", "Courbe journalière du profil type"),
}


def rendre_markdown(
    diagnostic: Diagnostic,
    comparaisons: Comparaisons,
    chemin: Path,
    *,
    graphiques: bool = True,
    now: datetime,
) -> Path:
    """Écrit le rapport Markdown, graphiques en PNG dans `<nom>_graphiques/`."""
    dossier = chemin.with_name(f"{chemin.stem}_graphiques")
    blocs = [
        f"# Diagnostic d'implantation — {diagnostic.territoire.adresse}",
        "",
        *[f"*{texte_compact(ligne)}*  " for ligne in entete(diagnostic)],
        "",
        f"Établi le {now:%d/%m/%Y} à partir des données ouvertes de RTE et d'ODRÉ."
        " Chaque indicateur est suivi de sa limite méthodologique.",
        "",
    ]
    for numero, section in enumerate(sections(diagnostic), start=1):
        blocs += [f"## {section.titre}", ""]
        for nature, contenu in section.elements:
            if nature == "limite":
                blocs += ["", f"> **Limite.** {contenu}", ""]
            else:
                blocs += [f"- {texte_compact(contenu)}"]
        blocs.append("")
        if graphiques and numero in GRAPHIQUES:
            nom, legende = GRAPHIQUES[numero]
            # Import paresseux : sans graphiques, matplotlib n'est pas nécessaire.
            from reporting import implantation_charts

            png = getattr(implantation_charts, nom)(diagnostic, comparaisons)
            if png is not None:
                dossier.mkdir(parents=True, exist_ok=True)
                (dossier / f"{nom}.png").write_bytes(png)
                blocs += [f"![{legende}]({dossier.name}/{nom}.png)", ""]
    blocs += ["## Sources", "", *[f"- {source}" for source in SOURCES], ""]
    # Les limites ajoutent leurs propres lignes vides : une seule suffit entre deux blocs.
    chemin.write_text(re.sub(r"\n{3,}", "\n\n", "\n".join(blocs)), encoding="utf-8")
    return chemin
