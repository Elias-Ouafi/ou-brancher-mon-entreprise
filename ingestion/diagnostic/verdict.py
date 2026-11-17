"""Verdict « brancher ici ? » : cinq critères (raccordement, risques, carbone, facture, foncier)."""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.diagnostic.formats import (
    distance_fr,
    euros_arrondis,
    nombre_court,
    nombre_fr,
    pourcentage,
)
from ingestion.diagnostic.lignes_facture import heures_carbone, prix_court
from ingestion.diagnostic.lignes_site import (
    liste_plans,
    risques_recenses,
    voisinage_court,
)
from ingestion.diagnostic.modeles import LIBELLES_ACCISE, Diagnostic
from ingestion.diagnostic.requetes import RAYON_FRICHES_KM

# Au-delà, une zone prioritaire RTE n'aide plus un projet à cette adresse
DISTANCE_ZONE_UTILE_KM = 50


@dataclass(frozen=True, slots=True)
class Critere:
    nom: str
    # `ok`, `attention` (à vérifier avant de s'engager), `inconnu` (donnée manquante), `info`
    etat: str
    valeur: str
    detail: str


ETATS_CRITERE = {
    "ok": "favorable",
    "attention": "à vérifier",
    "inconnu": "non évalué",
    "info": "pour information",
}


def verdict(diagnostic: Diagnostic) -> list[Critere]:
    return [
        _critere_raccordement(diagnostic),
        _critere_risques(diagnostic),
        _critere_carbone(diagnostic),
        _critere_facture(diagnostic),
        _critere_foncier(diagnostic),
    ]


def _critere_raccordement(d: Diagnostic) -> Critere:
    if d.seuil is None:
        return Critere(
            "Raccordement",
            "inconnu",
            "puissance non précisée",
            "Préciser --puissance (en MW) pour savoir qui raccorde : Enedis ou RTE.",
        )
    valeur = f"{d.seuil.domaine_tension}, {d.seuil.gestionnaire}"
    if d.seuil.est_zni and (d.seuil.domaine_turpe == "htb1" or "grise" in d.seuil.domaine_tension):
        return Critere(
            "Raccordement",
            "attention",
            valeur,
            f"Réseau insulaire géré par {d.seuil.gestionnaire}, sans RTE : à cette puissance, son"
            " étude fixe la capacité disponible et le délai ; elle est indispensable.",
        )
    if "RTE" not in d.seuil.gestionnaire:
        return Critere(
            "Raccordement",
            "ok",
            valeur,
            "Raccordement courant au réseau de distribution ; délai et coût fixés par l'étude"
            " du gestionnaire.",
        )
    zone = d.zone_rte
    if zone is not None and zone.distance_km <= DISTANCE_ZONE_UTILE_KM:
        annee = f" dès {zone.annee_capacite}" if zone.annee_capacite else ""
        detail = (
            f"Zone prioritaire RTE {zone.zone} ({zone.priorite}) {distance_fr(zone.distance_km)} :"
            f" capacité supplémentaire prévue{annee}. Étude RTE indispensable."
        )
    else:
        detail = (
            f"Aucune zone prioritaire RTE à moins de {DISTANCE_ZONE_UTILE_KM} km : délais de 2 à"
            " 7 ans annoncés, étude RTE indispensable."
        )
    return Critere("Raccordement", "attention", valeur, detail)


# Plans cités dans le verdict ; le diagnostic complet les donne tous
NB_PLANS_CITES = 3


# Risques : à vérifier seulement quand un plan de prévention en vigueur couvre la commune, car son
# règlement peut interdire ou conditionner une construction ; un risque seulement recensé informe
def _critere_risques(d: Diagnostic) -> Critere:
    plans = d.plans_prevention
    if plans is None:
        return Critere(
            "Risques",
            "inconnu",
            "plans de prévention non chargés",
            "Lancer refresh pour charger l'export GASPAR.",
        )
    if plans:
        return Critere(
            "Risques",
            "attention",
            f"{len(plans)} plan{'s' if len(plans) > 1 else ''} de prévention dans la commune",
            f"{liste_plans(plans, NB_PLANS_CITES)}. Le règlement du plan peut interdire ou"
            " conditionner la construction selon la zone : le consulter en mairie avant de"
            f" s'engager.{voisinage_court(d.voisinage)}",
        )
    return Critere(
        "Risques",
        "info",
        "aucun plan de prévention dans la commune",
        risques_recenses(d.risques) + voisinage_court(d.voisinage),
    )


def _critere_carbone(d: Diagnostic) -> Critere:
    c = d.carbone
    if c is None:
        return Critere("Carbone", "inconnu", "non disponible", "Lancer refresh.")
    detail = (
        f"{c.systeme_electrique} : {'sous le' if c.est_decarbone else 'au-dessus du'} seuil de"
        f" {nombre_court(c.seuil_decarbone_g_co2e_kwh)} gCO₂e/kWh de la taxonomie européenne."
    )
    if heures := heures_carbone(c):
        detail += f" {heures[0].upper()}{heures[1:]}."
    return Critere(
        "Carbone",
        "ok" if c.est_decarbone else "attention",
        f"{nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh",
        detail,
    )


def _critere_facture(d: Diagnostic) -> Critere:
    p = d.prix
    if p is None:
        return Critere("Facture", "inconnu", "prix non disponible", prix_court(d))
    prix = f"{nombre_fr(p.prix_reference_eur_kwh, 4)} €/kWh HTVA ({p.code_tranche})"
    if p.conso_mwh is None:
        return Critere(
            "Facture", "info", prix, "Préciser --conso (MWh par an) pour la facture annuelle."
        )
    detail = f"{prix}, identique partout en France."
    if (a := d.acheminement) is not None:
        detail += f" Dont acheminement {nombre_fr(a.prix_eur_mwh, 1)} €/MWh (TURPE 7)."
    if (part := d.part_chiffre_affaires) is not None:
        detail += f" {pourcentage(part.part)} du chiffre d'affaires."
    if (r := d.accise_reduite) is not None:
        detail += (
            f" Accise réduite possible ({LIBELLES_ACCISE[r.categorie]}) : environ"
            f" {euros_arrondis(r.economie_eur)} de moins par an, sous conditions."
        )
    return Critere(
        "Facture",
        "info",
        f"{euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))} HTVA par an",
        detail,
    )


def _critere_foncier(d: Diagnostic) -> Critere:
    site = d.site_cle_en_main
    if site is None:
        valeur, detail = "aucun site clé en main", "Dans ce territoire."
    else:
        valeur = f"site clé en main {distance_fr(site.distance_km)}"
        detail = f"{site.commune}, annoncé prêt en {site.maturite_site}."
    etat = "info"
    if d.zones_activite and d.zones_activite[0].contient_adresse:
        detail += f" Adresse dans la zone d'activité {d.zones_activite[0].nom}."
    if d.nb_friches is not None:
        detail += f" {d.nb_friches} friche(s) de 1 ha ou plus à moins de {RAYON_FRICHES_KM} km."
    return Critere("Foncier", etat, valeur, detail)


def phrase_verdict(criteres: list[Critere]) -> str:
    a_verifier = [c.nom.lower() for c in criteres if c.etat == "attention"]
    inconnus = [c.nom.lower() for c in criteres if c.etat == "inconnu"]
    if a_verifier:
        phrase = "À vérifier avant de s'engager : " + ", ".join(a_verifier)
    else:
        phrase = "Rien de bloquant dans les données publiques"
    if inconnus:
        phrase += " (non évalué : " + ", ".join(inconnus) + ")"
    return phrase + "."
