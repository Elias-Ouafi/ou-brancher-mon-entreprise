from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from ingestion.config import Settings, get_settings
from ingestion.courbe import Courbe, CourbeError, lire_courbe
from ingestion.datasets import NATIONAL_CONS_DEF, NATIONAL_TR, SPECS, get_spec
from ingestion.diagnostic import (
    ACTIVITES,
    Diagnostic,
    DiagnosticIndisponibleError,
    DonneesAbsentesError,
    avec_risques_locaux,
    completer_acces_routier,
    completer_eau,
    completer_risques,
    completer_urbanisme,
    completer_zones_activite,
    essentiel_en_dict,
    formater_diagnostic,
    formater_essentiel,
    lire_diagnostic,
)
from ingestion.extract_odre import ExtractionWindow
from ingestion.extract_rte import DayRange, RteCredentialsMissingError
from ingestion.geocode import (
    AdresseAmbigueError,
    AdresseIntrouvableError,
    GeocodingError,
    geocode,
)
from ingestion.pipeline import (
    IncompleteMonthError,
    StaleDataError,
    check_freshness,
    check_month_coverage,
    extract_to_parquet,
    load_to_warehouse,
    run_consolidation,
    run_ecowatt_ingestion,
    run_ingestion,
    run_prix_entreprises_ingestion,
    run_tempo_ingestion,
    tempo_days_for_month,
)

logger = logging.getLogger("ingestion")

# Commandes qui répondent à une question : seule la réponse s'affiche, sans journal technique
QUESTIONS = frozenset({"essentiel", "diagnose", "compare", "carte", "europe", "geocode"})

COMMANDE_REFRESH = "python -m ingestion.cli refresh --months 180"


def _donnees_absentes(error: DonneesAbsentesError) -> int:
    logger.error(
        "Aucune donnée utilisable (%s). Lancez d'abord, une seule fois (une minute et demie"
        " environ) :\n    %s\npuis reposez la question.",
        error,
        COMMANDE_REFRESH,
    )
    return 1


# Fichier produit par une commande : annoncé après la réponse, même quand le journal est masqué
def _signaler(message: str) -> None:
    sys.stdout.flush()
    print(message, file=sys.stderr)


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _window_from(args: argparse.Namespace) -> ExtractionWindow | None:
    if args.start or args.end:
        if not (args.start and args.end):
            raise SystemExit("--start et --end vont par paire")
        return ExtractionWindow(start=args.start, end=args.end)
    if args.hours:
        return ExtractionWindow.last_hours(args.hours)
    return None


def _parse_month(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"mois invalide : {value!r} (AAAA-MM)") from None


# Nombre saisi à la française (« 1 500,5 ») ; `strict` refuse zéro
def _positif(nom: str, unite: str, *, strict: bool = True):
    def lire(value: str) -> float:
        try:
            nombre = float(value.replace(",", ".").replace(" ", ""))
        except ValueError:
            raise argparse.ArgumentTypeError(f"{nom} invalide : {value!r} ({unite})") from None
        if nombre < 0 or (strict and nombre == 0):
            raise argparse.ArgumentTypeError(f"{nom} : nombre positif attendu ({unite})")
        return nombre

    return lire


_parse_puissance = _positif("puissance", "MW", strict=False)
_parse_conso = _positif("consommation", "MWh/an")
_parse_euros = _positif("montant", "€")


def _add_projet_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--puissance", type=_parse_puissance, help="puissance du projet en MW : qui raccorde"
    )
    parser.add_argument(
        "--conso",
        type=_parse_conso,
        help="consommation annuelle en MWh (défaut : tranche des petites entreprises)",
    )
    parser.add_argument(
        "--ca", type=_parse_euros, help="chiffre d'affaires annuel en euros : part de la facture"
    )
    parser.add_argument(
        "--va",
        type=_parse_euros,
        help="valeur ajoutée annuelle en euros : électro-intensité fiscale",
    )
    parser.add_argument(
        "--surface",
        type=_positif("surface", "m²"),
        help="surface des locaux cherchés en m² : coût d'achat d'après les ventes DVF",
    )
    parser.add_argument(
        "--activite",
        choices=sorted(ACTIVITES),
        help="activité du projet : profil de charge et régimes d'accise (taux non chiffrés)",
    )
    parser.add_argument(
        "--courbe",
        help="export CSV de la courbe de charge Enedis : vraie consommation, pointe et carbone",
    )


def _add_carte_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sans-carte",
        action="store_true",
        help="ne place pas l'adresse sur la carte (data/reports/carte.html)",
    )


def _lire_courbe(chemin: str | None) -> Courbe | None:
    return None if chemin is None else lire_courbe(Path(chemin).read_bytes())


def _diagnostiquer(adresse: str, args: argparse.Namespace, settings: Settings) -> Diagnostic:
    return lire_diagnostic(
        geocode(adresse, settings=settings),
        puissance_mw=args.puissance,
        conso_mwh=args.conso,
        ca_eur=args.ca,
        va_eur=args.va,
        surface_m2=args.surface,
        activite=args.activite,
        courbe=_lire_courbe(args.courbe),
        settings=settings,
    )


def _echec(error: Exception) -> int:
    match error:
        case DonneesAbsentesError():
            return _donnees_absentes(error)
        case AdresseIntrouvableError():
            logger.error(
                "adresse introuvable : %s. Preciser le numero, la rue et la commune"
                " (adresse en France uniquement).",
                error,
            )
        case AdresseAmbigueError():
            logger.error("adresse ambigue : %s. Preciser le code postal ou la commune.", error)
        case GeocodingError():
            logger.error("geocodage impossible : %s", error)
        case DiagnosticIndisponibleError():
            logger.error("donnees indisponibles : %s", error)
        case _:
            logger.error("courbe de charge illisible : %s", error)
    return 1


ECHECS_PREVUS = (GeocodingError, DiagnosticIndisponibleError, CourbeError, OSError)


# La carte montre le verdict de l'adresse ; son échec ne fait pas échouer la commande
def _placer_sur_carte(diagnostic: Diagnostic, settings: Settings) -> None:
    import duckdb
    import httpx

    from ingestion.extract_odre import RetryableStatusError
    from reporting.carte import mettre_a_jour_carte, verdict_carte

    try:
        path = mettre_a_jour_carte(
            settings=settings,
            territoire=diagnostic.territoire,
            verdict_adresse=verdict_carte(avec_risques_locaux(diagnostic, settings=settings)),
        )
    except (
        DiagnosticIndisponibleError,
        httpx.HTTPError,
        RetryableStatusError,
        duckdb.Error,
        OSError,
        ValueError,
    ) as error:
        logger.warning("carte non mise a jour : %s", error)
        return
    _signaler(f"Carte : {path}")


def _commande(argv: list[str] | None) -> str:
    arguments = sys.argv[1:] if argv is None else argv
    cites = [f'"{a}"' if not a or " " in a else a for a in arguments]
    return " ".join(["python -m ingestion.cli", *cites])


def _add_dataset_option(parser: argparse.ArgumentParser, default: str) -> None:
    parser.add_argument("--dataset", choices=sorted(SPECS), default=default, help="dataset ODRÉ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eco2mix",
        description=__doc__,
        # Sans cela, argparse recompacte les exemples de la docstring en un pavé
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", action="store_true", help="passe les logs en DEBUG")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("extract", "extrait une fenêtre vers un Parquet, sans charger"),
        ("ingest", "extrait puis charge dans le warehouse"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--hours", type=int, help="profondeur de la fenêtre glissante")
        sub.add_argument("--start", type=_parse_instant, help="borne de début (UTC, incluse)")
        sub.add_argument("--end", type=_parse_instant, help="borne de fin (UTC, exclue)")
        sub.add_argument("--run-id", default="manual", help="suffixe du fichier Parquet produit")
        _add_dataset_option(sub, NATIONAL_TR.dataset_id)

    consolidate = subparsers.add_parser(
        "consolidate", help="relit les N derniers mois du dataset consolidé/définitif"
    )
    consolidate.add_argument("--months", type=int, help="profondeur en mois civils")
    consolidate.add_argument("--run-id", default="manual", help="suffixe du fichier Parquet")
    _add_dataset_option(consolidate, NATIONAL_CONS_DEF.dataset_id)

    refresh = subparsers.add_parser(
        "refresh", help="rafraîchit toutes les sources du diagnostic, puis lance dbt build"
    )
    refresh.add_argument(
        "--months", type=int, help="profondeur relue dans l'éCO2mix consolidé (180 : depuis 2012)"
    )

    load = subparsers.add_parser("load", help="charge un Parquet déjà extrait")
    load.add_argument("parquet_path", help="chemin du fichier Parquet")
    _add_dataset_option(load, NATIONAL_TR.dataset_id)

    freshness = subparsers.add_parser(
        "freshness", help="contrôle le retard de la donnée la plus récente"
    )
    _add_dataset_option(freshness, NATIONAL_TR.dataset_id)

    geocode_cmd = subparsers.add_parser(
        "geocode", help="résout une adresse en territoire administratif"
    )
    geocode_cmd.add_argument(
        "--adresse", required=True, help="adresse à résoudre, entre guillemets"
    )

    essentiel = subparsers.add_parser(
        "essentiel", help="prix moyen de l'électricité et verdict carbone d'une adresse"
    )
    essentiel.add_argument("--adresse", required=True, help="adresse, entre guillemets")
    _add_projet_options(essentiel)
    essentiel.add_argument(
        "--format", choices=["texte", "json"], default="texte", help="texte ou json"
    )
    _add_carte_option(essentiel)

    diagnose = subparsers.add_parser(
        "diagnose", help="diagnostic d'implantation électrique d'une adresse"
    )
    diagnose.add_argument("--adresse", required=True, help="adresse à diagnostiquer")
    _add_projet_options(diagnose)
    diagnose.add_argument(
        "--format",
        choices=["texte", "md", "pdf", "json"],
        default="texte",
        help="texte (terminal), rapport md ou pdf, ou json (sortie standard sans --output)",
    )
    diagnose.add_argument("--output", help="chemin du fichier (défaut : data/reports/)")
    diagnose.add_argument(
        "--sans-risques",
        action="store_true",
        help="ne consulte ni Géorisques, ni l'urbanisme, ni l'eau, ni la BD TOPO (hors ligne)",
    )
    diagnose.add_argument(
        "--sans-graphiques",
        action="store_true",
        help="Markdown uniquement : texte seul, sans images PNG",
    )
    _add_carte_option(diagnose)

    compare = subparsers.add_parser(
        "compare", help="compare plusieurs adresses côte à côte, sans classement"
    )
    compare.add_argument(
        "--adresse",
        action="append",
        required=True,
        help="adresse à comparer (répéter l'option : 2 à 6 adresses)",
    )
    _add_projet_options(compare)
    compare.add_argument(
        "--format",
        choices=["texte", "md", "csv", "json"],
        default="texte",
        help="tableau en texte (terminal), Markdown, CSV (tableur) ou JSON",
    )
    compare.add_argument("--output", help="fichier de sortie (défaut : sortie standard)")
    compare.add_argument(
        "--sans-risques",
        action="store_true",
        help="ne consulte ni Géorisques, ni le Géoportail de l'urbanisme, ni VigiEau",
    )

    carte = subparsers.add_parser(
        "carte", help="carte 3D par système électrique : raccorder et territoire"
    )
    carte.add_argument("--adresse", help="adresse à épingler sur la carte, entre guillemets")
    carte.add_argument(
        "--puissance", type=_parse_puissance, help="puissance du projet en MW : qui raccorde"
    )
    carte.add_argument("--output", help="chemin du fichier (défaut : data/reports/carte.html)")
    carte.add_argument(
        "--sans-ouvrir", action="store_true", help="écrit la page sans ouvrir le navigateur"
    )
    carte.add_argument(
        "--reconstruire",
        action="store_true",
        help="refait toute la carte, même si ses données n'ont pas changé",
    )

    europe = subparsers.add_parser(
        "europe", help="prix et carbone moyens d'un pays de l'UE, face à la France"
    )
    europe.add_argument(
        "--adresse", required=True, help="adresse dans l'UE, entre guillemets (ex. Milano)"
    )
    europe.add_argument(
        "--conso",
        type=_parse_conso,
        help="consommation annuelle en MWh (défaut : tranche des petites entreprises)",
    )

    coverage = subparsers.add_parser(
        "coverage", help="contrôle la complétude du dernier mois révolu"
    )
    _add_dataset_option(coverage, NATIONAL_CONS_DEF.dataset_id)

    tempo = subparsers.add_parser("tempo", help="charge le calendrier Tempo (API RTE)")
    tempo.add_argument("--month", type=_parse_month, help="mois AAAA-MM (veille du 1er comprise)")
    tempo.add_argument("--start", type=date.fromisoformat, help="premier jour (inclus)")
    tempo.add_argument("--end", type=date.fromisoformat, help="dernier jour (exclu)")
    tempo.add_argument("--run-id", default="manual", help="suffixe du fichier Parquet")
    ecowatt = subparsers.add_parser(
        "ecowatt", help="relève le signal Ecowatt du jour à J+3 (API RTE, sans historique)"
    )
    ecowatt.add_argument("--run-id", default="manual", help="suffixe du fichier Parquet")

    prix = subparsers.add_parser(
        "prix", help="charge les prix de l'electricite des entreprises (Eurostat)"
    )
    prix.add_argument("--run-id", default="manual", help="suffixe du fichier Parquet")

    report = subparsers.add_parser("report", help="génère le rapport d'un mois (PDF ou Markdown)")
    report.add_argument("--month", type=_parse_month, required=True, help="mois AAAA-MM")
    report.add_argument("--format", choices=["pdf", "md"], default="pdf", help="format du rapport")
    report.add_argument("--output", help="chemin du fichier (défaut : data/reports/)")
    report.add_argument(
        "--sans-graphiques",
        action="store_true",
        help="Markdown uniquement : texte et tableaux, sans images PNG",
    )
    # Aussi après la commande ; SUPPRESS pour ne pas écraser `--verbose` placé avant elle
    for sub in subparsers.choices.values():
        sub.add_argument(
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="affiche le journal technique (DEBUG)",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for option in ("ca", "va"):
        if getattr(args, option, None) is not None and args.conso is None and not args.courbe:
            parser.error(
                f"--{option} demande --conso ou --courbe : le calcul part de la consommation"
                " annuelle"
            )
    # Sortie redirigée sous Windows : cp1252 ne connaît ni « → » ni « ≤ »
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8")
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(levelname)-8s %(name)s %(message)s"
        )
    elif args.command in QUESTIONS:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s %(message)s"
        )
    settings = get_settings()
    spec = get_spec(args.dataset) if hasattr(args, "dataset") else None

    match args.command:
        case "extract":
            outcome = extract_to_parquet(
                settings=settings, spec=spec, window=_window_from(args), run_id=args.run_id
            )
            logger.info("extraction terminee %s", outcome)
        case "ingest":
            outcome, result = run_ingestion(
                settings=settings, spec=spec, window=_window_from(args), run_id=args.run_id
            )
            logger.info("ingestion terminee %s %s", outcome, result.as_dict())
        case "consolidate":
            outcome, result = run_consolidation(
                settings=settings, spec=spec, months=args.months, run_id=args.run_id
            )
            logger.info("consolidation terminee %s %s", outcome, result.as_dict())
        case "refresh":
            from ingestion.refresh import run_refresh

            bilan = run_refresh(settings=settings, months=args.months)
            echecs = [etape for etape in bilan if not etape.reussie]
            logger.info(
                "refresh : %d etape(s) sur %d reussie(s)", len(bilan) - len(echecs), len(bilan)
            )
            if echecs:
                logger.error("refresh incomplet : %s", ", ".join(e.nom for e in echecs))
                return 1
        case "load":
            result = load_to_warehouse(args.parquet_path, settings=settings, spec=spec)
            logger.info("chargement %s", result)
        case "freshness":
            try:
                check_freshness(settings=settings, spec=spec)
            except StaleDataError as error:
                logger.error("donnee obsolete : %s", error)
                return 1
        case "coverage":
            try:
                check_month_coverage(settings=settings, spec=spec)
            except IncompleteMonthError as error:
                logger.error("mois incomplet : %s", error)
                return 1
        case "geocode":
            try:
                territoire = geocode(args.adresse, settings=settings)
            except GeocodingError as error:
                return _echec(error)
            lignes = [
                f"adresse           : {territoire.adresse}",
                f"commune           : {territoire.commune} ({territoire.code_insee_commune})",
                f"departement       : {territoire.departement} ({territoire.code_departement})",
                f"region            : {territoire.region}"
                f" ({territoire.code_insee_region or 'code inconnu'})",
                f"coordonnees       : {territoire.latitude:.5f}, {territoire.longitude:.5f}",
                f"precision / score : {territoire.precision} / {territoire.score:.2f}",
            ]
            print("\n".join(lignes))
        case "essentiel":
            try:
                diagnostic = _diagnostiquer(args.adresse, args, settings)
            except ECHECS_PREVUS as error:
                return _echec(error)
            diagnostic = avec_risques_locaux(diagnostic, settings=settings)
            if args.format == "json":
                print(
                    json.dumps(
                        essentiel_en_dict(diagnostic), ensure_ascii=False, indent=2, default=str
                    )
                )
            else:
                print(formater_essentiel(diagnostic))
            if not args.sans_carte:
                _placer_sur_carte(diagnostic, settings)
        case "diagnose":
            try:
                diagnostic = _diagnostiquer(args.adresse, args, settings)
            except ECHECS_PREVUS as error:
                return _echec(error)
            if not args.sans_risques:
                diagnostic = completer_risques(diagnostic, settings=settings)
                diagnostic = completer_urbanisme(diagnostic, settings=settings)
                diagnostic = completer_eau(diagnostic, settings=settings)
                diagnostic = completer_acces_routier(diagnostic, settings=settings)
                diagnostic = completer_zones_activite(diagnostic, settings=settings)
            if args.format == "texte":
                print(formater_diagnostic(diagnostic))
            elif args.format == "json" and not args.output:
                from reporting.implantation import en_json

                print(en_json(diagnostic))
            else:
                # Import paresseux : matplotlib et reportlab ne servent qu'au rapport
                from reporting.implantation import generer_rapport_implantation

                path = generer_rapport_implantation(
                    diagnostic,
                    format=args.format,
                    settings=settings,
                    output=Path(args.output) if args.output else None,
                    graphiques=not args.sans_graphiques,
                    commande=_commande(argv),
                )
                _signaler(f"Rapport : {path}")
            if not args.sans_carte:
                _placer_sur_carte(diagnostic, settings)
        case "compare":
            from ingestion import comparaison

            if not comparaison.NB_ADRESSES_MIN <= len(args.adresse) <= comparaison.NB_ADRESSES_MAX:
                logger.error(
                    "comparer de %d a %d adresses (%d donnees)",
                    comparaison.NB_ADRESSES_MIN,
                    comparaison.NB_ADRESSES_MAX,
                    len(args.adresse),
                )
                return 1
            diagnostics = []
            for adresse in args.adresse:
                try:
                    diagnostic = _diagnostiquer(adresse, args, settings)
                except ECHECS_PREVUS as error:
                    logger.error("comparaison impossible pour %r", adresse)
                    return _echec(error)
                if not args.sans_risques:
                    diagnostic = completer_risques(diagnostic, settings=settings)
                    diagnostic = completer_urbanisme(diagnostic, settings=settings)
                    diagnostic = completer_eau(diagnostic, settings=settings)
                    diagnostic = completer_acces_routier(diagnostic, settings=settings)
                    diagnostic = completer_zones_activite(diagnostic, settings=settings)
                diagnostics.append(diagnostic)
            rendus = {
                "texte": comparaison.formater_comparaison,
                "md": comparaison.comparaison_en_markdown,
                "csv": comparaison.comparaison_en_csv,
                "json": comparaison.comparaison_en_json,
            }
            sortie = rendus[args.format](diagnostics)
            if args.output:
                chemin = Path(args.output)
                chemin.parent.mkdir(parents=True, exist_ok=True)
                # BOM pour le CSV : Excel le lit alors en UTF-8 sans casser les accents
                encodage = "utf-8-sig" if args.format == "csv" else "utf-8"
                chemin.write_text(sortie, encoding=encodage)
                _signaler(f"Comparaison : {chemin}")
            else:
                print(sortie)
        case "carte":
            # Import paresseux : la carte n'est utile qu'à cette commande
            import webbrowser

            from reporting.carte import generer_carte, mettre_a_jour_carte, verdict_carte

            try:
                territoire = geocode(args.adresse, settings=settings) if args.adresse else None
                verdict_adresse = None
                if territoire is not None:
                    diagnostic = lire_diagnostic(
                        territoire, puissance_mw=args.puissance, settings=settings
                    )
                    verdict_adresse = verdict_carte(
                        avec_risques_locaux(diagnostic, settings=settings)
                    )
                path = (generer_carte if args.reconstruire else mettre_a_jour_carte)(
                    settings=settings,
                    territoire=territoire,
                    verdict_adresse=verdict_adresse,
                    output=Path(args.output) if args.output else None,
                )
            except ECHECS_PREVUS as error:
                return _echec(error)
            _signaler(f"Carte : {path}")
            if not args.sans_ouvrir:
                webbrowser.open(path.resolve().as_uri())
        case "europe":
            from ingestion.europe import formater_europe, lire_pays, localiser

            try:
                localisation = localiser(args.adresse, settings=settings)
                pays, france = lire_pays(
                    localisation.code_pays, conso_mwh=args.conso, settings=settings
                )
            except AdresseIntrouvableError as error:
                logger.error("adresse introuvable : %s. Preciser la ville et le pays.", error)
                return 1
            except ECHECS_PREVUS as error:
                return _echec(error)
            print(formater_europe(localisation, pays, france))
        case "tempo":
            if args.month:
                days = tempo_days_for_month(args.month)
            elif args.start and args.end:
                days = DayRange(args.start, args.end)
            else:
                raise SystemExit("--month, ou --start et --end")
            try:
                outcome, result = run_tempo_ingestion(days, settings=settings, run_id=args.run_id)
            except RteCredentialsMissingError as error:
                logger.error("%s", error)
                return 1
            logger.info("tempo charge %s %s", outcome, result.as_dict())
        case "ecowatt":
            try:
                outcome, result = run_ecowatt_ingestion(settings=settings, run_id=args.run_id)
            except RteCredentialsMissingError as error:
                logger.error("%s", error)
                return 1
            logger.info("ecowatt charge %s %s", outcome, result.as_dict())
        case "prix":
            outcome, result = run_prix_entreprises_ingestion(settings=settings, run_id=args.run_id)
            logger.info("prix entreprises charges %s %s", outcome, result.as_dict())
        case "report":
            # Import paresseux : matplotlib et reportlab ne servent qu'ici
            from reporting.monthly import generate_monthly_report

            output = Path(args.output) if args.output else None
            path = generate_monthly_report(
                args.month,
                settings=settings,
                output=output,
                format=args.format,
                graphiques=not args.sans_graphiques,
            )
            logger.info("rapport ecrit %s", path)
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    sys.exit(main())
