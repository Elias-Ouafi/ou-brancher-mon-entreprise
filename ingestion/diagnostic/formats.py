"""Mise en forme française des nombres, montants, distances et durées."""

from __future__ import annotations


def nombre_fr(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def gwh_fr(valeur: float) -> str:
    return f"{nombre_fr(valeur, 1 if valeur < 100 else 0)} GWh"


def pourcentage(part: float) -> str:
    return f"{nombre_fr(part * 100, 2 if part < 0.01 else 1)} %"


MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip


# « à 9 km » ; en deçà d'un kilomètre, la précision du point de référence ne suit pas
def distance_fr(km: float) -> str:
    return "à moins d'un km" if km < 1 else f"à {nombre_fr(km)} km"


def libelle_semestre(code: str, *, precedent: bool = False) -> str:
    annee, numero = int(code[:4]), int(code[-1])
    if precedent:
        annee, numero = (annee, 1) if numero == 2 else (annee - 1, 2)
    return f"S{numero} {annee}"


def euros_arrondis(valeur: float) -> str:
    if valeur <= 0:
        return f"{nombre_fr(valeur)} €"
    chiffres = len(str(int(valeur)))
    return f"{nombre_fr(round(valeur, min(0, 3 - chiffres)))} €"


def nombre_court(valeur: float) -> str:
    return nombre_fr(valeur, 3).rstrip("0").rstrip(",")
