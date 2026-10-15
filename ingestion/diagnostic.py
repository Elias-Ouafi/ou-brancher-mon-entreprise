"""Diagnostic d'implantation pour une adresse géocodée.

Lit la couche gold en lecture seule et ne recalcule aucune règle métier : la
pression industrielle, la tension régionale, les seuils de raccordement et les
limites méthodologiques sont tous construits par dbt. Ce module ne fait que
sélectionner, assembler et mettre en forme.

Points de conception :

* **Le département, maille garantie.** `gold.fct_diagnostic_territoire` a une
  ligne par département ; la commune s'y ajoute par jointure quand elle figure
  dans `gold.fct_pression_industrielle_commune` (environ 1 300 communes sur
  35 000). Sinon, le diagnostic le dit et s'en tient au département.
* **Arrondissements.** La BAN renvoie le code de l'arrondissement (69383 pour
  Lyon 3e) ; ODRÉ publie Paris, Lyon et Marseille sous le code de la commune.
* **Coût d'un profil type, pas d'une adresse.** Le prix est péréqué : le
  coût horaire d'un profil (`gold.fct_cout_mensuel_profil`) est le même partout,
  et le diagnostic le présente comme tel, sur le dernier mois complet et tarifé.
* **Aucun chiffre sans sa limite.** Chaque bloc de la sortie texte est suivi de
  la limite correspondante, lue dans `gold.limites_methodologiques`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from ingestion.config import Settings, get_settings
from ingestion.geocode import Territoire
from ingestion.georisques import Exposition, GeorisquesError, Risque, RisquesSite, lire_risques

logger = logging.getLogger(__name__)

#: Arrondissements municipaux (3 premiers caractères du code) -> code de la commune.
ARRONDISSEMENTS: dict[str, str] = {"751": "75056", "693": "69123", "132": "13055"}

_DIAGNOSTIC_SQL = """
select
    territoire.*,
    commune.commune,
    commune.consommation_electricite_mwh / 1000 as conso_industrielle_commune_gwh,
    commune.nb_sites_electricite as nb_sites_commune,
    commune.est_minorant as conso_commune_est_minorant
from gold.fct_diagnostic_territoire as territoire
left join gold.fct_pression_industrielle_commune as commune
    on commune.code_insee_commune = $commune
where territoire.code_departement = $departement
"""

_SEUIL_SQL = """
select domaine_tension, gestionnaire, commentaire
from gold.seuils_raccordement
where $puissance >= puissance_min_mw
  and (puissance_max_mw is null or $puissance < puissance_max_mw)
"""


#: Profil type chiffré par défaut (seed `profils_consommation`).
PROFIL_DEFAUT = "bureau_36kva"

_COUT_SQL = """
select
    cout.mois,
    profil.libelle,
    cout.conso_kwh,
    cout.part_hc,
    cout.cout_htva_eur,
    cout.prix_moyen_htva_eur_kwh,
    cout.intensite_moyenne_g_kwh,
    cout.kwh_decales,
    cout.gain_decalage_eur,
    cout.co2_evite_kg
from gold.fct_cout_mensuel_profil as cout
inner join (
    select distinct profil, libelle from gold.profils_consommation
) as profil on profil.profil = cout.profil
where cout.profil = $profil
  and cout.est_complet
  and cout.cout_htva_eur is not null
order by cout.mois desc
limit 1
"""


_PRIX_SQL = """
select *
from gold.fct_prix_entreprise_tranche
where $conso >= conso_min_mwh
  and (conso_max_mwh is null or $conso < conso_max_mwh)
"""


class DiagnosticIndisponibleError(RuntimeError):
    """Le warehouse ne permet pas de produire le diagnostic."""


def code_commune_parent(code_insee: str) -> str:
    """Ramène un arrondissement de Paris, Lyon ou Marseille au code de sa commune."""
    if len(code_insee) == 5 and code_insee[:3] in ARRONDISSEMENTS:
        return ARRONDISSEMENTS[code_insee[:3]]
    return code_insee


def code_departement(code_commune: str) -> str:
    """Code département d'un code commune : 3 caractères outre-mer, 2 ailleurs.

    Pendant Python de la macro dbt `code_departement`.
    """
    return code_commune[:3] if code_commune.startswith("97") else code_commune[:2]


@dataclass(frozen=True, slots=True)
class Seuil:
    """Domaine de tension et gestionnaire applicables à une puissance."""

    puissance_mw: float
    domaine_tension: str
    gestionnaire: str
    commentaire: str


@dataclass(frozen=True, slots=True)
class CoutProfil:
    """Bilan du dernier mois complet et tarifé d'un profil type."""

    mois: date
    libelle: str
    conso_kwh: float
    part_hc: float
    cout_htva_eur: float
    prix_moyen_htva_eur_kwh: float
    intensite_moyenne_g_kwh: float
    kwh_decales: float
    gain_decalage_eur: float
    co2_evite_kg: float


@dataclass(frozen=True, slots=True)
class PrixEntreprise:
    """Prix de l'électricité de la tranche de consommation de l'entreprise (Eurostat)."""

    conso_mwh: float
    tranche: str
    code_tranche: str
    libelle_tranche: str
    conso_min_mwh: float
    conso_max_mwh: float | None
    premier_semestre: str
    dernier_semestre: str
    prix_dernier_semestre_eur_kwh: float
    prix_reference_eur_kwh: float
    taxes_reference_eur_kwh: float | None
    prix_moyen_5_ans_eur_kwh: float
    prix_min_eur_kwh: float
    semestre_min: str
    prix_max_eur_kwh: float
    semestre_max: str
    prix_3_ans_bas_eur_kwh: float | None
    prix_3_ans_central_eur_kwh: float | None
    prix_3_ans_haut_eur_kwh: float | None
    nb_erreurs_observees: int | None
    # Contrôle de la méthode sur le passé (gold.fct_prix_entreprise_calibration)
    nb_periodes_controlees: int
    nb_dans_eventail: int | None
    nb_au_dessus: int | None
    nb_en_dessous: int | None
    premiere_origine_controlee: str | None
    derniere_origine_controlee: str | None
    ecart_median_au_central: float | None

    def facture_eur(self, prix_eur_kwh: float) -> float:
        """Facture annuelle HTVA à ce prix.

        Le prix Eurostat est un prix moyen tout compris (énergie, acheminement,
        part fixe), rapporté au kWh : il donne directement la facture.
        """
        return self.conso_mwh * 1000 * prix_eur_kwh


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Diagnostic complet d'une adresse : une ligne de gold, plus la commune."""

    territoire: Territoire
    code_insee_commune: str
    # Territoire
    code_departement: str
    departement: str
    code_insee_region: str
    region: str
    est_zni: bool
    # Pression industrielle
    annee_pression: int | None
    commune: str | None
    conso_industrielle_commune_gwh: float | None
    nb_sites_commune: int | None
    conso_commune_est_minorant: bool | None
    conso_industrielle_departement_gwh: float
    nb_sites_departement: int
    nb_communes_avec_site: int
    conso_departement_est_minorant: bool
    rang_pression_departement: int | None
    nb_departements_classes: int
    # Tension du réseau régional
    equilibre_mois_debut: date | None
    equilibre_mois_fin: date | None
    equilibre_nb_mois: int
    production_region_gwh: float | None
    consommation_region_gwh: float | None
    solde_region_gwh: float | None
    taux_couverture_region: float | None
    statut_equilibre: str | None
    rang_dependance_region: int | None
    nb_regions_classees: int
    puissance_enr_installee_mw: float | None
    puissance_a_compenser_mw: float | None
    energie_non_evacuee_mwh: float | None
    # Raccordement et limites
    seuil: Seuil | None
    #: Consommation annuelle déclarée (`--conso`), même sans prix publié pour sa tranche.
    conso_mwh: float | None
    prix: PrixEntreprise | None
    cout: CoutProfil | None
    limites: dict[str, str]
    # Risques à l'adresse (Géorisques), ajoutés par `completer_risques`
    risques: RisquesSite | None = None
    #: Raison pour laquelle Géorisques n'a pas pu être consulté, le cas échéant.
    risques_erreur: str | None = None

    @property
    def commune_a_des_sites(self) -> bool:
        return bool(self.nb_sites_commune)


def lire_diagnostic(
    territoire: Territoire,
    *,
    puissance_mw: float | None = None,
    conso_mwh: float | None = None,
    settings: Settings | None = None,
    database: Path | None = None,
) -> Diagnostic:
    """Assemble le diagnostic d'un territoire depuis la couche gold.

    Lève `DiagnosticIndisponibleError` si le warehouse est absent ou si la
    couche gold n'a pas été construite (`dbt build`).
    """
    path = database or (settings or get_settings()).duckdb_path
    if not Path(path).exists():
        raise DiagnosticIndisponibleError(
            f"warehouse introuvable : {path} (charger les donnees puis lancer dbt build)"
        )

    commune = code_commune_parent(territoire.code_insee_commune)
    departement = code_departement(commune)
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            ligne = _une_ligne(
                connection, _DIAGNOSTIC_SQL, {"commune": commune, "departement": departement}
            )
            seuil = _seuil(connection, puissance_mw) if puissance_mw is not None else None
            prix = _prix(connection, conso_mwh) if conso_mwh is not None else None
            cout_ligne = _une_ligne(connection, _COUT_SQL, {"profil": PROFIL_DEFAUT})
            limites = dict(
                connection.execute(
                    "select indicateur, limite from gold.limites_methodologiques"
                ).fetchall()
            )
    except duckdb.CatalogException as error:
        raise DiagnosticIndisponibleError(
            f"couche gold incomplete, lancer dbt build ({error})"
        ) from error

    if ligne is None:
        raise DiagnosticIndisponibleError(
            f"departement {departement!r} absent de gold.fct_diagnostic_territoire"
        )
    return Diagnostic(
        territoire=territoire,
        code_insee_commune=commune,
        seuil=seuil,
        conso_mwh=conso_mwh,
        prix=prix,
        cout=None if cout_ligne is None else CoutProfil(**cout_ligne),
        limites=limites,
        **ligne,
    )


def _une_ligne(
    connection: duckdb.DuckDBPyConnection, sql: str, parametres: dict[str, Any]
) -> dict[str, Any] | None:
    result = connection.execute(sql, parametres)
    row = result.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in result.description], row, strict=True))


def _seuil(connection: duckdb.DuckDBPyConnection, puissance_mw: float) -> Seuil | None:
    if puissance_mw < 0:
        raise ValueError("la puissance du projet doit etre positive")
    ligne = _une_ligne(connection, _SEUIL_SQL, {"puissance": puissance_mw})
    return None if ligne is None else Seuil(puissance_mw=puissance_mw, **ligne)


def _prix(connection: duckdb.DuckDBPyConnection, conso_mwh: float) -> PrixEntreprise | None:
    if conso_mwh <= 0:
        raise ValueError("la consommation annuelle doit etre strictement positive")
    ligne = _une_ligne(connection, _PRIX_SQL, {"conso": conso_mwh})
    return None if ligne is None else PrixEntreprise(conso_mwh=conso_mwh, **ligne)


# ---------------------------------------------------------------------------
# Sortie texte
# ---------------------------------------------------------------------------


def _nombre(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def _gwh(valeur: float) -> str:
    return f"{_nombre(valeur, 1 if valeur < 100 else 0)} GWh"


@dataclass(frozen=True, slots=True)
class Section:
    """Un bloc du diagnostic : un titre, puis des lignes de texte et de limites.

    Les limites restent à leur place, juste après le chiffre qu'elles nuancent :
    chaque format (terminal, Markdown, PDF) les met en forme à sa façon.
    """

    titre: str
    #: Couples (`"texte"` ou `"limite"`, contenu), dans l'ordre d'affichage.
    elements: list[tuple[str, str]]


def entete(diagnostic: Diagnostic) -> list[str]:
    """Hiérarchie territoriale, et l'avertissement si l'adresse est imprécise."""
    d = diagnostic
    t = d.territoire
    lignes = [
        f"Commune {t.commune} ({d.code_insee_commune}) · {d.departement} ({d.code_departement})"
        f" · {d.region} ({d.code_insee_region})",
    ]
    if not t.est_precis:
        lignes.append(f"Attention : adresse résolue à la maille « {t.precision} » seulement.")
    return lignes


def sections(diagnostic: Diagnostic) -> list[Section]:
    """Les quatre blocs du diagnostic, communs à tous les formats de sortie."""
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

    # 1. Pression industrielle
    s = bloc(
        "1. Pression industrielle locale"
        f" (sites raccordés au réseau de transport, {d.annee_pression or '?'})"
    )
    if d.commune_a_des_sites:
        minorant = " (minorant : secret statistique)" if d.conso_commune_est_minorant else ""
        texte(
            s,
            f"Commune     : {d.nb_sites_commune} site(s),"
            f" {_gwh(d.conso_industrielle_commune_gwh or 0)}/an{minorant}",
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
            f" commune(s), {_gwh(d.conso_industrielle_departement_gwh)}/an{rang}",
        )
    else:
        texte(s, "Département : aucun site raccordé au transport")
    limite(s, "pression_industrielle")

    # 2. Tension du réseau régional
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
            f"Production {_gwh(d.production_region_gwh or 0)},"
            f" consommation {_gwh(d.consommation_region_gwh or 0)}"
            f" : couverture {_nombre(d.taux_couverture_region * 100)} %"
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
            f"Contraintes d'évacuation (RTE) : {_nombre(d.puissance_enr_installee_mw or 0)} MW"
            f" d'EnR installés ou en projet, {_nombre(d.puissance_a_compenser_mw)} MW à"
            f" compenser, {_nombre(d.energie_non_evacuee_mwh or 0)} MWh non évacués par an",
        )
        limite(s, "contraintes_reseau")

    # 3. Raccordement
    s = bloc("3. Raccordement")
    if d.seuil is not None:
        texte(
            s,
            f"Puissance {_nombre_court(d.seuil.puissance_mw)} MW → {d.seuil.domaine_tension},"
            f" {d.seuil.gestionnaire} : {d.seuil.commentaire}",
        )
        limite(s, "seuil_raccordement")
    else:
        texte(s, "Préciser --puissance (en MW) pour connaître le domaine de tension.")
    limite(s, "capacite_raccordement")

    # 4. Prix de l'électricité pour l'entreprise
    s = bloc("4. Prix de l'électricité pour l'entreprise (identique sur tout le territoire)")
    if d.prix is None and d.conso_mwh is not None:
        texte(
            s,
            f"Aucun prix publié par Eurostat pour la tranche de {_nombre_court(d.conso_mwh)}"
            " MWh/an.",
        )
    elif d.prix is None:
        texte(
            s,
            "Préciser --conso (consommation annuelle en MWh, lue sur la facture) pour connaître"
            " le prix de la tranche de l'entreprise.",
        )
    else:
        p = d.prix
        texte(
            s,
            f"Consommation {_nombre_court(p.conso_mwh)} MWh/an → tranche {p.code_tranche}"
            f" ({p.libelle_tranche}), prix moyen constaté par Eurostat",
        )
        taxes = (
            f", dont {_nombre(p.taxes_reference_eur_kwh, 4)} €/kWh de taxes non récupérables"
            if p.taxes_reference_eur_kwh is not None
            else ""
        )
        facture = _euros_arrondis(p.facture_eur(p.prix_reference_eur_kwh))
        texte(
            s,
            f"12 derniers mois ({_semestre(p.dernier_semestre, precedent=True)} –"
            f" {_semestre(p.dernier_semestre)}) : {_nombre(p.prix_reference_eur_kwh, 4)} €/kWh"
            f" HTVA{taxes} → facture d'environ {facture} par an",
        )
        texte(
            s,
            f"Repères depuis {_semestre(p.premier_semestre)} : moyenne sur 5 ans"
            f" {_nombre(p.prix_moyen_5_ans_eur_kwh, 4)} €/kWh, plus bas"
            f" {_nombre(p.prix_min_eur_kwh, 4)} ({_semestre(p.semestre_min)}), plus haut"
            f" {_nombre(p.prix_max_eur_kwh, 4)} ({_semestre(p.semestre_max)})",
        )
        bas, centre, haut = (
            p.prix_3_ans_bas_eur_kwh,
            p.prix_3_ans_central_eur_kwh,
            p.prix_3_ans_haut_eur_kwh,
        )
        if bas is not None and centre is not None and haut is not None:
            texte(
                s,
                "Prix moyen des 3 prochaines années, d'après l'historique de la tranche et les"
                " erreurs passées de la méthode :"
                f" de {_nombre(bas, 4)} à {_nombre(haut, 4)} €/kWh, {_nombre(centre, 4)} au centre"
                f" → facture de {_euros_arrondis(p.facture_eur(bas))} à"
                f" {_euros_arrondis(p.facture_eur(haut))} par an"
                f" ({_euros_arrondis(p.facture_eur(centre))} au centre)",
            )
            texte(s, _controle_sur_le_passe(p))
        else:
            texte(
                s,
                "Historique trop court pour cette tranche : pas de scénario à 3 ans.",
            )
        limite(s, "prix_entreprise")

    # 5. Coût d'un profil type
    s = bloc("5. Coût horaire d'un profil type (identique sur tout le territoire)")
    if d.cout is None:
        texte(s, "Aucun mois complet et tarifé : charger les données nationales récentes.")
    else:
        c = d.cout
        texte(
            s,
            f"{c.libelle}, {_MOIS[c.mois.month - 1]} {c.mois.year},"
            " option Heures Creuses du tarif bleu non résidentiel",
        )
        texte(
            s,
            f"{_nombre(c.conso_kwh)} kWh, dont {_nombre(c.part_hc * 100)} % en heures creuses :"
            f" {_nombre(c.cout_htva_eur)} € HTVA, soit {_nombre(c.prix_moyen_htva_eur_kwh, 4)}"
            f" €/kWh ; {_nombre(c.intensite_moyenne_g_kwh)} gCO₂/kWh en moyenne",
        )
        texte(
            s,
            f"Décaler {_nombre(c.kwh_decales)} kWh flexibles vers les heures creuses :"
            f" {_nombre(c.gain_decalage_eur)} € économisés, {_co2_evite(c.co2_evite_kg)}",
        )
        if d.est_zni:
            texte(
                s,
                "Barème de métropole continentale : la Corse et l'outre-mer ont leurs propres"
                " barèmes réglementés.",
            )
    limite(s, "cout_profil")

    # 6. Risques à l'adresse
    s = bloc("6. Risques naturels et technologiques à l'adresse (Géorisques)")
    if d.risques_erreur is not None:
        texte(
            s,
            "Géorisques n'a pas répondu, même après plusieurs tentatives : risques non évalués."
            " À consulter sur georisques.gouv.fr avant tout engagement.",
        )
    elif d.risques is None:
        texte(s, "Non consulté (option --sans-risques).")
    elif d.risques.maille == "commune":
        r = d.risques
        texte(
            s,
            "Rapport à l'adresse indisponible (Géorisques n'a pas répondu) : repli sur les"
            " risques recensés dans la commune, sans évaluation à l'adresse.",
        )
        for famille, nom in (("naturel", "Naturels"), ("technologique", "Technologiques")):
            recenses = r.selon(Exposition.INCONNU, famille)
            texte(
                s,
                f"{nom}, recensés dans la commune : {_liste_risques(recenses) or 'aucun'}",
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
            texte(s, f"{nom}, à l'adresse : {_liste_risques(exposes) or 'aucun recensé'}")
        if inconnus := r.selon(Exposition.INCONNU):
            texte(
                s,
                "Présents dans la commune, non évalués à l'adresse : " + _liste_risques(inconnus),
            )
        if ecartes := r.selon(Exposition.NON_EXPOSE):
            texte(
                s,
                "Présents dans la commune, mais l'adresse n'est pas concernée : "
                + _liste_risques(ecartes),
            )
        if r.url:
            texte(s, f"Rapport officiel : {r.url}")
        limite(s, "risques_site")
    return resultat


def _liste_risques(risques: list[Risque]) -> str:
    """« Séisme (faible), Inondation » : le niveau entre parenthèses quand il est publié."""
    return ", ".join(f"{r.libelle} ({r.niveau})" if r.niveau else r.libelle for r in risques)


def completer_risques(diagnostic: Diagnostic, *, settings: Settings | None = None) -> Diagnostic:
    """Ajoute le rapport Géorisques de l'adresse ; ne fait jamais échouer le diagnostic.

    Géorisques coupe des connexions par intermittence : s'il reste injoignable
    après les nouvelles tentatives, le diagnostic sort quand même et le dit.
    """
    t = diagnostic.territoire
    try:
        risques = lire_risques(
            latitude=t.latitude,
            longitude=t.longitude,
            codes_commune={t.code_insee_commune, diagnostic.code_insee_commune},
            settings=settings,
        )
    except GeorisquesError as error:
        logger.warning("risques non evalues : %s", error)
        return replace(diagnostic, risques_erreur=str(error))
    return replace(diagnostic, risques=risques)


def formater_diagnostic(diagnostic: Diagnostic) -> str:
    """Diagnostic lisible en terminal, chaque bloc suivi de sa limite."""
    lignes = [f"DIAGNOSTIC D'IMPLANTATION — {diagnostic.territoire.adresse}"]
    lignes += entete(diagnostic)
    for section in sections(diagnostic):
        lignes += ["", section.titre]
        lignes += [
            f"   ↳ Limite : {contenu}" if nature == "limite" else f"   {contenu}"
            for nature, contenu in section.elements
        ]
    return "\n".join(lignes)


def diagnostic_en_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    """Diagnostic sérialisable en JSON : dates au format ISO, mêmes noms de champs."""

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
    return convertir(contenu)


_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip


def _co2_evite(kg: float) -> str:
    """Le décalage vers la nuit peut **augmenter** les émissions en été (solaire de midi)."""
    if kg >= 0:
        return f"{_nombre(kg, 1)} kgCO₂ évités"
    return (
        f"mais {_nombre(-kg, 1)} kgCO₂ émis en plus : les heures creuses de nuit sont"
        " plus carbonées que la mi-journée solaire"
    )


_STATUTS = {"excedentaire": "excédentaire", "deficitaire": "déficitaire"}


def _controle_sur_le_passe(p: PrixEntreprise) -> str:
    """Combien de fois la méthode des scénarios a encadré le prix réel, par le passé."""
    if not p.nb_periodes_controlees or p.premiere_origine_controlee is None:
        return "Historique trop court pour contrôler la méthode sur le passé."
    total = p.nb_periodes_controlees
    dans = p.nb_dans_eventail or 0
    return (
        f"Contrôle sur le passé : appliquée à chaque semestre de"
        f" {_semestre(p.premiere_origine_controlee)} à"
        f" {_semestre(p.derniere_origine_controlee or p.premiere_origine_controlee)},"
        f" la méthode a encadré le prix moyen réel des 3 années suivantes {dans} fois sur"
        f" {total} ({_nombre(dans / total * 100)} %, contre 80 % pour une méthode bien"
        f" calibrée) ; il a dépassé le scénario haut {p.nb_au_dessus or 0} fois et est passé"
        f" sous le scénario bas {p.nb_en_dessous or 0} fois. Ces périodes de 3 ans se"
        " chevauchent : ce ne sont pas des essais indépendants. Leurs écarts font désormais"
        " partie des erreurs qui calibrent la fourchette affichée."
    )


def _semestre(code: str, *, precedent: bool = False) -> str:
    """« 2026-S1 » -> « S1 2026 » ; `precedent` donne le semestre d'avant."""
    annee, numero = int(code[:4]), int(code[-1])
    if precedent:
        annee, numero = (annee, 1) if numero == 2 else (annee - 1, 2)
    return f"S{numero} {annee}"


def _euros_arrondis(valeur: float) -> str:
    """Trois chiffres significatifs : 67 375 -> 67 400, 5 496 000 -> 5 500 000.

    Au-delà, la précision serait illusoire : le prix est une moyenne de tranche.
    """
    if valeur <= 0:
        return f"{_nombre(valeur)} €"
    chiffres = len(str(int(valeur)))
    return f"{_nombre(round(valeur, min(0, 3 - chiffres)))} €"


def _nombre_court(valeur: float) -> str:
    """« 0,5 », « 12 » : décimales utiles seulement."""
    return _nombre(valeur, 3).rstrip("0").rstrip(",")
