"""Réponse courte `essentiel` : entête, lignes clés et verdict, en texte et en dict."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ingestion.diagnostic.chargements import ligne_chargements
from ingestion.diagnostic.formats import nombre_court
from ingestion.diagnostic.lignes_facture import (
    heures_carbone,
    ligne_accise,
    ligne_acheminement,
    ligne_activite,
    ligne_courbe,
    ligne_entreprises_adresse,
    ligne_locaux,
    part_ca,
    part_va,
    prix_court,
)
from ingestion.diagnostic.lignes_site import lignes_obligations_solaires
from ingestion.diagnostic.modeles import ACTIVITES, SEUIL_AUTORISATION_ZRE_M3H, Diagnostic, Section
from ingestion.diagnostic.requetes import RAYON_FRICHES_KM, RAYON_RESEAUX_CHALEUR_KM
from ingestion.diagnostic.verdict import phrase_verdict, verdict
from ingestion.zones_activite import (
    RAYON_KM as RAYON_ZONES_ACTIVITE_KM,
)


def entete(diagnostic: Diagnostic) -> list[str]:
    d = diagnostic
    t = d.territoire
    lignes = [
        f"Commune {t.commune} ({d.code_insee_commune}) · {d.departement} ({d.code_departement})"
        f" · {d.region} ({d.code_insee_region})",
    ]
    if not t.est_precis:
        lignes.append(f"Attention : adresse résolue à la maille « {t.precision} » seulement.")
    lignes.append(ligne_chargements(d.chargements))
    return lignes


A_SAVOIR = (
    "Le prix ne dépend pas de l'adresse mais de la consommation (péréquation, Corse et"
    " outre-mer compris). Le carbone est la moyenne annuelle du réseau de ce territoire, pas"
    " celle d'un contrat de fourniture."
)


def essentiel(diagnostic: Diagnostic) -> Section:
    d = diagnostic
    section = Section("L'essentiel : prix et carbone à cette adresse", [])
    section.elements.append(("texte", f"Prix moyen : {prix_court(d)}"))
    if d.acheminement is not None:
        section.elements.append(("texte", f"Acheminement : {ligne_acheminement(d.acheminement)}"))
    if d.carbone is None:
        carbone = "non disponible (lancer dbt build)"
    else:
        c = d.carbone
        position = "sous le" if c.est_decarbone else "au-dessus du"
        carbone = (
            f"{'oui' if c.est_decarbone else 'non'} ({c.systeme_electrique}),"
            f" {nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh en cycle de vie, {position}"
            f" seuil de {nombre_court(c.seuil_decarbone_g_co2e_kwh)} gCO₂e/kWh"
        )
    section.elements.append(("texte", f"Électricité décarbonée : {carbone}"))
    if d.carbone is not None and (heures := heures_carbone(d.carbone)):
        section.elements.append(("texte", f"Heure par heure : {heures}"))
    if (deja := d.entreprises_adresse) is not None:
        section.elements.append(("texte", f"Déjà raccordé ici : {ligne_entreprises_adresse(deja)}"))
    if (part := d.part_chiffre_affaires) is not None:
        section.elements.append(("texte", f"Part du chiffre d'affaires : {part_ca(part)}"))
    if (va := d.part_valeur_ajoutee) is not None:
        section.elements.append(("texte", f"Part de la valeur ajoutée : {part_va(va)}"))
    if d.activite is not None:
        section.elements.append(
            (
                "texte",
                f"Activité : {ligne_activite(d.activite, courbe_fournie=d.courbe is not None)}",
            )
        )
    if (accise := ligne_accise(d)) is not None:
        section.elements.append(("texte", f"Accise : {accise}"))
    if d.courbe is not None:
        section.elements.append(("texte", f"Votre courbe de charge : {ligne_courbe(d.courbe)}"))
    if (locaux := d.prix_locaux) is not None:
        ligne = ligne_locaux(locaux, d.surface_m2, d.territoire.commune)
        section.elements.append(("texte", f"Locaux d'activité : {ligne}"))
    section.elements.append(("texte", f"À savoir : {A_SAVOIR}"))
    section.elements.append(("texte", f"Brancher ici ? {phrase_verdict(verdict(d))}"))
    return section


def formater_essentiel(diagnostic: Diagnostic) -> str:
    lignes = [diagnostic.territoire.adresse, *entete(diagnostic), ""]
    lignes += [contenu for _, contenu in essentiel(diagnostic).elements]
    return "\n".join(lignes)


def essentiel_en_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    d = diagnostic
    t = d.territoire
    p = d.prix
    c = d.carbone
    return {
        "adresse": t.adresse,
        "code_insee_commune": d.code_insee_commune,
        "commune": t.commune,
        "code_departement": d.code_departement,
        "code_insee_region": d.code_insee_region,
        "region": d.region,
        "est_zni": d.est_zni,
        "precision_geocodage": t.precision,
        "donnees_chargees": {table: jour.isoformat() for table, jour in d.chargements.items()},
        "prix": None
        if p is None
        else {
            "prix_htva_eur_kwh": p.prix_reference_eur_kwh,
            "code_tranche": p.code_tranche,
            "libelle_tranche": p.libelle_tranche,
            "est_tranche_par_defaut": p.est_tranche_par_defaut,
            "conso_mwh": p.conso_mwh,
            "facture_htva_eur_an": None
            if p.conso_mwh is None
            else p.facture_eur(p.prix_reference_eur_kwh),
            "dernier_semestre": p.dernier_semestre,
            "source": "Eurostat nrg_pc_205",
        },
        "acheminement": None if d.acheminement is None else asdict(d.acheminement),
        "part_chiffre_affaires": None
        if d.part_chiffre_affaires is None
        else asdict(d.part_chiffre_affaires),
        "part_valeur_ajoutee": None
        if d.part_valeur_ajoutee is None
        else asdict(d.part_valeur_ajoutee),
        "activite": None
        if d.activite is None
        else {"code": d.activite, **asdict(ACTIVITES[d.activite])},
        "accise_reduite": None
        if (r := d.accise_reduite) is None
        else {**asdict(r), "economie_eur": r.economie_eur},
        "carbone": None
        if c is None
        else {
            "est_decarbone": c.est_decarbone,
            "intensite_acv_g_co2e_kwh": c.intensite_acv_g_co2e_kwh,
            "seuil_decarbone_g_co2e_kwh": c.seuil_decarbone_g_co2e_kwh,
            "systeme_electrique": c.systeme_electrique,
            "source": f"ADEME Base Carbone, {c.validite_source}",
            "profil_horaire": [
                {"heure_locale": h, "intensite_estimee_g_co2e_kwh": round(i, 1)}
                for h, i in c.profil_horaire
            ],
            "annee_profil": c.annee_profil,
        },
        "a_savoir": A_SAVOIR,
        "entreprises_adresse": None
        if d.entreprises_adresse is None
        else asdict(d.entreprises_adresse),
        "courbe": None if d.courbe is None else asdict(d.courbe),
        "urbanisme": None if d.urbanisme is None else asdict(d.urbanisme),
        "eau": None
        if d.eau is None
        else {
            "niveau_max": d.eau.niveau_max,
            "est_contraignante": d.eau.est_contraignante,
            "zones": [asdict(z) for z in d.eau.zones],
            "source": "VigiEau",
        },
        "zones_repartition_eaux": None
        if d.zones_repartition is None
        else {
            "zones": [asdict(z) for z in d.zones_repartition],
            "seuil_autorisation_m3_h": SEUIL_AUTORISATION_ZRE_M3H,
            "source": "Sandre (référentiel ZRPE)",
        },
        "solaire": None
        if not d.solaire_consulte or d.productible is None
        else {
            "batiment": None if d.batiment is None else asdict(d.batiment),
            "productible": asdict(d.productible),
            "source": "BD TOPO (IGN) et PVGIS (Commission européenne)",
        },
        "obligations_solaires": lignes_obligations_solaires(
            d.batiment, code_departement=d.code_departement, solaire_consulte=d.solaire_consulte
        ),
        "acces_routier": None
        if d.acces_routier is None
        else {
            "acces": [asdict(a) for a in d.acces_routier],
            "source": "BD TOPO et Géoplateforme (IGN)",
        },
        "zones_activite": None
        if d.nb_zones_activite is None
        else {
            "nb_dans_le_rayon": d.nb_zones_activite,
            "rayon_km": RAYON_ZONES_ACTIVITE_KM,
            "plus_proches": [asdict(z) for z in d.zones_activite],
            "source": "BD TOPO (IGN)",
        },
        "friches": None
        if d.nb_friches is None
        else {
            "nb_dans_le_rayon": d.nb_friches,
            "rayon_km": RAYON_FRICHES_KM,
            "plus_proches": [asdict(f) for f in d.friches],
            "source": "Cartofriches (Cerema)",
        },
        "reseaux_chaleur": None
        if d.reseaux_chaleur is None
        else {
            "rayon_km": RAYON_RESEAUX_CHALEUR_KM,
            "plus_proches": [asdict(r) for r in d.reseaux_chaleur],
            "source": "France Chaleur Urbaine",
        },
        "locaux": None
        if d.prix_locaux is None
        else {**asdict(d.prix_locaux), "surface_m2": d.surface_m2, "source": "DVF (DGFiP)"},
        "plans_prevention": None
        if d.plans_prevention is None
        else [asdict(p) for p in d.plans_prevention],
        "verdict": {
            "phrase": phrase_verdict(criteres := verdict(d)),
            "criteres": [asdict(c) for c in criteres],
        },
        "limites": {
            code: d.limites[code]
            for code, present in (
                ("prix_entreprise", True),
                ("carbone_territoire", True),
                ("part_chiffre_affaires", d.part_chiffre_affaires is not None),
                ("acheminement_turpe", d.acheminement is not None),
                ("part_valeur_ajoutee", d.part_valeur_ajoutee is not None),
                ("accise_activite", d.activite is not None),
                ("entreprises_enedis", d.entreprises_adresse is not None),
                ("prix_locaux", d.prix_locaux is not None),
                ("friches", d.nb_friches is not None),
                ("reseaux_chaleur", d.reseaux_chaleur is not None),
                ("acces_routier", d.acces_routier is not None),
                ("solaire", d.solaire_consulte and d.productible is not None),
                ("image_site", d.image_site is not None),
                ("zones_activite", d.nb_zones_activite is not None),
                ("restrictions_eau", d.eau is not None),
                ("zones_repartition_eaux", d.zones_repartition is not None),
                ("zonage_urbanisme", d.urbanisme_consulte and d.urbanisme_erreur is None),
            )
            if present and code in d.limites
        },
    }
