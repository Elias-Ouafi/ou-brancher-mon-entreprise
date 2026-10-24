from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

import duckdb
import httpx

from ingestion.config import Settings, get_settings
from ingestion.diagnostic import (
    DiagnosticIndisponibleError,
    ligne_chargements,
    lire_chargements,
)
from ingestion.extract_geo import (
    CONTOURS_JEU,
    TERRES_JEU,
    completer_altitudes,
    dernier_geojson,
    lire_altitudes,
    telecharger_geojson,
)
from ingestion.extract_odre import RetryableStatusError
from ingestion.geocode import Territoire
from ingestion.hexagones import (
    RAYON_SCENE,
    Grille,
    arrondir,
    construire_grilles,
    lire_contours,
    lire_terres,
    points_terrestres,
)

logger = logging.getLogger(__name__)

GABARIT = Path(__file__).with_name("carte.html")
VENDOR = Path(__file__).with_name("vendor")
SCRIPTS = ("three.min.js", "OrbitControls.js")
MARQUEUR_DONNEES = "/*__DONNEES__*/null"
MARQUEUR_SCRIPTS = "<!--__SCRIPTS__-->"
# Gardé dans la page générée : l'adresse se remplace sans reconstruire la carte
MARQUEUR_ADRESSE = "/*__ADRESSE__*/"
_LIGNE_ADRESSE = re.compile(r"^(DATA\.adresse = /\*__ADRESSE__\*/).*$", re.MULTILINE)

_DEPARTEMENTS_SQL = """
select
    d.code_departement as c, d.departement as n, d.code_insee_region as r, d.region as rn,
    d.est_zni as z, d.annee_pression as an,
    round(d.conso_industrielle_departement_gwh, 0) as gwh,
    d.rang_pression_departement as rk, d.nb_departements_classes as nc,
    d.nb_sites_departement as s, round(d.taux_couverture_region, 2) as tc,
    d.nb_postes_rte as p, d.nb_postes_htb3 as p3, d.nb_postes_en_projet as pp,
    d.nb_contraintes_departement as k, c.intensite_acv_g_co2e_kwh as co2
from gold.fct_diagnostic_territoire d
left join (
    select distinct code_insee_region, intensite_acv_g_co2e_kwh from gold.fct_carbone_territoire
) c using (code_insee_region)
order by d.code_departement
"""
_SITES_SQL = """
select commune as n, code_departement as c, latitude as la, longitude as lo
from gold.sites_cles_en_main order by id_site
"""
_ZONES_SQL = """
select zone as n, priorite as pr, annee_capacite as a, latitude as la, longitude as lo
from gold.zones_raccordement_rte order by priorite, zone
"""
_PRIX_SQL = """
select code_tranche as t, libelle_tranche as l, round(prix_reference_eur_kwh, 4) as p
from gold.fct_prix_entreprise_tranche order by conso_min_mwh
"""


def _lignes(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    relation = connection.execute(sql)
    colonnes = [colonne[0] for colonne in relation.description]
    return [dict(zip(colonnes, ligne, strict=True)) for ligne in relation.fetchall()]


def lire_donnees_gold(database: Path) -> dict[str, Any]:
    if not Path(database).exists():
        raise DiagnosticIndisponibleError(
            f"warehouse introuvable : {database} (lancer refresh avant la carte)"
        )
    with duckdb.connect(str(database), read_only=True) as connection:
        gold: dict[str, Any] = {
            cle: _lignes(connection, sql)
            for cle, sql in (
                ("departements", _DEPARTEMENTS_SQL),
                ("sites", _SITES_SQL),
                ("zones", _ZONES_SQL),
                ("prix", _PRIX_SQL),
            )
        }
        gold["donnees"] = ligne_chargements(lire_chargements(connection))
        return gold


# Case terrestre d'un point : encart de son département d'outre-mer, sinon métropole
def _reperer(
    grilles: list[Grille], lon: float, lat: float, departement: str | None = None
) -> list[int] | None:
    indice = next((i for i, g in enumerate(grilles) if g.code and g.code == departement), 0)
    grille = grilles[indice]
    case = grille.case_terrestre(lon, lat, departement) or grille.case_terrestre(lon, lat)
    return [indice, case.c, case.r] if case else None


def construire_donnees(
    grilles: list[Grille],
    altitudes: dict[tuple[float, float], float | None],
    gold: dict[str, Any],
    *,
    territoire: Territoire | None = None,
    genere_le: datetime | None = None,
) -> dict[str, Any]:
    cases: list[list[Any]] = []
    for indice, grille in enumerate(grilles):
        for case in grille.cases:
            # Mer : 3 valeurs ; terre voisine : 4 ; case française : 8
            if not case.departement:
                cases.append([indice, case.c, case.r, *([1] if case.etranger else [])])
                continue
            mesures = [altitudes.get(arrondir(*p)) for p in case.echantillons]
            mesures = [m for m in mesures if m is not None]
            cases.append(
                [
                    indice,
                    case.c,
                    case.r,
                    case.departement,
                    round(fmean(mesures)) if mesures else 0,
                    round(max(mesures)) if mesures else 0,
                    round(case.lat, 2),
                    round(case.lon, 2),
                ]
            )
    return {
        "genereLe": (genere_le or datetime.now(UTC)).date().isoformat(),
        "rayonScene": RAYON_SCENE,
        "grilles": [
            {
                "nom": g.nom,
                "code": g.code,
                "x0": round(g.scene_x0, 3),
                "z0": round(g.scene_z0, 3),
                "rayonKm": g.rayon_km,
            }
            for g in grilles
        ],
        "cases": cases,
        "departements": gold["departements"],
        "sites": [
            {"n": s["n"], "c": s["c"], "case": _reperer(grilles, s["lo"], s["la"], s["c"])}
            for s in gold["sites"]
        ],
        "zones": [
            {"n": z["n"], "pr": z["pr"], "a": z["a"], "case": _reperer(grilles, z["lo"], z["la"])}
            for z in gold["zones"]
        ],
        "prix": gold["prix"],
        "donnees": gold.get("donnees"),
        "adresse": construire_adresse(grilles, territoire),
    }


def construire_adresse(
    grilles: list[Grille], territoire: Territoire | None
) -> dict[str, Any] | None:
    if territoire is None:
        return None
    return {
        "libelle": territoire.adresse,
        "departement": territoire.code_departement,
        "case": _reperer(
            grilles, territoire.longitude, territoire.latitude, territoire.code_departement
        ),
    }


# `</` échappé : un nom contenant `</script>` ne doit pas fermer la balise
def _json(valeur: Any) -> str:
    return json.dumps(valeur, ensure_ascii=False, default=str).replace("</", "<\\/")


# Page autonome : gabarit, données et scripts three.js intégrés (fonctionne hors ligne)
def rendre_html(donnees: dict[str, Any]) -> str:
    donnees = dict(donnees)
    adresse = donnees.pop("adresse", None)
    scripts = "\n".join(
        f"<script>\n{(VENDOR / nom).read_text(encoding='utf-8')}\n</script>" for nom in SCRIPTS
    )
    gabarit = GABARIT.read_text(encoding="utf-8")
    html = gabarit.replace(MARQUEUR_SCRIPTS, scripts).replace(MARQUEUR_DONNEES, _json(donnees))
    return placer_adresse(html, adresse)


def placer_adresse(html: str, adresse: dict[str, Any] | None) -> str:
    html, remplacements = _LIGNE_ADRESSE.subn(lambda m: f"{m.group(1)}{_json(adresse)};", html)
    if remplacements != 1:
        raise ValueError("ligne de l'adresse introuvable dans la carte")
    return html


# GeoJSON du bronze, téléchargé à la première utilisation ; `None` si injoignable
def _geojson(jeu: str, url: str, settings: Settings) -> Path | None:
    path = dernier_geojson(jeu, settings)
    if path is None:
        try:
            path = telecharger_geojson(jeu, url, settings=settings)
        except (httpx.HTTPError, RetryableStatusError) as error:
            logger.warning("%s indisponible : %s", jeu, error)
    return path


def _grilles(settings: Settings) -> list[Grille]:
    contours = _geojson(CONTOURS_JEU, settings.contours_url, settings)
    if contours is None:
        raise DiagnosticIndisponibleError("contours des departements introuvables (lancer refresh)")
    terres = _geojson(TERRES_JEU, settings.terres_url, settings)
    return construire_grilles(
        lire_contours(contours), terres=lire_terres(terres) if terres else None
    )


def _chemin(settings: Settings, output: Path | None) -> Path:
    return output or Path(settings.reports_dir) / "carte.html"


def generer_carte(
    *,
    settings: Settings | None = None,
    territoire: Territoire | None = None,
    output: Path | None = None,
) -> Path:
    settings = settings or get_settings()
    gold = lire_donnees_gold(Path(settings.duckdb_path))
    grilles = _grilles(settings)
    try:
        altitudes = completer_altitudes(
            points_terrestres(grilles), settings=settings, run_id="carte"
        )
    except (httpx.HTTPError, RetryableStatusError) as error:
        logger.warning("relief incomplet, cases manquantes a plat : %s", error)
        altitudes = lire_altitudes(settings)
    html = rendre_html(construire_donnees(grilles, altitudes, gold, territoire=territoire))
    path = _chemin(settings, output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


# Plus ancienne que le warehouse, le gabarit ou les contours, la carte est refaite
def _carte_a_jour(path: Path, settings: Settings) -> bool:
    if not path.exists() or MARQUEUR_ADRESSE not in path.read_text(encoding="utf-8"):
        return False
    sources = [Path(settings.duckdb_path), GABARIT, *(VENDOR / nom for nom in SCRIPTS)]
    sources += [p for jeu in (CONTOURS_JEU, TERRES_JEU) if (p := dernier_geojson(jeu, settings))]
    date_carte = path.stat().st_mtime
    return all(not p.exists() or p.stat().st_mtime <= date_carte for p in sources)


# Carte à jour : seule l'épingle bouge ; sinon reconstruction complète
def mettre_a_jour_carte(
    *,
    settings: Settings | None = None,
    territoire: Territoire | None = None,
    output: Path | None = None,
) -> Path:
    settings = settings or get_settings()
    path = _chemin(settings, output)
    if not _carte_a_jour(path, settings):
        return generer_carte(settings=settings, territoire=territoire, output=path)
    adresse = construire_adresse(_grilles(settings), territoire)
    path.write_text(placer_adresse(path.read_text(encoding="utf-8"), adresse), encoding="utf-8")
    return path
