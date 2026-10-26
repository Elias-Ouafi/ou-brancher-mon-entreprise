from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

PARIS_TZ = ZoneInfo("Europe/Paris")
# Ordre des classes : la première porte la puissance souscrite de référence (b₁)
CLASSES = ("pointe", "hph", "hch", "hpb", "hcb")
SAISON_HAUTE = {11, 12, 1, 2, 3}
MOIS_POINTE = {12, 1, 2}
HEURES_POINTE = {9, 10, 18, 19}
# Sans courbe de charge : même puissance à toute heure de l'année
PROFIL_PLAT = "plat"
PROFIL_COURBE = "courbe"

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


# Part de l'énergie dans chaque classe : c'est elle qui pondère le prix du MWh acheminé
def parts_energie(points: Iterable[tuple[datetime, float]]) -> dict[str, float]:
    energies: dict[str, float] = defaultdict(float)
    for instant, kwh in points:
        energies[classe_temporelle(instant.astimezone(PARIS_TZ))] += kwh
    total = sum(energies.values())
    return {classe: energies[classe] / total for classe in CLASSES} if total else {}


# Un kWh à chaque heure d'une année de grille, en heure de Paris (changements d'heure compris)
def parts_profil_plat(debut: date) -> dict[str, float]:
    depart = datetime(debut.year, debut.month, debut.day, tzinfo=PARIS_TZ).astimezone(UTC)
    fin = datetime(debut.year + 1, debut.month, debut.day, tzinfo=PARIS_TZ).astimezone(UTC)
    nb_heures = int((fin - depart).total_seconds() // 3600)
    return parts_energie((depart + timedelta(hours=h), 1.0) for h in range(nb_heures))


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


# Part acheminement de la facture au TURPE en vigueur : la seule qui dépend du raccordement
def lire_acheminement(
    connection: duckdb.DuckDBPyConnection,
    domaine_turpe: str,
    *,
    puissance_kva: float,
    conso_mwh: float,
    points: Iterable[tuple[datetime, float]] | None = None,
) -> Acheminement | None:
    lignes = connection.execute(_SOUTIRAGE_SQL, {"domaine": domaine_turpe}).fetchall()
    fixes = connection.execute(_FIXES_SQL, {"domaine": domaine_turpe}).fetchone()
    if not lignes or fixes is None:
        return None
    debut = lignes[0][0]
    grilles: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for _, utilisation, classe, b, c in lignes:
        grilles[utilisation][classe] = (b, c)
    parts = parts_energie(points) if points is not None else {}
    profil = PROFIL_COURBE if parts else PROFIL_PLAT
    parts = parts or parts_profil_plat(debut)

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
