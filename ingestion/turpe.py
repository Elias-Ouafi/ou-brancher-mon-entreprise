from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache, partial
from zoneinfo import ZoneInfo

import duckdb

PARIS_TZ = ZoneInfo("Europe/Paris")
# Ordre des classes : la première porte la puissance souscrite de référence (b₁)
CLASSES = ("pointe", "hph", "hch", "hpb", "hcb")
SAISON_HAUTE = {11, 12, 1, 2, 3}
MOIS_POINTE = {12, 1, 2}
HEURES_POINTE = {9, 10, 18, 19}
# Plages HTB de la délibération CRE 2025-77 : nouveau régime au 1er janvier 2027, propre à la
# Nouvelle-Aquitaine et à l'Occitanie pour suivre leur production photovoltaïque
DEBUT_PLAGES_HTB_2027 = date(2027, 1, 1)
REGIONS_HTB_MERIDIENNES = {"75", "76"}
HEURES_POINTE_HTB_MERIDIENNES = {7, 8, 18, 19}
# Sans courbe de charge : même puissance à toute heure de l'année
PROFIL_PLAT = "plat"
PROFIL_COURBE = "courbe"
# Journée type mesurée par Enedis pour l'activité demandée (`--activite`)
PROFIL_ACTIVITE = "activite"

_SOUTIRAGE_SQL = """
select date_debut, utilisation, classe, b_eur_kva_an, c_eur_mwh
from gold.turpe_soutirage
where domaine_turpe = $domaine and date_fin is null
"""

_FIXES_SQL = """
select gestion_eur_an + comptage_eur_an
from gold.turpe_gestion_comptage
where domaine_turpe = $domaine and date_fin is null
"""


@dataclass(frozen=True, slots=True)
class Acheminement:
    domaine_turpe: str
    # `courte` ou `longue` utilisation : la moins chère pour ce profil
    utilisation: str
    date_debut: date
    puissance_kva: float
    conso_mwh: float
    gestion_comptage_eur: float
    puissance_eur: float
    energie_eur: float
    total_eur: float
    prix_eur_mwh: float
    profil: str

    # Nouvelle grille chaque 1er août : passé cette date, le seed est peut-être périmé
    def a_reverifier(self, aujourd_hui: date | None = None) -> bool:
        annee = self.date_debut.year + (self.date_debut >= date(self.date_debut.year, 8, 1))
        return (aujourd_hui or date.today()) >= date(annee, 8, 1)


# Classe temporelle d'une heure locale : saison haute de novembre à mars, heures creuses de
# 22 h à 6 h (fixées localement en réalité), pointe fixe en hiver hors dimanche
def classe_temporelle(instant: datetime) -> str:
    if instant.month in MOIS_POINTE and instant.weekday() != 6 and instant.hour in HEURES_POINTE:
        return "pointe"
    periode = "hc" if not 6 <= instant.hour < 22 else "hp"
    return periode + ("h" if instant.month in SAISON_HAUTE else "b")


# Jours fériés légaux, dont ceux qui suivent Pâques (calcul grégorien anonyme)
@lru_cache
def _jours_feries(annee: int) -> frozenset[date]:
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mois, jour = divmod(h + m - 7 * n + 114, 31)
    paques = date(annee, mois, jour + 1)
    fixes = ((1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25))
    return frozenset(
        {date(annee, *jour_mois) for jour_mois in fixes}
        | {paques + timedelta(days=decalage) for decalage in (1, 39, 50)}
    )


# Classe HTB d'une heure locale : week-ends et jours fériés entièrement creux, pointe en semaine
def classe_temporelle_htb(instant: datetime, code_region: str | None = None) -> str:
    jour, heure = instant.date(), instant.hour
    saison = "h" if instant.month in SAISON_HAUTE else "b"
    if instant.weekday() >= 5 or jour in _jours_feries(jour.year):
        return "hc" + saison
    nouveau_regime = jour >= DEBUT_PLAGES_HTB_2027
    meridien = nouveau_regime and code_region in REGIONS_HTB_MERIDIENNES
    pointe = HEURES_POINTE_HTB_MERIDIENNES if meridien else HEURES_POINTE
    if instant.month in MOIS_POINTE and heure in pointe:
        return "pointe"
    if not nouveau_regime:
        creuse = heure < 7 or heure >= 23
    elif meridien:
        creuse = (heure in {2, 3} or 10 <= heure < 16) if saison == "h" else 10 <= heure < 18
    else:
        creuse = (heure < 6 or heure >= 22) if saison == "h" else 2 <= heure < 6 or 12 <= heure < 16
    return ("hc" if creuse else "hp") + saison


# Part de l'énergie dans chaque classe : c'est elle qui pondère le prix du MWh acheminé
def parts_energie(
    points: Iterable[tuple[datetime, float]],
    classer: Callable[[datetime], str] = classe_temporelle,
) -> dict[str, float]:
    energies: dict[str, float] = defaultdict(float)
    for instant, kwh in points:
        energies[classer(instant.astimezone(PARIS_TZ))] += kwh
    total = sum(energies.values())
    return {classe: energies[classe] / total for classe in CLASSES} if total else {}


# Un kWh à chaque heure d'une année de grille, en heure de Paris (changements d'heure compris) ;
# avec une journée type {(type de jour, heure locale): kW}, chaque heure pèse sa puissance
def parts_profil_annee(
    debut: date,
    journee_type: dict[tuple[str, int], float] | None = None,
    classer: Callable[[datetime], str] = classe_temporelle,
) -> dict[str, float]:
    depart = datetime(debut.year, debut.month, debut.day, tzinfo=PARIS_TZ).astimezone(UTC)
    fin = datetime(debut.year + 1, debut.month, debut.day, tzinfo=PARIS_TZ).astimezone(UTC)
    nb_heures = int((fin - depart).total_seconds() // 3600)

    def poids(instant: datetime) -> float:
        if journee_type is None:
            return 1.0
        local = instant.astimezone(PARIS_TZ)
        return journee_type.get(("ouvre" if local.weekday() < 5 else "week_end", local.hour), 0.0)

    heures = (depart + timedelta(hours=h) for h in range(nb_heures))
    return parts_energie(((instant, poids(instant)) for instant in heures), classer)


# Parts puissance et énergie d'une version : une même puissance souscrite dans toutes les
# classes ne paie que le coefficient b de la première, l'énergie de chaque classe paie son prix
def _cout(
    coefficients: dict[str, tuple[float, float]],
    puissance_kva: float,
    conso_mwh: float,
    parts: dict[str, float],
) -> tuple[float, float]:
    # En BT, la pointe n'existe pas : ses heures sont des heures pleines de saison haute
    if "pointe" not in coefficients:
        parts = dict(parts, hph=parts.get("hph", 0) + parts.get("pointe", 0), pointe=0.0)
    premiere = next(c for c in CLASSES if c in coefficients)
    puissance = coefficients[premiere][0] * puissance_kva
    energie = sum(conso_mwh * parts.get(c, 0) * prix for c, (_, prix) in coefficients.items())
    return puissance, energie


def lire_acheminement(
    connection: duckdb.DuckDBPyConnection,
    domaine_turpe: str,
    *,
    puissance_kva: float,
    conso_mwh: float,
    points: Iterable[tuple[datetime, float]] | None = None,
    journee_type: dict[tuple[str, int], float] | None = None,
    code_region: str | None = None,
) -> Acheminement | None:
    lignes = connection.execute(_SOUTIRAGE_SQL, {"domaine": domaine_turpe}).fetchall()
    fixes = connection.execute(_FIXES_SQL, {"domaine": domaine_turpe}).fetchone()
    if not lignes or fixes is None:
        return None
    debut = lignes[0][0]
    grilles: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for _, utilisation, classe, b, c in lignes:
        grilles[utilisation][classe] = (b, c)
    classer = (
        partial(classe_temporelle_htb, code_region=code_region)
        if domaine_turpe.startswith("htb")
        else classe_temporelle
    )
    parts = parts_energie(points, classer) if points is not None else {}
    profil = PROFIL_COURBE
    if not parts and journee_type:
        parts, profil = parts_profil_annee(debut, journee_type, classer), PROFIL_ACTIVITE
    if not parts:
        parts, profil = parts_profil_annee(debut, classer=classer), PROFIL_PLAT

    versions = []
    for utilisation, coefficients in grilles.items():
        puissance, energie = _cout(coefficients, puissance_kva, conso_mwh, parts)
        versions.append((fixes[0] + puissance + energie, utilisation, puissance, energie))
    total, utilisation, puissance, energie = min(versions)
    return Acheminement(
        domaine_turpe=domaine_turpe,
        utilisation=utilisation,
        date_debut=debut,
        puissance_kva=puissance_kva,
        conso_mwh=conso_mwh,
        gestion_comptage_eur=fixes[0],
        puissance_eur=puissance,
        energie_eur=energie,
        total_eur=total,
        prix_eur_mwh=total / conso_mwh,
        profil=profil,
    )
