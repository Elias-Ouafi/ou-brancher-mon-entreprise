from __future__ import annotations

from datetime import date, datetime, timedelta

from reporting.data import Creneau

MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Espace insécable : « 13 h » ne doit jamais être coupé en fin de ligne
NBSP = chr(0xA0)


def nom_mois(mois: date) -> str:
    return f"{MOIS[mois.month - 1]} {mois.year}"


def nombre(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def euros(valeur: float | None, decimales: int = 2) -> str:
    return "n.d." if valeur is None else f"{nombre(valeur, decimales)} €"


def prix(valeur: float | None) -> str:
    return "n.d." if valeur is None else f"{nombre(valeur, 4)} €/kWh"


def co2(valeur: float | None) -> str:
    return "n.d." if valeur is None else f"{nombre(valeur)} g/kWh"


def ligne_ecowatt(jours_connus: int, jours_en_tension: list[tuple[date, str]]) -> str:
    if not jours_connus:
        return (
            "Aucun signal Ecowatt relevé ce mois-ci : l'API ne publie que le jour et les trois "
            "suivants (`python -m ingestion.cli ecowatt`, identifiants Ecowatt requis)."
        )
    sur = f"sur {jours_connus} jour(s) relevé(s)"
    if not jours_en_tension:
        return f"Ecowatt : aucun jour orange ni rouge {sur}."
    detail = ", ".join(f"{jour(j)} ({niveau})" for j, niveau in jours_en_tension)
    return f"Ecowatt : {len(jours_en_tension)} jour(s) de tension {sur} : {detail}."


def jour(valeur: date) -> str:
    return f"{JOURS[valeur.weekday()]} {valeur.day} {MOIS[valeur.month - 1]}"


def heure(valeur: datetime | int) -> str:
    h = valeur if isinstance(valeur, int) else valeur.hour
    return f"{h}{NBSP}h"


def plage(debut: datetime, fin: datetime) -> str:
    if debut.date() == fin.date():
        return f"{jour(debut.date())}, {heure(debut)}–{heure(fin)}"
    if fin.hour == 0 and fin.date() - debut.date() == timedelta(days=1):
        # Se termine à minuit : on reste sur le jour de début
        return f"{jour(debut.date())}, {heure(debut)}–{heure(24)}"
    return f"{jour(debut.date())} {heure(debut)} → {jour(fin.date())} {heure(fin)}"


def creneau_tarif(creneau: Creneau) -> str:
    periode = "heures creuses" if creneau.periode == "hc" else "heures pleines"
    return periode if creneau.couleur is None else f"jour {creneau.couleur}, {periode}"
