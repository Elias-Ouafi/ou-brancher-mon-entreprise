from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import duckdb

PARIS_TZ = ZoneInfo("Europe/Paris")
# Heure locale des systèmes insulaires, pour lire leur profil carbone horaire
FUSEAUX = {
    "01": ZoneInfo("America/Guadeloupe"),
    "02": ZoneInfo("America/Martinique"),
    "03": ZoneInfo("America/Cayenne"),
    "04": ZoneInfo("Indian/Reunion"),
    "06": ZoneInfo("Indian/Mayotte"),
}
# Au-delà, la consommation n'est plus celle d'un site raccordable au tarif bleu (≤ 36 kVA)
PUISSANCE_TARIF_BLEU_KW = 36
# Moins de la moitié des kWh appariés au mix horaire : comparaison carbone non publiée
COUVERTURE_MINIMALE = 0.5


class CourbeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Courbe:
    pas_minutes: int
    # (début de l'intervalle en UTC, kWh consommés pendant l'intervalle)
    points: tuple[tuple[datetime, float], ...]

    @property
    def conso_kwh(self) -> float:
        return sum(kwh for _, kwh in self.points)

    @property
    def nb_jours(self) -> float:
        debut, fin = self.points[0][0], self.points[-1][0] + timedelta(minutes=self.pas_minutes)
        return (fin - debut).total_seconds() / 86_400

    @property
    def conso_annuelle_mwh(self) -> float:
        return self.conso_kwh / self.nb_jours * 365 / 1000

    @property
    def puissance_max_kw(self) -> float:
        return max(kwh for _, kwh in self.points) * 60 / self.pas_minutes


@dataclass(frozen=True, slots=True)
class BilanCourbe:
    debut: date
    fin: date
    nb_jours: float
    conso_kwh: float
    conso_annuelle_mwh: float
    puissance_max_kw: float
    part_hc: float
    # Au barème HP/HC en vigueur, ramené à un an ; sans objet au-delà de 36 kVA
    cout_htva_eur_an: float | None
    # Intensité de cette consommation et du réseau aux mêmes heures (même source, même unité)
    intensite_courbe_g_kwh: float | None
    intensite_reseau_g_kwh: float | None
    source_carbone: str | None


def _texte(contenu: bytes) -> str:
    try:
        return contenu.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenu.decode("latin-1")


def _instant(valeur: str) -> datetime:
    instant = datetime.fromisoformat(valeur.strip().replace(" ", "T"))
    return instant if instant.tzinfo else instant.replace(tzinfo=PARIS_TZ)


# Export « Mes données » d'Enedis (métadonnées puis `Horodate;Valeur` en W) : la vraie
# consommation du site remplace le profil type pour la tranche de prix, la pointe et le carbone
def lire_courbe(contenu: bytes) -> Courbe:
    lignes = list(csv.reader(io.StringIO(_texte(contenu)), delimiter=";"))
    entete = next(
        (i for i, ligne in enumerate(lignes) if ligne and ligne[0].strip().lower() == "horodate"),
        None,
    )
    if entete is None:
        raise CourbeError("courbe de charge sans ligne d'en-tête « Horodate;Valeur »")
    en_kw = any(cellule.strip() == "kW" for ligne in lignes[:entete] for cellule in ligne)
    mesures = sorted(
        (_instant(ligne[0]).astimezone(UTC), float(ligne[1].replace(",", ".")))
        for ligne in lignes[entete + 1 :]
        if len(ligne) >= 2 and ligne[0].strip() and ligne[1].strip()
    )
    if len(mesures) < 2:
        raise CourbeError("courbe de charge vide ou réduite à une mesure")
    ecarts = [(b[0] - a[0]).total_seconds() / 60 for a, b in pairwise(mesures)]
    pas = int(min(e for e in ecarts if e > 0))
    facteur = 1.0 if en_kw else 1 / 1000
    # Enedis date la fin de l'intervalle : la puissance moyenne vaut pour le pas qui précède
    points = tuple(
        (instant - timedelta(minutes=pas), puissance * facteur * pas / 60)
        for instant, puissance in mesures
    )
    return Courbe(pas_minutes=pas, points=points)


def _par_heure(courbe: Courbe) -> dict[datetime, float]:
    heures: dict[datetime, float] = defaultdict(float)
    for instant, kwh in courbe.points:
        heures[instant.replace(minute=0, second=0, microsecond=0)] += kwh
    return heures


# Coût, heures creuses et carbone de la consommation réelle, comparés au réseau aux mêmes heures
def bilan_courbe(
    courbe: Courbe,
    connection: duckdb.DuckDBPyConnection,
    *,
    profil_horaire: tuple[tuple[int, float], ...] = (),
    code_region: str | None = None,
) -> BilanCourbe:
    fuseau = FUSEAUX.get(code_region or "", PARIS_TZ)
    heures = _par_heure(courbe)
    total = courbe.conso_kwh
    hc = sum(kwh for h, kwh in heures.items() if not 6 <= h.astimezone(PARIS_TZ).hour < 22)
    tarifs = dict(
        connection.execute(
            "select periode, prix_ht_eur_kwh + accise_eur_kwh from gold.tarifs_hphc"
            " where date_fin is null"
        ).fetchall()
    )
    cout = None
    if courbe.puissance_max_kw <= PUISSANCE_TARIF_BLEU_KW and {"hp", "hc"} <= set(tarifs):
        cout_periode = (total - hc) * tarifs["hp"] + hc * tarifs["hc"]
        cout = cout_periode / courbe.nb_jours * 365

    if profil_horaire:
        intensites = dict(profil_horaire)
        co2 = {h: intensites[h.astimezone(fuseau).hour] for h in heures}
        source = "profil horaire EDF SEI, cycle de vie"
    else:
        co2 = dict(
            connection.execute(
                "select heure_utc, taux_co2_g_kwh from gold.fct_creneau_horaire"
                " where heure_utc >= $debut and heure_utc <= $fin and taux_co2_g_kwh is not null",
                {"debut": min(heures), "fin": max(heures)},
            ).fetchall()
        )
        co2 = {h.astimezone(UTC): v for h, v in co2.items()}
        source = "éCO2mix de RTE, émissions directes"
    appariees = {h: kwh for h, kwh in heures.items() if h in co2}
    intensite_courbe = intensite_reseau = None
    if total > 0 and sum(appariees.values()) / total >= COUVERTURE_MINIMALE:
        kwh = sum(appariees.values())
        intensite_courbe = sum(v * co2[h] for h, v in appariees.items()) / kwh
        intensite_reseau = sum(co2[h] for h in appariees) / len(appariees)

    return BilanCourbe(
        debut=courbe.points[0][0].astimezone(PARIS_TZ).date(),
        fin=courbe.points[-1][0].astimezone(PARIS_TZ).date(),
        nb_jours=courbe.nb_jours,
        conso_kwh=total,
        conso_annuelle_mwh=courbe.conso_annuelle_mwh,
        puissance_max_kw=courbe.puissance_max_kw,
        part_hc=hc / total if total else 0.0,
        cout_htva_eur_an=cout,
        intensite_courbe_g_kwh=intensite_courbe,
        intensite_reseau_g_kwh=intensite_reseau,
        source_carbone=source if intensite_courbe is not None else None,
    )
