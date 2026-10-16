"""Comparaison de plusieurs adresses, côte à côte, sans classement.

Le tableau ne garde que les indicateurs **qui dépendent de l'adresse** : territoire,
pression industrielle, tension régionale, contraintes et postes RTE, zone
prioritaire la plus proche, carbone du réseau (continent, Corse ou outre-mer), risques à
l'adresse. Le reste — niveau de tension
selon la puissance, prix de la tranche, coût du profil type — est le même
partout (péréquation) : il est donné une fois, sous le tableau.

Pas de note ni de verdict : pondérer des critères aussi différents qu'un risque
d'inondation et une distance à un poste est un choix de l'entreprise, pas une
donnée. Chaque indicateur garde sa limite, listée une fois à la fin.
"""

from __future__ import annotations

import csv
import io
import json
import textwrap
from collections.abc import Callable
from dataclasses import dataclass

from ingestion.diagnostic import (
    Diagnostic,
    _distance,
    _gwh,
    _liste_risques,
    _nombre,
    _nombre_court,
    diagnostic_en_dict,
    sections,
)
from ingestion.georisques import Exposition

#: Bornes du nombre d'adresses comparées : au-delà de six, le tableau devient illisible.
NB_ADRESSES_MIN = 2
NB_ADRESSES_MAX = 6


@dataclass(frozen=True, slots=True)
class Ligne:
    """Un indicateur, sa valeur pour chaque adresse, et la limite qui l'accompagne."""

    indicateur: str
    valeurs: list[str]
    limite: str | None = None


def _territoire(d: Diagnostic) -> str:
    return f"{d.territoire.commune} ({d.code_insee_commune}), {d.departement}, {d.region}"


def _geocodage(d: Diagnostic) -> str:
    precision = {"housenumber": "numéro", "street": "rue"}.get(d.territoire.precision)
    if precision is None:
        return f"commune seulement ({d.territoire.precision or 'inconnue'})"
    return f"{precision}, score {_nombre(d.territoire.score, 2)}"


def _pression_commune(d: Diagnostic) -> str:
    if not d.commune_a_des_sites:
        return "aucun site raccordé au transport"
    return f"{d.nb_sites_commune} site(s), {_gwh(d.conso_industrielle_commune_gwh or 0)}/an"


def _pression_departement(d: Diagnostic) -> str:
    if not d.nb_sites_departement:
        return "aucun site raccordé au transport"
    rang = f", rang {d.rang_pression_departement}/{d.nb_departements_classes}"
    return f"{d.nb_sites_departement} site(s), {_gwh(d.conso_industrielle_departement_gwh)}/an" + (
        rang if d.rang_pression_departement else ""
    )


def _couverture(d: Diagnostic) -> str:
    if d.taux_couverture_region is None:
        return "non publiée" + (" (zone non interconnectée)" if d.est_zni else "")
    statut = {"excedentaire": "excédentaire", "deficitaire": "déficitaire"}.get(
        d.statut_equilibre or "", "?"
    )
    return (
        f"{_nombre(d.taux_couverture_region * 100)} %, {statut} ; dépendance"
        f" {d.rang_dependance_region}/{d.nb_regions_classees}"
    )


def _contraintes(d: Diagnostic) -> str:
    if d.est_zni and not d.nb_postes_rte:
        return "réseau hors RTE"
    if not d.nb_contraintes_departement:
        return "aucune"
    return (
        f"{d.nb_contraintes_departement}, dont {d.nb_contraintes_frequentes_departement or 0}"
        " plus de 75 fois par an"
    )


def _postes(d: Diagnostic) -> str:
    if not d.nb_postes_rte:
        return "aucun" + (" (réseau hors RTE)" if d.est_zni else "")
    return (
        f"{d.nb_postes_rte} (HTB1 {d.nb_postes_htb1 or 0}, HTB2 {d.nb_postes_htb2 or 0},"
        f" HTB3 {d.nb_postes_htb3 or 0})"
    )


def _zone(d: Diagnostic) -> str:
    if d.est_zni or d.zone_rte is None:
        return "aucune (réseau hors RTE)" if d.est_zni else "aucune"
    z = d.zone_rte
    condition = "dès les autorisations" if z.priorite == "P1" else "si les projets se concrétisent"
    return (
        f"{z.zone} ({z.priorite}), {_distance(z.distance_km)} ; capacité {z.annee_capacite},"
        f" {condition}"
    )


def _site(d: Diagnostic) -> str:
    if d.site_cle_en_main is None:
        return "aucun dans ce territoire"
    site = d.site_cle_en_main
    return f"{site.commune}, {_distance(site.distance_km)} ; prêt en {site.maturite_site}"


def _risques(d: Diagnostic, famille: str) -> str:
    if d.risques_erreur is not None:
        return "non évalués (Géorisques injoignable)"
    if d.risques is None:
        return "non consultés"
    if d.risques.maille == "commune":
        recenses = d.risques.selon(Exposition.INCONNU, famille)
        return f"commune seulement : {_liste_risques(recenses) or 'aucun'}"
    return _liste_risques(d.risques.selon(Exposition.EXPOSE, famille)) or "aucun recensé"


def _carbone(d: Diagnostic) -> str:
    if d.carbone is None:
        return "n.d."
    c = d.carbone
    verdict = "oui" if c.est_decarbone else "non"
    return (
        f"{verdict}, {_nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh ({c.systeme_electrique})"
    )


def _non_evalues(d: Diagnostic) -> str:
    if d.risques is None or d.risques.maille == "commune":
        return "—"
    return _liste_risques(d.risques.selon(Exposition.INCONNU)) or "aucun"


def lignes(diagnostics: list[Diagnostic]) -> list[Ligne]:
    """Les indicateurs qui dépendent de l'adresse, une valeur par diagnostic."""

    def ligne(
        indicateur: str, valeur: Callable[[Diagnostic], str], limite: str | None = None
    ) -> Ligne:
        return Ligne(indicateur, [valeur(d) for d in diagnostics], limite)

    resultat = [
        ligne("Territoire", _territoire),
        ligne("Géocodage", _geocodage),
        ligne("Industrie : commune", _pression_commune, "pression_industrielle"),
        ligne("Industrie : département", _pression_departement, "pression_industrielle"),
        ligne("Région : couverture", _couverture, "equilibre_regional"),
        ligne("Contraintes RTE (3-5 ans)", _contraintes, "contraintes_prospectives"),
        ligne("Postes RTE du département", _postes, "postes_rte"),
        ligne("Zone prioritaire RTE", _zone, "zones_raccordement_rte"),
        ligne("Site clé en main (France 2030)", _site, "sites_cles_en_main"),
        ligne("Électricité décarbonée", _carbone, "carbone_territoire"),
    ]
    if any(d.risques is not None or d.risques_erreur is not None for d in diagnostics):
        resultat += [
            ligne("Risques naturels", lambda d: _risques(d, "naturel"), "risques_site"),
            ligne("Risques technologiques", lambda d: _risques(d, "technologique"), "risques_site"),
            ligne("Non évalués à l'adresse", _non_evalues, "risques_site"),
        ]
    return resultat


def communs(diagnostics: list[Diagnostic]) -> list[str]:
    """Ce qui ne dépend pas de l'adresse : niveau de tension, prix, coût du profil."""
    reference = next((d for d in diagnostics if not d.est_zni), diagnostics[0])
    blocs = sections(reference)
    resultat = []
    raccordement = next(s for s in blocs if s.titre.startswith("3."))
    resultat += [
        contenu
        for nature, contenu in raccordement.elements
        if nature == "texte" and (contenu.startswith("Puissance") or contenu.startswith("Préciser"))
    ]
    for bloc in blocs:
        if bloc.titre.startswith(("4.", "5.")):
            resultat.append(bloc.titre[3:])
            # Le verdict carbone dépend du territoire : il est dans le tableau.
            resultat += [
                f"  {c}"
                for nature, c in bloc.elements
                if nature == "texte" and not c.startswith("Électricité du réseau")
            ]
    if any(d.est_zni for d in diagnostics):
        resultat.append(
            "La Corse et l'outre-mer ont leurs propres barèmes réglementés : les prix ci-dessus"
            " valent pour la métropole continentale."
        )
    return resultat


def limites(diagnostics: list[Diagnostic], tableau: list[Ligne]) -> list[tuple[str, str]]:
    """Limites des indicateurs du tableau et des éléments communs, chacune une seule fois."""
    references = diagnostics[0].limites
    codes: list[str] = []
    for ligne in tableau:
        if ligne.limite and ligne.limite not in codes:
            codes.append(ligne.limite)
    codes += [c for c in ("seuil_raccordement", "capacite_raccordement", "prix_entreprise",
                          "cout_profil") if c not in codes]  # fmt: skip
    return [(code, references[code]) for code in codes if code in references]


# ---------------------------------------------------------------------------
# Rendus
# ---------------------------------------------------------------------------

LARGEUR_INDICATEUR = 26


def formater_comparaison(diagnostics: list[Diagnostic], *, largeur: int = 120) -> str:
    """Tableau en texte, colonnes repliées pour tenir dans `largeur` caractères."""
    tableau = lignes(diagnostics)
    n = len(diagnostics)
    colonne = max(18, (largeur - LARGEUR_INDICATEUR - 3 * n) // n)
    separateur = "-" * (LARGEUR_INDICATEUR + n * (colonne + 3))

    def rangee(cellules: list[str], indicateur: str = "") -> list[str]:
        replies = [textwrap.wrap(c, colonne) or [""] for c in cellules]
        etiquette = textwrap.wrap(indicateur, LARGEUR_INDICATEUR) or [""]
        hauteur = max(len(etiquette), *(len(r) for r in replies))
        sortie = []
        for i in range(hauteur):
            gauche = etiquette[i] if i < len(etiquette) else ""
            droite = " | ".join((r[i] if i < len(r) else "").ljust(colonne) for r in replies)
            sortie.append(f"{gauche.ljust(LARGEUR_INDICATEUR)} | {droite}".rstrip())
        return sortie

    texte = [f"COMPARAISON DE {n} ADRESSES (sans classement)", ""]
    texte += rangee([f"{i}. {d.territoire.adresse}" for i, d in enumerate(diagnostics, 1)])
    texte.append(separateur)
    for ligne in tableau:
        texte += rangee(ligne.valeurs, ligne.indicateur)
        texte.append(separateur)
    texte += ["", "IDENTIQUE POUR TOUTES LES ADRESSES (péréquation tarifaire)"]
    texte += [f"  {c}" for c in communs(diagnostics)]
    texte += ["", "LIMITES"]
    texte += [f"  ↳ {limite}" for _, limite in limites(diagnostics, tableau)]
    return "\n".join(texte)


def _cellule_md(texte: str) -> str:
    return texte.replace("|", "\\|")


def comparaison_en_markdown(diagnostics: list[Diagnostic]) -> str:
    tableau = lignes(diagnostics)
    entete = ["Indicateur", *(d.territoire.adresse for d in diagnostics)]
    texte = [
        f"# Comparaison de {len(diagnostics)} adresses",
        "",
        "*Sans classement : chaque indicateur est donné tel quel, avec sa limite en fin de"
        " document.*",
        "",
        "| " + " | ".join(_cellule_md(c) for c in entete) + " |",
        "|" + "|".join("---" for _ in entete) + "|",
    ]
    texte += [
        "| " + " | ".join(_cellule_md(c) for c in [ligne.indicateur, *ligne.valeurs]) + " |"
        for ligne in tableau
    ]
    texte += ["", "## Identique pour toutes les adresses", ""]
    texte += [f"- {c.strip()}" for c in communs(diagnostics)]
    texte += ["", "## Limites", ""]
    texte += [f"- {limite}" for _, limite in limites(diagnostics, tableau)]
    return "\n".join(texte) + "\n"


def comparaison_en_csv(diagnostics: list[Diagnostic]) -> str:
    """Une ligne par indicateur, une colonne par adresse : prêt pour un tableur."""
    sortie = io.StringIO()
    ecrivain = csv.writer(sortie, delimiter=";", lineterminator="\n")
    ecrivain.writerow(["indicateur", *(d.territoire.adresse for d in diagnostics), "limite"])
    for ligne in lignes(diagnostics):
        limite = diagnostics[0].limites.get(ligne.limite or "", "")
        ecrivain.writerow([ligne.indicateur, *ligne.valeurs, limite])
    return sortie.getvalue()


def comparaison_en_json(diagnostics: list[Diagnostic]) -> str:
    """Le tableau, et le diagnostic complet de chaque adresse."""
    tableau = lignes(diagnostics)
    contenu = {
        "adresses": [d.territoire.adresse for d in diagnostics],
        "tableau": [
            {"indicateur": ligne.indicateur, "valeurs": ligne.valeurs, "limite": ligne.limite}
            for ligne in tableau
        ],
        "communs": [c.strip() for c in communs(diagnostics)],
        "limites": dict(limites(diagnostics, tableau)),
        "diagnostics": [diagnostic_en_dict(d) for d in diagnostics],
    }
    return json.dumps(contenu, ensure_ascii=False, indent=2)
