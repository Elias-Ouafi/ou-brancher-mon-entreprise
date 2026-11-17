"""Lignes de sortie sur la facture, le raccordement et l'activité du projet."""

from __future__ import annotations

from ingestion.courbe import BilanCourbe
from ingestion.diagnostic.formats import (
    euros_arrondis,
    gwh_fr,
    libelle_semestre,
    nombre_court,
    nombre_fr,
    pourcentage,
)
from ingestion.diagnostic.modeles import (
    ACTIVITES,
    CATEGORIES_PALIER,
    DOMAINES_TURPE,
    LIBELLES_ACCISE,
    Carbone,
    Continuite,
    Diagnostic,
    EntreprisesAdresse,
    EntreprisesCommune,
    PartChiffreAffaires,
    PartValeurAjoutee,
    PrixLocaux,
    ZoneRte,
)
from ingestion.turpe import PROFIL_ACTIVITE, PROFIL_COURBE, Acheminement

_GRANDS_SECTEURS = {
    "INDUSTRIE": "industrie",
    "TERTIAIRE": "tertiaire",
    "AGRICULTURE": "agriculture",
    "INCONNU": "secteur inconnu",
}


def ligne_entreprises_adresse(e: EntreprisesAdresse) -> str:
    secteur = _GRANDS_SECTEURS.get(e.grand_secteur, e.grand_secteur.lower())
    activite = f", {e.libelle_naf2}" if e.libelle_naf2 else ""
    return (
        f"{e.nb_sites} site(s) d'entreprise consommant {nombre_fr(e.consommation_mwh, 1)} MWh"
        f" en {e.annee} ({secteur}{activite} ; Enedis)"
    )


def ligne_entreprises_commune(e: EntreprisesCommune) -> str:
    phrase = (
        f"{e.nb_sites} site(s), {gwh_fr(e.consommation_gwh)}/an en {e.annee}, dont industrie"
        f" {gwh_fr(e.consommation_industrie_gwh)} ({e.nb_sites_industrie} site(s))"
    )
    if e.consommation_industrie_gwh > 0:
        phrase += (
            f" — rang {e.rang_industrie_departement} sur {e.nb_communes_departement} communes"
            " du département"
        )
    if e.libelle_naf2_principal:
        phrase += (
            f" ; premier secteur : {e.libelle_naf2_principal}"
            f" ({gwh_fr(e.consommation_naf2_principal_gwh or 0)})"
        )
    return phrase


def ligne_courbe(b: BilanCourbe) -> str:
    phrase = (
        f"du {b.debut:%d/%m/%Y} au {b.fin:%d/%m/%Y}, {nombre_fr(b.conso_annuelle_mwh, 1)} MWh"
        f" par an, pointe de {nombre_fr(b.puissance_max_kw)} kW,"
        f" {nombre_fr(b.part_hc * 100)} % en heures creuses (22 h – 6 h)"
    )
    if b.cout_htva_eur_an is not None:
        cout = euros_arrondis(b.cout_htva_eur_an)
        phrase += f" ; environ {cout} HTVA par an au tarif HP/HC actuel"
    else:
        # Au-delà de 36 kVA, aucun prix horaire public : l'énergie reste au prix de la tranche
        phrase += (
            " ; énergie au prix moyen de la tranche (aucun prix horaire public au-delà de"
            " 36 kVA), acheminement chiffré sur cette courbe"
        )
    if b.intensite_courbe_g_kwh is not None and b.intensite_reseau_g_kwh:
        ecart = b.intensite_courbe_g_kwh / b.intensite_reseau_g_kwh - 1
        sens = "de plus" if ecart >= 0 else "de moins"
        phrase += (
            f" ; cette consommation émet {nombre_fr(abs(ecart) * 100)} % {sens} que le réseau aux"
            f" mêmes heures ({b.source_carbone})"
        )
    return phrase


def ligne_acheminement(a: Acheminement) -> str:
    source = {
        PROFIL_COURBE: "votre courbe de charge",
        PROFIL_ACTIVITE: "journée type mesurée par Enedis pour l'industrie",
    }.get(a.profil, "puissance constante")
    phrase = (
        f"environ {euros_arrondis(a.total_eur)} HTVA par an, soit"
        f" {nombre_fr(a.prix_eur_mwh, 1)} €/MWh, déjà compris dans le prix moyen (TURPE 7"
        f" {DOMAINES_TURPE[a.domaine_turpe]}, {a.utilisation} utilisation, la moins chère ici ;"
        f" {nombre_fr(a.puissance_kva)} kVA souscrits, {source})"
    )
    if a.a_reverifier():
        phrase += (
            ". Grille TURPE peut-être périmée (nouvelle chaque 1er août) : mettre à jour les"
            " seeds turpe_soutirage et turpe_gestion_comptage"
        )
    return phrase


def ligne_activite(code: str, *, courbe_fournie: bool = False) -> str:
    a = ACTIVITES[code]
    # La courbe du site prime sur le profil de l'activité, comme dans l'acheminement
    if courbe_fournie:
        charge = "votre courbe de charge"
    elif a.profil_charge:
        charge = "journée type mesurée par Enedis (points de plus de 250 kVA)"
    else:
        charge = "puissance constante, faute de courbe publiée"
    return f"{a.libelle}, profil de charge : {charge}. Régime d'accise : {a.accise}."


def ligne_accise(d: Diagnostic) -> str | None:
    if d.accise is None or d.prix is None or d.prix.conso_mwh is None:
        return None
    normal = nombre_fr(d.accise.tarif_eur_mwh, 2)
    if (r := d.accise_reduite) is None:
        va = d.part_valeur_ajoutee
        if d.activite == "data-center" and va is not None:
            return (
                f"électro-intensité de {pourcentage(va.electro_intensite)}, sous les 2,25 %"
                " exigés des centres de stockage de données : tarif normal de"
                f" {normal} €/MWh"
            )
        if d.activite == "data-center":
            return (
                "consommation sous 1 GWh par an : tarif normal de"
                f" {normal} €/MWh, le tarif réduit ne vise que la fraction au-delà"
            )
        return None
    tarif = nombre_fr(r.tarif_eur_mwh, 2 if r.tarif_eur_mwh % 1 else 0)
    assiette = (
        f" sur {nombre_fr(r.mwh_reduits)} MWh (au-delà du premier GWh)"
        if r.categorie == "centre_stockage_donnees"
        else ""
    )
    condition = (
        "si les huit conditions de L312-70 sont remplies"
        if r.categorie == "centre_stockage_donnees"
        else "si l'activité principale relève de l'industrie, de l'énergie ou de l'eau"
        " (sections B à E de la NACE)"
    )
    return (
        f"environ {euros_arrondis(r.accise_reduite_eur)} par an au tarif réduit de"
        f" {tarif} €/MWh ({LIBELLES_ACCISE[r.categorie]}){assiette}, au lieu de"
        f" {euros_arrondis(r.accise_normale_eur)} au tarif normal de {normal} €/MWh, soit"
        f" {euros_arrondis(r.economie_eur)} de moins, {condition}, sur attestation au fournisseur"
    )


def ligne_locaux(p: PrixLocaux, surface_m2: float | None, commune: str) -> str:
    lieu = commune if p.niveau == "commune" else "le département (peu de ventes dans la commune)"
    ligne = (
        f"{nombre_fr(p.prix_m2_median)} €/m² à la vente"
        f"{' à ' if p.niveau == 'commune' else ' dans '}{lieu}"
        f" (médiane de {nombre_fr(p.nb_ventes)} ventes, quartiles {nombre_fr(p.prix_m2_q1)} à"
        f" {nombre_fr(p.prix_m2_q3)} ; dernière vente en {p.derniere_vente:%m/%Y} ; DVF)"
    )
    if surface_m2:
        bas, haut = p.prix_m2_q1 * surface_m2, p.prix_m2_q3 * surface_m2
        ligne += (
            f". Pour {nombre_fr(surface_m2)} m² : environ"
            f" {euros_arrondis(p.prix_m2_median * surface_m2)} à l'achat"
            f" (de {euros_arrondis(bas)} à {euros_arrondis(haut)})"
        )
    return ligne


def ligne_continuite(c: Continuite) -> str:
    def pct(valeur: float) -> str:
        return f"{nombre_fr(valeur, 1)} %"

    ligne = (
        f"Qualité de fourniture (Enedis, {c.derniere_annee}) : {pct(c.part_mal_alimentes_pct)}"
        " des clients du département au-delà des seuils de coupure"
    )
    if c.mediane_nationale_pct is not None:
        ligne += f" (médiane des départements : {pct(c.mediane_nationale_pct)})"
    ligne += (
        ", au-dessus du seuil réglementaire de 3 %"
        if c.depasse_seuil
        else ", sous le seuil réglementaire de 3 %"
    )
    if c.moyenne_5_ans_pct is not None:
        ligne += f" ; {pct(c.moyenne_5_ans_pct)} en moyenne sur cinq ans"
        if c.annees_au_dela_seuil_5_ans:
            ligne += f", seuil dépassé {c.annees_au_dela_seuil_5_ans} année(s) sur cinq"
    return ligne + "."


def part_va(va: PartValeurAjoutee) -> str:
    tarif = nombre_fr(va.accise.tarif_eur_mwh, 2)
    phrase = (
        f"facture {pourcentage(va.part_facture)} ; électro-intensité"
        f" {pourcentage(va.electro_intensite)} (accise au tarif normal de {tarif} €/MWh"
        f" depuis le {va.accise.date_debut:%d/%m/%Y}, rapportée à la valeur ajoutée)"
    )
    if va.palier is None:
        phrase += ", sous le palier de 0,5 % : pas de taux réduit d'accise à ce titre"
    else:
        palier = nombre_fr(va.palier * 100, 3).rstrip("0")
        categorie = CATEGORIES_PALIER[va.palier]
        reduit = va.accise.reduits.get(categorie)
        montant = "" if reduit is None else f", {nombre_fr(reduit, 2)} €/MWh au lieu de {tarif}"
        phrase += (
            f", au-dessus du palier de {palier} % : taux réduit d'accise possible"
            f" ({LIBELLES_ACCISE[categorie]}{montant}) si l'activité principale relève de"
            " l'industrie, de l'énergie ou de l'eau (sections B à E de la NACE)"
        )
    if va.accise.a_reverifier():
        phrase += (
            ". Barème d'accise peut-être périmé (indexé chaque 1er février) : mettre à jour le"
            " seed accise_electricite"
        )
    return phrase


def part_ca(part: PartChiffreAffaires) -> str:
    phrase = (
        f"{pourcentage(part.part)} ({euros_arrondis(part.facture_eur)} sur"
        f" {euros_arrondis(part.chiffre_affaires_eur)})"
    )
    if part.part_bas is not None and part.part_haut is not None:
        phrase += (
            f", de {pourcentage(part.part_bas)} à {pourcentage(part.part_haut)}"
            " avec les scénarios à 3 ans"
        )
    # Une facture au-delà du chiffre d'affaires trahit presque toujours une erreur d'unité
    if part.part > 1:
        phrase += ". Facture supérieure au chiffre d'affaires : --ca est en euros, --conso en MWh"
    return phrase


def prix_court(d: Diagnostic) -> str:
    p = d.prix
    if p is None and d.conso_mwh is not None:
        return f"non publié par Eurostat pour {nombre_court(d.conso_mwh)} MWh/an"
    if p is None:
        return "non disponible (lancer la commande prix, puis dbt build)"
    debut = libelle_semestre(p.dernier_semestre, precedent=True)
    periode = f"{debut} – {libelle_semestre(p.dernier_semestre)}"
    prix = f"{nombre_fr(p.prix_reference_eur_kwh, 4)} €/kWh HTVA"
    if p.conso_mwh is None:
        return (
            f"{prix} pour une petite entreprise (tranche {p.code_tranche}, {p.libelle_tranche} ;"
            f" Eurostat, {periode}). Préciser --conso pour la tranche et la facture de"
            " l'entreprise"
        )
    return (
        f"{prix}, soit environ {euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))} par an"
        f" pour {nombre_court(p.conso_mwh)} MWh (tranche {p.code_tranche} ; Eurostat, {periode})"
    )


# Pièces exigées par la procédure de raccordement de RTE (CRE, délibération 2026-32 du 4 février
# 2026, § 2) : une liste à rassembler, jamais une note du projet. La CRE a demandé à RTE une
# évolution qui tienne mieux compte de la maturité au plus tard le 1er octobre 2026 : la procédure
# en vigueur se relit chez RTE. La procédure d'Enedis (HTA) n'est pas couverte.
def pieces_dossier_rte(puissance_mw: float) -> list[str]:
    forfait = euros_arrondis(puissance_mw * 1000)
    return [
        "Dossier de raccordement RTE, pièces à rassembler (CRE, délibération 2026-32) :",
        "  - à la demande de proposition technique et financière : 42 000 € HT (déduits du premier"
        " acompte) et un calendrier prévisionnel (décision d'investissement, début et fin des"
        " travaux, mise en service, dépôt et obtention de l'autorisation environnementale et du"
        " permis de construire quand ils sont obligatoires) ;",
        "  - pour entrer dans la file d'attente : justificatif d'avancement du projet (à défaut"
        f" 1 000 €/MW, soit {forfait} ici), justificatif de maîtrise foncière de la parcelle du"
        " point de raccordement, premier acompte ;",
        "  - pour y rester : ces deux justificatifs chaque année (le versement de remplacement,"
        " deux fois au plus) ; un calendrier non tenu peut allonger le délai.",
        "  Limite : liste de la procédure approuvée en février 2026, à relire chez RTE avant de"
        " déposer ; la maturité des projets doit y entrer plus largement, et Enedis (HTA) a sa"
        " propre procédure.",
    ]


def heures_carbone(c: Carbone) -> str | None:
    if not c.profil_horaire:
        return None
    bas = min(c.profil_horaire, key=lambda heure: heure[1])
    haut = max(c.profil_horaire, key=lambda heure: heure[1])
    return (
        f"le moins carboné vers {bas[0]} h ({nombre_fr(bas[1])} gCO₂e/kWh), le plus vers"
        f" {haut[0]} h ({nombre_fr(haut[1])}), heure locale ; profil EDF SEI {c.annee_profil}"
    )


def calendrier_zone(z: ZoneRte) -> str:
    if z.priorite == "P1":
        return (
            f"capacité supplémentaire annoncée à partir de {z.annee_capacite}, travaux lancés"
            " dès l'obtention des autorisations"
        )
    return (
        f"capacité supplémentaire possible à partir de {z.annee_capacite}, si les projets"
        " industriels de la zone se concrétisent"
    )
