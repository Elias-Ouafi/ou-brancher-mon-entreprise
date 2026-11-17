"""Diagnostic complet `diagnose` : sections détaillées, texte et dict."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from typing import Any

from ingestion.diagnostic.essentiel import entete, essentiel
from ingestion.diagnostic.formats import (
    MOIS,
    distance_fr,
    euros_arrondis,
    gwh_fr,
    libelle_semestre,
    nombre_court,
    nombre_fr,
)
from ingestion.diagnostic.lignes_facture import (
    calendrier_zone,
    heures_carbone,
    ligne_accise,
    ligne_activite,
    ligne_continuite,
    ligne_courbe,
    ligne_entreprises_adresse,
    ligne_entreprises_commune,
    ligne_locaux,
    part_ca,
    part_va,
    pieces_dossier_rte,
)
from ingestion.diagnostic.lignes_site import (
    ligne_acces,
    ligne_eau,
    ligne_friches,
    ligne_image_site,
    ligne_main_oeuvre,
    ligne_recrutements,
    ligne_reseaux_chaleur,
    ligne_solaire,
    ligne_zones_activite,
    ligne_zones_repartition,
    lignes_obligations_solaires,
    lignes_voisinage,
    liste_plans,
    liste_risques,
)
from ingestion.diagnostic.modeles import (
    DOMAINES_TURPE,
    Carbone,
    ContrainteOuvrage,
    Diagnostic,
    PrixEntreprise,
    Section,
)
from ingestion.diagnostic.verdict import ETATS_CRITERE, phrase_verdict, verdict
from ingestion.georisques import (
    Exposition,
)


def sections(diagnostic: Diagnostic) -> list[Section]:
    d = diagnostic
    resultat: list[Section] = []

    def bloc(titre: str) -> Section:
        resultat.append(Section(titre, []))
        return resultat[-1]

    def texte(section: Section, contenu: str) -> None:
        section.elements.append(("texte", contenu))

    def limite(section: Section, indicateur: str) -> None:
        if contenu := d.limites.get(indicateur):
            section.elements.append(("limite", contenu))

    s = bloc(
        "1. Pression industrielle locale"
        f" (sites raccordés au réseau de transport, {d.annee_pression or '?'})"
    )
    if d.commune_a_des_sites:
        minorant = " (minorant : secret statistique)" if d.conso_commune_est_minorant else ""
        texte(
            s,
            f"Commune     : {d.nb_sites_commune} site(s),"
            f" {gwh_fr(d.conso_industrielle_commune_gwh or 0)}/an{minorant}",
        )
    else:
        texte(s, "Commune     : aucun site raccordé au transport → repli sur le département")
    if d.nb_sites_departement:
        rang = (
            f" — rang {d.rang_pression_departement} sur {d.nb_departements_classes} départements"
            if d.rang_pression_departement
            else ""
        )
        texte(
            s,
            f"Département : {d.nb_sites_departement} site(s) dans {d.nb_communes_avec_site}"
            f" commune(s), {gwh_fr(d.conso_industrielle_departement_gwh)}/an{rang}",
        )
    else:
        texte(s, "Département : aucun site raccordé au transport")
    limite(s, "pression_industrielle")
    if (entreprises := d.entreprises_commune) is not None:
        texte(s, f"Entreprises raccordées à Enedis : {ligne_entreprises_commune(entreprises)}")
    if (deja := d.entreprises_adresse) is not None:
        texte(s, f"À cette adresse : {ligne_entreprises_adresse(deja)}")
    if entreprises is not None or deja is not None:
        limite(s, "entreprises_enedis")

    if d.taux_couverture_region is None:
        zni = " (zone non interconnectée)" if d.est_zni else ""
        s = bloc("2. Tension du réseau régional")
        texte(s, f"Aucun équilibre régional publié par RTE pour cette région{zni}.")
    else:
        debut = f"{d.equilibre_mois_debut:%m/%Y}" if d.equilibre_mois_debut else "?"
        fin = f"{d.equilibre_mois_fin:%m/%Y}" if d.equilibre_mois_fin else "?"
        s = bloc(f"2. Tension du réseau régional ({debut} – {fin}, {d.equilibre_nb_mois} mois)")
        texte(
            s,
            f"Production {gwh_fr(d.production_region_gwh or 0)},"
            f" consommation {gwh_fr(d.consommation_region_gwh or 0)}"
            f" : couverture {nombre_fr(d.taux_couverture_region * 100)} %"
            f" → région {_STATUTS.get(d.statut_equilibre or '', '?')}",
        )
        texte(
            s,
            f"Rang de dépendance : {d.rang_dependance_region} sur {d.nb_regions_classees}"
            " (1 = la plus déficitaire)",
        )
        if d.est_zni:
            texte(
                s,
                "Zone non interconnectée : le réseau n'est relié au continent que par des"
                " liaisons limitées, le déficit y pèse bien davantage qu'en métropole.",
            )
    limite(s, "equilibre_regional")
    if d.puissance_a_compenser_mw is not None:
        texte(
            s,
            f"Contraintes d'évacuation (RTE) : {nombre_fr(d.puissance_enr_installee_mw or 0)} MW"
            f" d'EnR installés ou en projet, {nombre_fr(d.puissance_a_compenser_mw)} MW à"
            f" compenser, {nombre_fr(d.energie_non_evacuee_mwh or 0)} MWh non évacués par an",
        )
        limite(s, "contraintes_reseau")
    if d.est_zni and not d.nb_postes_rte:
        texte(
            s,
            "Contraintes prospectives du réseau de transport : non publiées, le réseau de ce"
            " territoire n'est pas exploité par RTE.",
        )
    else:
        if d.nb_contraintes_departement == 0:
            texte(
                s,
                "Contraintes prospectives (RTE, d'ici 3 à 5 ans) : aucune sur les ouvrages reliés"
                " aux postes du département.",
            )
        else:
            frequentes = d.nb_contraintes_frequentes_departement or 0
            texte(
                s,
                f"Contraintes prospectives (RTE, d'ici 3 à 5 ans) : {d.nb_contraintes_departement}"
                " sur des ouvrages reliés aux postes du département, dont"
                f" {frequentes} plus de 75 fois par an.",
            )
            texte(
                s,
                "Les plus fréquentes : " + " ; ".join(_contrainte(c) for c in d.contraintes),
            )
        limite(s, "contraintes_prospectives")

    s = bloc("3. Raccordement")
    if d.seuil is not None:
        texte(
            s,
            f"Puissance {nombre_court(d.seuil.puissance_mw)} MW → {d.seuil.domaine_tension},"
            f" {d.seuil.gestionnaire} : {d.seuil.commentaire}",
        )
        limite(s, "seuil_raccordement")
        if (a := d.acheminement) is not None:
            texte(
                s,
                f"Acheminement, détail : {euros_arrondis(a.puissance_eur)} pour la puissance"
                f" souscrite, {euros_arrondis(a.energie_eur)} pour l'énergie,"
                f" {euros_arrondis(a.gestion_comptage_eur)} de gestion et comptage par an",
            )
        elif d.seuil.domaine_turpe in DOMAINES_TURPE:
            texte(s, "Acheminement : préciser --conso (MWh par an) pour le chiffrer.")
        if d.seuil.domaine_turpe != "bt_inf36":
            limite(s, "acheminement_turpe")
    else:
        texte(s, "Préciser --puissance (en MW) pour connaître le domaine de tension.")
    if (c := d.continuite) is not None:
        texte(s, ligne_continuite(c))
        limite(s, "continuite_alimentation")
    elif "enedis_continuite_alimentation" in d.chargements:
        texte(
            s,
            "Qualité de fourniture : non publiée pour ce département (réseau de distribution"
            " hors Enedis).",
        )
    if not d.nb_postes_rte:
        texte(
            s,
            "Postes RTE dans le département : aucun"
            + (" (réseau de ce territoire non exploité par RTE)." if d.est_zni else "."),
        )
    else:
        projet = f" ; {d.nb_postes_en_projet} en projet" if d.nb_postes_en_projet else ""
        texte(
            s,
            f"Postes RTE en exploitation dans le département : {d.nb_postes_rte}, dont"
            f" {d.nb_postes_htb1 or 0} en HTB1 (63 à 150 kV), {d.nb_postes_htb2 or 0} en HTB2"
            f" (225 kV) et {d.nb_postes_htb3 or 0} en HTB3 (400 kV){projet}",
        )
        limite(s, "postes_rte")
    if d.est_zni:
        texte(
            s,
            "Zones industrielles prioritaires de RTE : aucune, le réseau de ce territoire n'est"
            " pas exploité par RTE.",
        )
    elif d.zone_rte is not None:
        z = d.zone_rte
        texte(
            s,
            f"Zone industrielle prioritaire de RTE la plus proche : {z.zone} ({z.priorite}),"
            f" {distance_fr(z.distance_km)} ({z.point_reference}) : {calendrier_zone(z)} ;"
            f" ouvrages prévus : {z.ouvrages}",
        )
        if (p3 := d.zone_rte_p3) is not None:
            texte(
                s,
                f"Plus proche encore, {p3.zone} ({distance_fr(p3.distance_km)}) : exemple de zone"
                " où RTE pourrait accélérer le réseau (P3), sans date et sous engagement"
                " financier des industriels.",
            )
        limite(s, "zones_raccordement_rte")
    if d.seuil is not None and "RTE" in d.seuil.gestionnaire:
        for ligne in pieces_dossier_rte(d.seuil.puissance_mw):
            texte(s, ligne)
    if d.site_cle_en_main is not None:
        site = d.site_cle_en_main
        texte(
            s,
            f"Site industriel clé en main (France 2030) le plus proche : {site.commune},"
            f" {distance_fr(site.distance_km)} ; annoncé prêt en {site.maturite_site}",
        )
        limite(s, "sites_cles_en_main")
    else:
        texte(s, "Site industriel clé en main (France 2030) : aucun dans ce territoire.")
    limite(s, "capacite_raccordement")

    s = bloc("4. Prix de l'électricité pour l'entreprise (identique sur tout le territoire)")
    if d.prix is None and d.conso_mwh is not None:
        texte(
            s,
            f"Aucun prix publié par Eurostat pour la tranche de {nombre_court(d.conso_mwh)}"
            " MWh/an.",
        )
    elif d.prix is None:
        texte(s, "Aucun prix Eurostat chargé : lancer la commande prix, puis dbt build.")
    else:
        p = d.prix
        if p.conso_mwh is None:
            texte(
                s,
                f"Consommation non précisée → tranche {p.code_tranche} ({p.libelle_tranche}),"
                " celle des TPE et des commerces, prix moyen constaté par Eurostat. Préciser"
                " --conso (consommation annuelle en MWh, lue sur la facture) pour la tranche de"
                " l'entreprise et sa facture.",
            )
        else:
            texte(
                s,
                f"Consommation {nombre_court(p.conso_mwh)} MWh/an → tranche {p.code_tranche}"
                f" ({p.libelle_tranche}), prix moyen constaté par Eurostat",
            )
        taxes = (
            f", dont {nombre_fr(p.taxes_reference_eur_kwh, 4)} €/kWh de taxes non récupérables"
            if p.taxes_reference_eur_kwh is not None
            else ""
        )
        facture = (
            ""
            if p.conso_mwh is None
            else f" → facture d'environ {euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))}"
            " par an"
        )
        texte(
            s,
            f"12 derniers mois ({libelle_semestre(p.dernier_semestre, precedent=True)} –"
            f" {libelle_semestre(p.dernier_semestre)}) :"
            f" {nombre_fr(p.prix_reference_eur_kwh, 4)} €/kWh"
            f" HTVA{taxes}{facture}",
        )
        texte(
            s,
            f"Repères depuis {libelle_semestre(p.premier_semestre)} : moyenne sur 5 ans"
            f" {nombre_fr(p.prix_moyen_5_ans_eur_kwh, 4)} €/kWh, plus bas"
            f" {nombre_fr(p.prix_min_eur_kwh, 4)} ({libelle_semestre(p.semestre_min)}), plus haut"
            f" {nombre_fr(p.prix_max_eur_kwh, 4)} ({libelle_semestre(p.semestre_max)})",
        )
        bas, centre, haut = (
            p.prix_3_ans_bas_eur_kwh,
            p.prix_3_ans_central_eur_kwh,
            p.prix_3_ans_haut_eur_kwh,
        )
        if bas is not None and centre is not None and haut is not None:
            factures = (
                ""
                if p.conso_mwh is None
                else f" → facture de {euros_arrondis(p.facture_eur(bas))} à"
                f" {euros_arrondis(p.facture_eur(haut))} par an"
                f" ({euros_arrondis(p.facture_eur(centre))} au centre)"
            )
            statut = "" if p.est_fourchette_validee else ", fourchette indicative"
            texte(
                s,
                f"Prix moyen des 3 prochaines années{statut}, d'après l'historique de la tranche"
                " et les erreurs passées de la méthode :"
                f" de {nombre_fr(bas, 4)} à {nombre_fr(haut, 4)} €/kWh, {nombre_fr(centre, 4)} au"
                f" centre{factures}",
            )
            texte(s, _controle_sur_le_passe(p))
        else:
            texte(
                s,
                "Historique trop court pour cette tranche : pas de scénario à 3 ans.",
            )
        if d.est_zni:
            texte(
                s,
                "En Corse et outre-mer, les tarifs réglementés sont alignés sur ceux du continent"
                " (péréquation) : ce prix moyen national reste le repère.",
            )
        limite(s, "prix_entreprise")
        if (part := d.part_chiffre_affaires) is not None:
            texte(s, f"Facture rapportée au chiffre d'affaires : {part_ca(part)}")
            limite(s, "part_chiffre_affaires")
        if (va := d.part_valeur_ajoutee) is not None:
            texte(s, f"Part de la valeur ajoutée : {part_va(va)}")
            limite(s, "part_valeur_ajoutee")
        if d.activite is not None:
            texte(
                s, f"Activité : {ligne_activite(d.activite, courbe_fournie=d.courbe is not None)}"
            )
        if (accise := ligne_accise(d)) is not None:
            texte(s, f"Accise : {accise}")
        if d.activite is not None:
            limite(s, "accise_activite")

    s = bloc("5. Carbone du réseau et coût horaire d'un profil type")
    if d.carbone is not None:
        texte(s, _verdict_carbone(d.carbone))
        if heures := heures_carbone(d.carbone):
            texte(s, f"Heure par heure : {heures}")
        limite(s, "carbone_territoire")
    if d.courbe is not None:
        texte(s, f"Votre courbe de charge : {ligne_courbe(d.courbe)}")
    if d.cout is None:
        texte(s, "Aucun mois complet et tarifé : charger les données nationales récentes.")
    else:
        c = d.cout
        texte(
            s,
            f"{c.libelle}, {MOIS[c.mois.month - 1]} {c.mois.year},"
            " option Heures Creuses du tarif bleu non résidentiel",
        )
        texte(
            s,
            f"{nombre_fr(c.conso_kwh)} kWh, dont {nombre_fr(c.part_hc * 100)} % en heures creuses :"
            f" {nombre_fr(c.cout_htva_eur)} € HTVA, soit {nombre_fr(c.prix_moyen_htva_eur_kwh, 4)}"
            f" €/kWh ; {nombre_fr(c.intensite_moyenne_g_kwh)} gCO₂/kWh en moyenne",
        )
        texte(
            s,
            f"Décaler {nombre_fr(c.kwh_decales)} kWh flexibles vers les heures creuses :"
            f" {nombre_fr(c.gain_decalage_eur)} € économisés, {_co2_evite(c.co2_evite_kg)}",
        )
        if d.est_zni:
            texte(
                s,
                "Barème de métropole continentale : la Corse et l'outre-mer ont leurs propres"
                " barèmes réglementés.",
            )
    limite(s, "cout_profil")

    s = bloc("6. Risques et contraintes du site : Géorisques et eau (arrêtés de restriction)")
    if d.plans_prevention is not None:
        texte(
            s,
            "Plans de prévention en vigueur dans la commune : "
            + (liste_plans(d.plans_prevention) or "aucun"),
        )
        limite(s, "plans_prevention")
    if d.risques_erreur is not None:
        texte(
            s,
            "Géorisques n'a pas répondu, même après plusieurs tentatives, et l'export GASPAR"
            " n'est pas chargé (lancer refresh) : risques non évalués. À consulter sur"
            " georisques.gouv.fr avant tout engagement.",
        )
    elif d.risques is None:
        texte(s, "Non consulté (option --sans-risques).")
    elif d.risques.maille == "commune":
        r = d.risques
        source = f"l'export GASPAR du {r.millesime:%d/%m/%Y}" if r.millesime else "l'API Géorisques"
        texte(
            s,
            "Rapport à l'adresse indisponible (Géorisques n'a pas répondu) : risques recensés"
            f" dans la commune d'après {source}, sans évaluation à l'adresse.",
        )
        for famille, nom in (("naturel", "Naturels"), ("technologique", "Technologiques")):
            recenses = r.selon(Exposition.INCONNU, famille)
            texte(
                s,
                f"{nom}, recensés dans la commune : {liste_risques(recenses) or 'aucun'}",
            )
        limite(s, "risques_site")
    else:
        r = d.risques
        if not d.territoire.est_precis:
            texte(
                s,
                "Adresse résolue à la commune seulement : le statut « à l'adresse » ne vaut que"
                " pour le point retenu par le géocodage.",
            )
        for famille, nom in (("naturel", "Naturels"), ("technologique", "Technologiques")):
            exposes = r.selon(Exposition.EXPOSE, famille)
            texte(s, f"{nom}, à l'adresse : {liste_risques(exposes) or 'aucun recensé'}")
        if inconnus := r.selon(Exposition.INCONNU):
            texte(
                s,
                "Présents dans la commune, non évalués à l'adresse : " + liste_risques(inconnus),
            )
        if ecartes := r.selon(Exposition.NON_EXPOSE):
            texte(
                s,
                "Présents dans la commune, mais l'adresse n'est pas concernée : "
                + liste_risques(ecartes),
            )
        if r.url:
            texte(s, f"Rapport officiel : {r.url}")
        limite(s, "risques_site")
    if d.risques is not None and any(r.code == "seisme" for r in d.risques.risques):
        limite(s, "zonage_sismique")
    if d.voisinage is not None:
        for ligne in lignes_voisinage(d.voisinage):
            texte(s, ligne)
        limite(s, "voisinage_site")
    elif d.voisinage_erreur is not None:
        texte(s, f"Voisinage (argiles, Seveso, sols pollués) non consulté : {d.voisinage_erreur}")
    if d.eau is not None:
        texte(s, f"Eau : {ligne_eau(d.eau)}")
        limite(s, "restrictions_eau")
    elif d.eau_erreur is not None:
        texte(s, f"Restrictions d'eau non consultées : {d.eau_erreur}")
    if d.zones_repartition is not None:
        texte(s, f"Tension sur la ressource : {ligne_zones_repartition(d.zones_repartition)}")
        limite(s, "zones_repartition_eaux")
    s = bloc("7. Foncier, desserte, main-d'œuvre et prix des locaux d'activité (DVF)")
    if d.solaire_consulte and d.productible is not None:
        texte(s, f"Bâtiment et solaire : {ligne_solaire(d.batiment, d.productible)}")
        limite(s, "solaire")
    elif d.solaire_erreur is not None:
        texte(s, f"Bâtiment et solaire non évalués : {d.solaire_erreur}")
    for obligation in lignes_obligations_solaires(
        d.batiment,
        code_departement=d.code_departement,
        solaire_consulte=d.solaire_consulte,
    ):
        texte(s, f"Obligation solaire : {obligation}")
    limite(s, "obligations_solaires")
    if d.image_site is not None:
        texte(s, f"Image du site : {ligne_image_site(d.image_site)}")
        limite(s, "image_site")
    elif d.image_site_erreur is not None:
        texte(s, f"Image du site non consultée : {d.image_site_erreur}")
    if d.acces_routier is not None:
        texte(
            s,
            f"Accès routier (voiture, pas de profil poids lourd) : {ligne_acces(d.acces_routier)}",
        )
        limite(s, "acces_routier")
    elif d.acces_routier_erreur is not None:
        texte(s, f"Accès routier non évalué : {d.acces_routier_erreur}")
    if d.main_oeuvre is not None:
        texte(s, f"Main-d'œuvre : {ligne_main_oeuvre(d.main_oeuvre)}")
        limite(s, "main_oeuvre")
    elif d.main_oeuvre_erreur is not None:
        texte(s, f"Main-d'œuvre non évaluée : {d.main_oeuvre_erreur}")
    if d.recrutements is not None:
        texte(s, f"Recrutements : {ligne_recrutements(d.recrutements)}")
        limite(s, "recrutements_techniques")
    if d.nb_zones_activite is not None:
        texte(
            s, f"Zones d'activité : {ligne_zones_activite(d.nb_zones_activite, d.zones_activite)}"
        )
        limite(s, "zones_activite")
    elif d.zones_activite_erreur is not None:
        texte(s, f"Zones d'activité non consultées : {d.zones_activite_erreur}")
    if d.nb_friches is not None:
        texte(s, f"Friches : {ligne_friches(d.nb_friches, d.friches)}")
        limite(s, "friches")
    if d.reseaux_chaleur is not None:
        texte(s, f"Réseaux de chaleur : {ligne_reseaux_chaleur(d.reseaux_chaleur)}")
        limite(s, "reseaux_chaleur")
    if d.prix_locaux is not None:
        texte(s, ligne_locaux(d.prix_locaux, d.surface_m2, d.territoire.commune))
        limite(s, "prix_locaux")
    elif "ventes_locaux" in d.chargements:
        texte(s, "Aucune vente de local d'activité recensée dans la commune ni son département.")
    else:
        texte(s, "Non chargé : lancer refresh pour récupérer les ventes DVF.")
    s = bloc("8. Brancher ici ? Les cinq critères")
    criteres = verdict(d)
    for c in criteres:
        texte(s, f"{c.nom} ({ETATS_CRITERE[c.etat]}) : {c.valeur}. {c.detail}")
    texte(s, phrase_verdict(criteres))
    return resultat


def formater_diagnostic(diagnostic: Diagnostic) -> str:
    lignes = [f"DIAGNOSTIC D'IMPLANTATION — {diagnostic.territoire.adresse}"]
    lignes += entete(diagnostic)
    for section in [essentiel(diagnostic), *sections(diagnostic)]:
        lignes += ["", section.titre]
        lignes += [
            f"   ↳ Limite : {contenu}" if nature == "limite" else f"   {contenu}"
            for nature, contenu in section.elements
        ]
    return "\n".join(lignes)


def diagnostic_en_dict(diagnostic: Diagnostic) -> dict[str, Any]:

    def convertir(valeur: Any) -> Any:
        if isinstance(valeur, date):
            return valeur.isoformat()
        if isinstance(valeur, dict):
            return {cle: convertir(v) for cle, v in valeur.items()}
        if isinstance(valeur, list):
            return [convertir(v) for v in valeur]
        return valeur

    contenu = asdict(diagnostic)
    contenu["commune_a_des_sites"] = diagnostic.commune_a_des_sites
    part = diagnostic.part_chiffre_affaires
    contenu["part_chiffre_affaires"] = None if part is None else asdict(part)
    va = diagnostic.part_valeur_ajoutee
    contenu["part_valeur_ajoutee"] = None if va is None else asdict(va)
    r = diagnostic.accise_reduite
    contenu["accise_reduite"] = None if r is None else {**asdict(r), "economie_eur": r.economie_eur}
    criteres = verdict(diagnostic)
    contenu["verdict"] = {
        "phrase": phrase_verdict(criteres),
        "criteres": [asdict(c) for c in criteres],
    }
    return convertir(contenu)


def _verdict_carbone(c: Carbone) -> str:
    verdict = "décarbonée" if c.est_decarbone else "non décarbonée"
    phrase = (
        f"Électricité du réseau {verdict} ({c.systeme_electrique}) :"
        f" {nombre_court(c.intensite_acv_g_co2e_kwh)} gCO₂e/kWh en cycle de vie (ADEME,"
        f" {c.validite_source}), {'sous le' if c.est_decarbone else 'au-dessus du'} seuil de"
        f" {nombre_court(c.seuil_decarbone_g_co2e_kwh)} gCO₂e/kWh de la taxonomie européenne"
    )
    if c.intensite_directe_rte_g_co2_kwh is not None and c.rte_jour_fin is not None:
        phrase += (
            f" ; émissions directes observées par RTE sur 12 mois, jusqu'au"
            f" {c.rte_jour_fin:%d/%m/%Y} : {nombre_fr(c.intensite_directe_rte_g_co2_kwh)} gCO₂/kWh"
        )
    return phrase


# Le décalage vers la nuit peut augmenter les émissions en été (solaire de midi)
def _co2_evite(kg: float) -> str:
    if kg >= 0:
        return f"{nombre_fr(kg, 1)} kgCO₂ évités"
    return (
        f"mais {nombre_fr(-kg, 1)} kgCO₂ émis en plus : les heures creuses de nuit sont"
        " plus carbonées que la mi-journée solaire"
    )


_STATUTS = {"excedentaire": "excédentaire", "deficitaire": "déficitaire"}


def _controle_sur_le_passe(p: PrixEntreprise) -> str:
    if not p.nb_periodes_controlees or p.premiere_origine_controlee is None:
        return "Historique trop court pour contrôler la méthode sur le passé."
    total = p.nb_periodes_controlees
    dans = p.nb_dans_eventail or 0
    return (
        f"Contrôle sur le passé : appliquée à chaque semestre de"
        f" {libelle_semestre(p.premiere_origine_controlee)} à"
        f" {libelle_semestre(p.derniere_origine_controlee or p.premiere_origine_controlee)},"
        f" la méthode a encadré le prix moyen réel des 3 années suivantes {dans} fois sur"
        f" {total} ({nombre_fr(dans / total * 100)} %, contre 80 % pour une méthode bien"
        f" calibrée) ; il a dépassé le scénario haut {p.nb_au_dessus or 0} fois et est passé"
        f" sous le scénario bas {p.nb_en_dessous or 0} fois. Ces périodes de 3 ans se"
        " chevauchent : ce ne sont pas des essais indépendants. Leurs écarts font désormais"
        " partie des erreurs qui calibrent la fourchette affichée."
        + (
            ""
            if p.est_fourchette_validee
            else f" Avec {total} périodes seulement, souvent prises dans une même crise, la"
            " méthode n'est pas validée : la fourchette est un ordre de grandeur."
        )
    )


def _contrainte(c: ContrainteOuvrage) -> str:
    details = [c.occurrence[:1].lower() + c.occurrence[1:], duree_fr(c.duree)]
    details.append(f"pérennité {c.perennite}")
    if c.est_en_journee:
        details.append("en journée")
    return f"{c.nom_de_l_ouvrage} ({', '.join(d for d in details if d)})"


def duree_fr(duree: str) -> str:
    bornes = re.fullmatch(r"\](\d+)h-(\d+)h\]", duree.strip())
    if bornes is None:
        return f"durée {duree}" if duree else ""
    debut, fin = bornes.groups()
    return f"épisodes de moins de {fin} h" if debut == "0" else f"épisodes de {debut} à {fin} h"
