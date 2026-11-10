from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

import duckdb
import httpx

from ingestion.config import Settings, get_settings
from ingestion.diagnostic import (
    DISTANCE_ZONE_UTILE_KM,
    Diagnostic,
    DiagnosticIndisponibleError,
    DonneesAbsentesError,
    ligne_chargements,
    lire_chargements,
    phrase_verdict,
    verdict,
)
from ingestion.extract_geo import (
    CONTOURS_JEU,
    PAYS_JEU,
    TERRES_JEU,
    completer_altitudes,
    dernier_geojson,
    lire_altitudes,
    telecharger_geojson,
)
from ingestion.extract_odre import RetryableStatusError
from ingestion.geocode import Territoire
from ingestion.hexagones import (
    PAYS_ETUDIES,
    RAYON_SCENE,
    Grille,
    Monde,
    arrondir,
    construire_grilles,
    construire_monde,
    lire_contours,
    lire_pays,
    lire_terres,
    points_terrestres,
)
from reporting import charte

logger = logging.getLogger(__name__)

GABARIT = Path(__file__).with_name("carte.html")
VENDOR = Path(__file__).with_name("vendor")
SCRIPTS = ("three.min.js", "OrbitControls.js")
MARQUEUR_DONNEES = "/*__DONNEES__*/null"
MARQUEUR_SCRIPTS = "<!--__SCRIPTS__-->"
MARQUEUR_CHARTE = "/*__CHARTE__*/"
# Gardé dans la page générée : l'adresse se remplace sans reconstruire la carte
MARQUEUR_ADRESSE = "/*__ADRESSE__*/"
Seconde = tuple[Territoire, dict[str, Any] | None]
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
select id_site as id, commune as n, code_departement as c, latitude as la, longitude as lo
from gold.sites_cles_en_main order by id_site
"""
_ZONES_SQL = """
select zone as n, priorite as pr, annee_capacite as a, latitude as la, longitude as lo
from gold.zones_raccordement_rte order by priorite, zone
"""
_PRODUCTION_SQL = """
select commune as n, code_departement as c, filiere as f, puissance_mw as mw,
    energie_gwh as gwh, nb_installations as ni, latitude as la, longitude as lo
from gold.fct_sites_production order by puissance_mw desc
"""
_FRET_SQL = """
select mode as m, nom as n, code_departement as c, annee as a, volume_t as v,
    conteneurs_evp as evp, vols_cargo as vc, longueur_voies_m as ml, manutention as eng,
    latitude as la, longitude as lo
from gold.fct_poles_fret order by mode, volume_t desc nulls last, longueur_voies_m desc
"""
_PRIX_SQL = """
select code_tranche as t, conso_min_mwh as mn, conso_max_mwh as mx,
    round(prix_reference_eur_kwh, 4) as e, dernier_semestre as s
from gold.fct_prix_entreprise_tranche order by conso_min_mwh
"""


def _lignes(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    relation = connection.execute(sql)
    colonnes = [colonne[0] for colonne in relation.description]
    return [dict(zip(colonnes, ligne, strict=True)) for ligne in relation.fetchall()]


def lire_donnees_gold(database: Path) -> dict[str, Any]:
    if not Path(database).exists():
        raise DonneesAbsentesError(f"warehouse introuvable : {database}")
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
        # Absent d'un warehouse antérieur aux pôles de production : la carte s'en passe
        try:
            gold["production"] = _lignes(connection, _PRODUCTION_SQL)
        except duckdb.CatalogException:
            gold["production"] = []
        # De même pour les pôles de fret, ajoutés après les pôles de production
        try:
            gold["fret"] = _lignes(connection, _FRET_SQL)
        except duckdb.CatalogException:
            gold["fret"] = []
        gold["donnees"] = ligne_chargements(lire_chargements(connection))
        return gold


def reperer(
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
    verdict_adresse: dict[str, Any] | None = None,
    autre: Seconde | None = None,
    genere_le: datetime | None = None,
    monde: Monde | None = None,
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
        "monde": _monde_en_donnees(monde),
        "departements": gold["departements"],
        "sites": [
            {
                "id": s["id"],
                "n": s["n"],
                "c": s["c"],
                "case": reperer(grilles, s["lo"], s["la"], s["c"]),
            }
            for s in gold["sites"]
        ],
        "zones": [
            {"n": z["n"], "pr": z["pr"], "a": z["a"], "case": reperer(grilles, z["lo"], z["la"])}
            for z in gold["zones"]
        ],
        "production": [
            {
                "n": p["n"],
                "c": p["c"],
                "f": p["f"],
                "mw": p["mw"],
                "gwh": p["gwh"],
                "ni": p["ni"],
                "case": reperer(grilles, p["lo"], p["la"], p["c"]),
            }
            for p in gold.get("production", [])
        ],
        "fret": [
            {
                **{cle: p[cle] for cle in ("m", "n", "c", "a", "v", "evp", "vc", "ml", "eng")},
                "case": reperer(grilles, p["lo"], p["la"], p["c"]),
            }
            for p in gold.get("fret", [])
        ],
        "prix": gold.get("prix", []),
        "donnees": gold.get("donnees"),
        "adresse": construire_adresse(grilles, territoire, verdict_adresse, autre),
    }


# Tuile du monde : [x, z, indice du pays] ; un pays non étudié n'a que son nom
def _monde_en_donnees(monde: Monde | None) -> dict[str, Any] | None:
    if monde is None:
        return None
    codes = sorted(monde.pays)
    indices = {code: i for i, code in enumerate(codes)}
    return {
        "rayon": monde.rayon_scene,
        "pays": [{"n": monde.pays[c], "e": c in PAYS_ETUDIES} for c in codes],
        "cases": [[round(t.x, 1), round(t.z, 1), indices[t.pays]] for t in monde.tuiles],
    }


def _nombre(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", "\u202f").replace(".", ",")


# L'encart et le haut du PDF, en quelques mots : le détail reste dans le diagnostic
def resume_adresse(diagnostic: Diagnostic) -> list[dict[str, str]]:
    criteres = {c.nom: c for c in verdict(diagnostic)}
    c = diagnostic.carbone
    electricite = (
        (
            f"{'décarbonée' if c.est_decarbone else 'carbonée'}, "
            f"{_nombre(c.intensite_acv_g_co2e_kwh, 1)} gCO₂e/kWh",
            "ok" if c.est_decarbone else "attention",
        )
        if c
        else ("non disponible", "inconnu")
    )
    p = diagnostic.prix_locaux
    locaux = (
        f"{_nombre(p.prix_m2_median)} €/m²" + (" (département)" if p.niveau != "commune" else "")
        if p
        else "non disponible"
    )
    lignes = [
        ("Électricité", *electricite),
        ("Raccordement", criteres["Raccordement"].valeur, criteres["Raccordement"].etat),
        ("Facture", criteres["Facture"].valeur, criteres["Facture"].etat),
        ("Prix des locaux", locaux, "info" if p else "inconnu"),
        ("Risques", criteres["Risques"].valeur, criteres["Risques"].etat),
        ("Foncier", criteres["Foncier"].valeur, criteres["Foncier"].etat),
    ]
    return [{"nom": nom, "valeur": valeur, "etat": etat} for nom, valeur, etat in lignes]


def verdict_carte(diagnostic: Diagnostic) -> dict[str, Any]:
    criteres = verdict(diagnostic)
    zone = diagnostic.zone_rte
    site = diagnostic.site_cle_en_main
    return {
        "phrase": phrase_verdict(criteres),
        "criteres": [asdict(c) for c in criteres],
        "resume": resume_adresse(diagnostic),
        # Les jauges de l'encart ont besoin des nombres, pas des phrases
        "carbone": {"g": c.intensite_acv_g_co2e_kwh, "seuil": c.seuil_decarbone_g_co2e_kwh}
        if (c := diagnostic.carbone)
        else None,
        "prix": {"eur_kwh": p.prix_reference_eur_kwh} if (p := diagnostic.prix) else None,
        "zone": zone.zone if zone and zone.distance_km <= DISTANCE_ZONE_UTILE_KM else None,
        "site": site.id_site if site else None,
    }


# La seconde adresse de l'application (deux au plus) voyage dans la première, sous `autre` :
# la page générée garde une seule ligne à remplacer
def construire_adresse(
    grilles: list[Grille],
    territoire: Territoire | None,
    verdict_adresse: dict[str, Any] | None = None,
    autre: Seconde | None = None,
) -> dict[str, Any] | None:
    if territoire is None:
        return None
    adresse = {
        "libelle": territoire.adresse,
        "departement": territoire.code_departement,
        "case": reperer(
            grilles, territoire.longitude, territoire.latitude, territoire.code_departement
        ),
        "verdict": verdict_adresse,
    }
    if autre is not None:
        adresse["autre"] = construire_adresse(grilles, *autre)
    return adresse


# `</` échappé : un nom contenant `</script>` ne doit pas fermer la balise
def _json(valeur: Any) -> str:
    return json.dumps(valeur, ensure_ascii=False, default=str).replace("</", "<\\/")


def rendre_html(donnees: dict[str, Any]) -> str:
    donnees = dict(donnees)
    adresse = donnees.pop("adresse", None)
    scripts = "\n".join(
        f"<script>\n{(VENDOR / nom).read_text(encoding='utf-8')}\n</script>" for nom in SCRIPTS
    )
    gabarit = GABARIT.read_text(encoding="utf-8")
    html = gabarit.replace(MARQUEUR_SCRIPTS, scripts).replace(MARQUEUR_DONNEES, _json(donnees))
    html = html.replace(MARQUEUR_CHARTE, charte.css())
    return placer_adresse(html, adresse)


def placer_adresse(html: str, adresse: dict[str, Any] | None) -> str:
    html, remplacements = _LIGNE_ADRESSE.subn(lambda m: f"{m.group(1)}{_json(adresse)};", html)
    if remplacements != 1:
        raise ValueError("ligne de l'adresse introuvable dans la carte")
    return html


def reprendre_adresse(ancienne: str, nouvelle: str) -> str:
    ligne = _LIGNE_ADRESSE.search(ancienne)
    if ligne is None:
        return nouvelle
    return _LIGNE_ADRESSE.sub(lambda _: ligne.group(0), nouvelle, count=1)


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


# Sans les pays (hors ligne au premier lancement), la France reste seule : le reste suit au refresh
def _monde(grilles: list[Grille], settings: Settings) -> Monde | None:
    pays = _geojson(PAYS_JEU, settings.pays_url, settings)
    return construire_monde(lire_pays(pays), grilles) if pays else None


def chemin(settings: Settings, output: Path | None) -> Path:
    return output or Path(settings.reports_dir) / "carte.html"


def generer_carte(
    *,
    settings: Settings | None = None,
    territoire: Territoire | None = None,
    verdict_adresse: dict[str, Any] | None = None,
    autre: Seconde | None = None,
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
    html = rendre_html(
        construire_donnees(
            grilles,
            altitudes,
            gold,
            territoire=territoire,
            verdict_adresse=verdict_adresse,
            autre=autre,
            monde=_monde(grilles, settings),
        )
    )
    path = chemin(settings, output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


# Plus ancienne que le warehouse, le gabarit ou les contours, la carte est refaite
def carte_a_jour(path: Path, settings: Settings) -> bool:
    if not path.exists() or MARQUEUR_ADRESSE not in path.read_text(encoding="utf-8"):
        return False
    sources = [Path(settings.duckdb_path), GABARIT, *(VENDOR / nom for nom in SCRIPTS)]
    sources += [Path(charte.__file__), *(charte.police(f, g) for f, g in charte.FICHIERS)]
    jeux = (CONTOURS_JEU, TERRES_JEU, PAYS_JEU)
    sources += [p for jeu in jeux if (p := dernier_geojson(jeu, settings))]
    date_carte = path.stat().st_mtime
    return all(not p.exists() or p.stat().st_mtime <= date_carte for p in sources)


# Carte à jour : seule l'épingle bouge ; sinon reconstruction complète
def mettre_a_jour_carte(
    *,
    settings: Settings | None = None,
    territoire: Territoire | None = None,
    verdict_adresse: dict[str, Any] | None = None,
    autre: Seconde | None = None,
    output: Path | None = None,
) -> Path:
    settings = settings or get_settings()
    path = chemin(settings, output)
    if not carte_a_jour(path, settings):
        return generer_carte(
            settings=settings,
            territoire=territoire,
            verdict_adresse=verdict_adresse,
            autre=autre,
            output=path,
        )
    adresse = construire_adresse(_grilles(settings), territoire, verdict_adresse, autre)
    path.write_text(placer_adresse(path.read_text(encoding="utf-8"), adresse), encoding="utf-8")
    return path
