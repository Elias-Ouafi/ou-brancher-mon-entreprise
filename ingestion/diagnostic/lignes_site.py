"""Lignes de sortie sur le site : risques, foncier, eau, solaire, main-d'œuvre, desserte."""

from __future__ import annotations

from datetime import date

from ingestion.acces_routier import Acces
from ingestion.diagnostic.formats import MOIS, distance_fr, nombre_fr, pourcentage
from ingestion.diagnostic.modeles import (
    SEUIL_AUTORISATION_ZRE_M3H,
    Friche,
    PlanPrevention,
    ReseauChaleur,
    ZoneRepartition,
)
from ingestion.diagnostic.requetes import RAYON_FRICHES_KM, RAYON_RESEAUX_CHALEUR_KM
from ingestion.eau import LIBELLES_NIVEAU, LIBELLES_TYPE, SituationEau
from ingestion.georisques import (
    RAYON_ICPE_KM,
    RAYON_SEVESO_KM,
    RAYON_SOLS_M,
    Exposition,
    Risque,
    RisquesSite,
    Voisinage,
)
from ingestion.image_site import ImageSite
from ingestion.main_oeuvre import (
    MainOeuvre,
    Recrutements,
)
from ingestion.solaire import (
    ECHEANCE_EXISTANTS,
    ECHEANCE_GRAND_PARKING,
    ECHEANCE_PARKING,
    EMPRISE_TOITURE_M2,
    PERTES_PCT,
    SEUIL_GRAND_PARKING_M2,
    SEUIL_PARKING_DEFAUT_M2,
    SEUIL_PARKING_M2,
    USAGES_SOUMIS,
    Batiment,
    Productible,
    part_toiture,
)
from ingestion.urbanisme import ZoneUrbanisme
from ingestion.zones_activite import (
    RAYON_KM as RAYON_ZONES_ACTIVITE_KM,
)
from ingestion.zones_activite import ZoneActivite


# Le voisinage informe sans changer l'état du critère : un Seveso seuil haut impose un plan de
# prévention technologique, que les plans de la commune donnent déjà
def voisinage_court(v: Voisinage | None) -> str:
    if v is None:
        return ""
    if v.seveso:
        s = v.seveso[0]
        phrase = f" Seveso le plus proche : {s.nom} ({s.seveso}) {distance_fr(s.distance_km)}."
    else:
        phrase = f" Aucun établissement Seveso en exploitation à moins de {RAYON_SEVESO_KM} km."
    if v.sis:
        phrase += (
            f" Secteurs d'information sur les sols à moins de {RAYON_SOLS_M} m : {len(v.sis)}."
        )
    return phrase


def lignes_voisinage(v: Voisinage) -> list[str]:
    argiles = (
        f"Argiles (retrait-gonflement) à l'adresse : {v.argiles.lower()}"
        if v.argiles
        else "Argiles (retrait-gonflement) : territoire non couvert par la carte d'exposition"
    )
    cavites = f"Cavités souterraines à moins de {RAYON_SOLS_M} m : {v.nb_cavites or 'aucune'}"
    seveso = f"Seveso en exploitation à moins de {RAYON_SEVESO_KM} km : " + (
        "; ".join(
            f"{s.nom} ({s.seveso}, {s.commune}) {distance_fr(s.distance_km)}" for s in v.seveso
        )
        or "aucun"
    )
    icpe = (
        f"Installations classées en exploitation à moins de {RAYON_ICPE_KM} km : {v.nb_icpe}"
        f" (dont {v.nb_icpe_autorisation} sous autorisation, le reste sous enregistrement)"
    )
    sols = [
        f"secteurs d'information sur les sols {len(v.sis)}"
        + (f" ({', '.join(v.sis)})" if v.sis else ""),
        f"servitudes d'utilité publique {v.nb_sup}",
        f"sites pollués en cours d'instruction {v.nb_instructions_en_cours}",
        f"anciens sites industriels ou de service (CASIAS) {v.nb_casias}",
    ]
    return [
        argiles,
        cavites,
        seveso,
        icpe,
        f"Sols pollués à moins de {RAYON_SOLS_M} m : " + " ; ".join(sols),
    ]


def risques_recenses(r: RisquesSite | None) -> str:
    if r is None:
        return "Risques recensés non consultés : à vérifier sur georisques.gouv.fr."
    if r.maille == "adresse":
        exposes = r.selon(Exposition.EXPOSE)
        if not exposes:
            return "Aucun risque recensé à l'adresse (rapport Géorisques)."
        return f"Recensés à l'adresse : {liste_risques(exposes)}."
    recenses = r.selon(Exposition.INCONNU)
    if not recenses:
        return "Aucun risque recensé dans la commune (export GASPAR)."
    return f"Recensés dans la commune, sans plan de prévention : {liste_risques(recenses)}."


# « PPRI de Paris (inondation, approuvé en 2007) », les premiers seulement si `limite`
def liste_plans(plans: list[PlanPrevention], limite: int | None = None) -> str:
    cites = plans if limite is None else plans[:limite]
    texte = ", ".join(_plan_court(p) for p in cites)
    if (reste := len(plans) - len(cites)) > 0:
        texte += f" et {reste} autre{'s' if reste > 1 else ''}"
    return texte


def _plan_court(p: PlanPrevention) -> str:
    date_etat = p.date_approbation if p.etat == "approuvé" else p.date_prescription
    etat = f"{p.etat} en {date_etat.year}" if date_etat else p.etat
    familles = f"{p.risques.lower()}, " if p.risques else ""
    return f"{p.libelle_plan} ({familles}{etat})"


def ligne_urbanisme(zone: ZoneUrbanisme) -> str:
    nom = f"{zone.nom_type} {zone.libelle}".strip()
    if zone.libelle_long and zone.libelle_long != zone.libelle:
        nom += f" ({zone.libelle_long})"
    phrase = f"{nom}, document {zone.document or 'non identifié'}"
    if zone.date_validite:
        phrase += f" validé le {zone.date_validite}"
    if zone.est_inconstructible_activite:
        phrase += (
            " : hors exception locale (STECAL), l'activité industrielle ou logistique n'y est"
            " pas permise"
        )
    else:
        phrase += " : destination des constructions à confirmer au règlement de la zone"
    if zone.url_reglement:
        phrase += f" ({zone.url_reglement})"
    return phrase


def ligne_zones_repartition(zones: list[ZoneRepartition]) -> str:
    if not zones:
        return (
            "hors zone de répartition des eaux : aucun déficit structurel de la ressource reconnu"
            " par arrêté à cette adresse"
        )
    noms = ", ".join(
        f"{z.nom} ({z.ressource})" if z.nom else f"zone sans nom publié ({z.ressource})"
        for z in zones
    )
    return (
        "en zone de répartition des eaux, ressource reconnue insuffisante face aux besoins :"
        f" {noms} ; un prélèvement non domestique dans la ressource visée est soumis à"
        " autorisation dès"
        f" {SEUIL_AUTORISATION_ZRE_M3H} m³/h, à déclaration en dessous (R214-1, rubrique 1.3.1.0)"
    )


def ligne_main_oeuvre(m: MainOeuvre) -> str:
    if not m.nb_communes:
        return (
            f"non disponible : aucune commune recensée par l'Insee à moins de {m.minutes} min"
            " en voiture"
        )
    communes = "commune" if m.nb_communes == 1 else f"{m.nb_communes} communes"
    return (
        f"{nombre_fr(round(m.nb_superieur, -2))} diplômés du supérieur et"
        f" {nombre_fr(round(m.nb_cap_bep_bac, -2))} titulaires d'un CAP-BEP ou d'un bac, parmi"
        f" {nombre_fr(round(m.nb_15_ans_plus, -2))} habitants de 15 ans ou plus ({communes} à"
        f" moins de {m.minutes} min en voiture, recensement {m.millesime})"
    )


def ligne_recrutements(r: Recrutements) -> str:
    if not r.projets:
        ligne = (
            f"aucun projet de recrutement déclaré en {r.annee} pour les métiers de l'électricité,"
            " du froid et de la maintenance dans le département"
        )
    else:
        difficiles = (
            ""
            if r.part_difficiles is None
            else f", dont {pourcentage(r.part_difficiles)} jugés difficiles par les employeurs"
        )
        ligne = (
            f"{nombre_fr(r.projets)} projets de recrutement en {r.annee} pour les métiers de"
            f" l'électricité, du froid et de la maintenance dans le département{difficiles}"
        )
    if r.nb_lignes_secretes:
        ligne += (
            f" ; {nombre_fr(r.nb_lignes_secretes)} des {nombre_fr(r.nb_lignes)} croisements"
            " bassin-métier sont sous secret statistique et ne sont pas comptés"
        )
    return ligne


def ligne_eau(eau: SituationEau) -> str:
    if not eau.zones:
        return "aucun arrêté de restriction des usages de l'eau en vigueur à cette adresse"
    zones = ", ".join(
        f"{LIBELLES_TYPE.get(z.type_zone, z.type_zone)} ({LIBELLES_NIVEAU[z.niveau]})"
        for z in sorted(eau.zones, key=lambda z: -z.rang)
    )
    fins = [z.date_fin for z in eau.zones if z.date_fin]
    phrase = f"niveau {LIBELLES_NIVEAU[eau.niveau_max]} en vigueur : {zones}"
    if fins:
        phrase += f", jusqu'au {max(fins):%d/%m/%Y}"
    if eau.est_contraignante:
        phrase += (
            " ; à ce niveau, les usages de l'eau des entreprises peuvent être très réduits"
            " ou suspendus selon l'arrêté"
        )
    else:
        phrase += (
            " ; à ce niveau, l'arrêté appelle surtout à la sobriété : en lire les usages encadrés"
        )
    return phrase


LIBELLES_ACCES = {"port": "port", "aeroport": "aéroport", "rail": "rail-route"}


def ligne_acces(acces: list[Acces]) -> str:
    morceaux = []
    for a in acces:
        if a.type_acces == "autoroute":
            morceaux.append(f"autoroute {a.nom} {distance_fr(a.distance_vol_km)} à vol d'oiseau")
            continue
        nom = f"{LIBELLES_ACCES[a.type_acces]} {a.nom}"
        if a.duree_min is None or a.distance_route_km is None:
            morceaux.append(
                f"{nom} {distance_fr(a.distance_vol_km)} à vol d'oiseau (trajet non calculé)"
            )
        else:
            morceaux.append(
                f"{nom} à {nombre_fr(a.duree_min)} min ({nombre_fr(a.distance_route_km)} km)"
            )
    if not any(a.type_acces == "autoroute" for a in acces):
        morceaux.insert(0, "aucune autoroute à moins de 10 km")
    return " ; ".join(morceaux)


def ligne_solaire(batiment: Batiment | None, productible: Productible) -> str:
    if batiment is None:
        lieu = "aucun bâtiment de la BD TOPO à moins de 60 m de l'adresse"
    else:
        qui = batiment.usage.lower() if batiment.usage else "usage inconnu"
        taille = f"{nombre_fr(batiment.emprise_m2)} m² au sol"
        if batiment.hauteur_m:
            taille += f", {nombre_fr(batiment.hauteur_m, 1)} m de haut"
        position = (
            "sur l'adresse"
            if batiment.contient_adresse
            else f"à {nombre_fr(batiment.distance_m)} m"
        )
        lieu = f"bâtiment {qui} {position} ({taille})"
    angles = ""
    if productible.inclinaison_deg is not None:
        angles = f", inclinaison {nombre_fr(productible.inclinaison_deg)}° optimale"
    return (
        f"{lieu} ; productible de {nombre_fr(productible.kwh_par_kwc_an)} kWh par kWc et par an"
        f" (panneaux fixes{angles}, pertes de {PERTES_PCT} %, PVGIS)"
    )


def _jour(j: date) -> str:
    return f"{'1er' if j.day == 1 else j.day} {MOIS[j.month - 1]} {j.year}"


def lignes_obligations_solaires(
    batiment: Batiment | None,
    *,
    code_departement: str,
    solaire_consulte: bool,
    aujourd_hui: date | None = None,
) -> list[str]:
    jour = aujourd_hui or date.today()
    part, suivante = part_toiture(jour)
    neuf = (
        f"bâtiment neuf, extension ou rénovation lourde de plus de {EMPRISE_TOITURE_M2} m² au sol"
        f" (industrie, entrepôt, commerce, bureaux) : énergie renouvelable ou végétalisation sur"
        f" {part} % de la toiture au moins"
    )
    if suivante:
        neuf += f", {suivante[1]} % à compter du {_jour(suivante[0])}"
    resultat = [neuf + " (CCH L171-4)"]
    if solaire_consulte:
        if batiment is None:
            resultat.append("bâtiment existant non identifié : obligation de 2028 non évaluée")
        elif batiment.usage not in USAGES_SOUMIS:
            qui = batiment.usage.lower() if batiment.usage else "inconnu"
            resultat.append(
                f"bâtiment existant d'usage {qui} selon la BD TOPO : hors de la liste de L171-5"
                if batiment.usage and batiment.usage != "Indifférencié"
                else "bâtiment existant d'usage inconnu : obligation de 2028 à vérifier"
            )
        elif batiment.emprise_m2 < EMPRISE_TOITURE_M2:
            resultat.append(
                f"bâtiment existant de {nombre_fr(batiment.emprise_m2)} m² au sol, sous le seuil de"
                f" {EMPRISE_TOITURE_M2} m² de L171-5"
            )
        else:
            resultat.append(
                f"bâtiment existant de {nombre_fr(batiment.emprise_m2)} m² au sol : même obligation"
                f" au {_jour(ECHEANCE_EXISTANTS)}, sur une surface de toiture fixée par décret"
                " (CCH L171-5)"
            )
    seuil = SEUIL_PARKING_M2.get(code_departement, SEUIL_PARKING_DEFAUT_M2)
    if seuil is None:
        resultat.append(
            "parc de stationnement extérieur : seuil non fixé par décret pour ce territoire"
            " (loi APER, art. 40 VI)"
        )
    else:
        resultat.append(
            f"parc de stationnement extérieur de plus de {nombre_fr(seuil)} m² : ombrières"
            " d'énergie renouvelable sur la moitié de sa surface (ou ombrières sur 35 % de"
            " cette moitié, le reste végétalisé), au plus tard le"
            f" {_jour(ECHEANCE_GRAND_PARKING)} dès {nombre_fr(SEUIL_GRAND_PARKING_M2)} m², le"
            f" {_jour(ECHEANCE_PARKING)} en dessous (loi APER, art. 40, modifiée le 26 novembre"
            " 2025)"
        )
    return resultat


def ligne_zones_activite(nb: int, zones: list[ZoneActivite]) -> str:
    if not nb:
        return (
            f"aucune zone d'activité ou industrielle à moins de {RAYON_ZONES_ACTIVITE_KM} km"
            " (BD TOPO)"
        )
    details = [
        f"{z.nom} ({nombre_fr(z.surface_ha, 1 if z.surface_ha < 10 else 0)} ha, "
        + ("l'adresse est dedans" if z.contient_adresse else distance_fr(z.distance_km))
        + ")"
        for z in zones
    ]
    pluriel = "s" if nb > 1 else ""
    return (
        f"{nb} zone{pluriel} d'activité ou industrielle{pluriel} à moins de"
        f" {RAYON_ZONES_ACTIVITE_KM} km ; les plus proches : {' ; '.join(details)}"
    )


def ligne_friches(nb: int, friches: list[Friche]) -> str:
    if not nb:
        return (
            f"aucune friche de 1 ha ou plus référencée à moins de {RAYON_FRICHES_KM} km"
            " (inventaire déclaratif : une friche non référencée n'y figure pas)"
        )
    pluriel = "s" if nb > 1 else ""
    details = []
    for f in friches:
        zone = f", zone {f.type_zone_urbanisme}" if f.type_zone_urbanisme else ""
        statut = f", {f.statut}" if f.statut else ""
        details.append(
            f"{f.nom} à {f.commune or 'commune inconnue'} ({f.type_friche}, "
            f"{nombre_fr(f.surface_m2 / 10_000, 1)} ha{zone}{statut}) {distance_fr(f.distance_km)}"
        )
    return (
        f"{nb} friche{pluriel} de 1 ha ou plus à moins de {RAYON_FRICHES_KM} km ; les plus"
        f" proches : {' ; '.join(details)}"
    )


def ligne_reseaux_chaleur(reseaux: list[ReseauChaleur]) -> str:
    if not reseaux:
        return f"aucun réseau recensé à moins de {RAYON_RESEAUX_CHALEUR_KM} km"
    details = []
    for r in reseaux:
        lieu = f" ({r.communes})" if r.communes and r.communes != r.nom else ""
        precisions = [] if r.a_trace else ["tracé non publié, centre de la commune"]
        if r.taux_enr_pct is not None:
            precisions.append(
                f"{nombre_fr(r.taux_enr_pct)} % d'énergies renouvelables et de récupération"
            )
        if r.classe:
            precisions.append("classé")
        suite = f", {', '.join(precisions)}" if precisions else ""
        details.append(f"{r.nom}{lieu} {distance_fr(r.distance_km)}{suite}")
    return f"le plus proche, {' ; puis '.join(details)}"


def liste_risques(risques: list[Risque]) -> str:
    return ", ".join(f"{r.libelle} ({r.niveau})" if r.niveau else r.libelle for r in risques)


def libelle_prise_de_vue(debut: date, fin: date) -> str:
    if debut == fin:
        return f"le {debut.day} {MOIS[debut.month - 1]} {debut.year}"
    if (debut.month, debut.year) == (fin.month, fin.year):
        return f"du {debut.day} au {fin.day} {MOIS[fin.month - 1]} {fin.year}"
    return (
        f"du {debut.day} {MOIS[debut.month - 1]} {debut.year}"
        f" au {fin.day} {MOIS[fin.month - 1]} {fin.year}"
    )


def ligne_image_site(image: ImageSite) -> str:
    prise = libelle_prise_de_vue(image.debut, image.fin)
    return (
        f"vue aérienne de {image.cote_m} m de côté, prise de vue {prise}"
        f" (BD ORTHO de l'IGN) : {image.url}"
    )
