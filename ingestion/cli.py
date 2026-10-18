from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from ingestion.config import get_settings
from ingestion.datasets import NATIONAL_CONS_DEF, NATIONAL_TR, SPECS, get_spec
from ingestion.diagnostic import (
    DiagnosticIndisponibleError,
    completer_risques,
    essentiel_en_dict,
    formater_diagnostic,
    formater_essentiel,
    lire_diagnostic,
)
from ingestion.extract_odre import ExtractionWindow
from ingestion.extract_rte import DayRange, RteCredentialsMissingError
from ingestion.geocode import AdresseIntrouvableError, GeocodingError, geocode
from ingestion.pipeline import (
    IncompleteMonthError,
    StaleDataError,
    check_freshness,
    check_month_coverage,
    extract_to_parquet,
    load_to_warehouse,
    run_consolidation,
    run_ingestion,
    run_prix_entreprises_ingestion,
    run_tempo_ingestion,
    tempo_days_for_month,
)

logger = logging.getLogger("ingestion")


# Accepte `2026-09-07` ou `2026-09-07T05:00:00`, interprétés en UTC
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


def _parse_puissance(value: str) -> float:
    try:
        puissance = float(value.replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError(f"puissance invalide : {value!r} (MW)") from None
    if puissance < 0:
        raise argparse.ArgumentTypeError("la puissance doit être positive")
    return puissance


def _parse_conso(value: str) -> float:
    try:
        conso = float(value.replace(",", ".").replace(" ", ""))
    except ValueError:
        raise argparse.ArgumentTypeError(f"consommation invalide : {value!r} (MWh/an)") from None
    if conso <= 0:
        raise argparse.ArgumentTypeError("la consommation doit être strictement positive")
    return conso


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
    essentiel.add_argument(
        "--conso",
        type=_parse_conso,
        help="consommation annuelle en MWh (défaut : tranche des petites entreprises)",
    )
    essentiel.add_argument(
        "--format", choices=["texte", "json"], default="texte", help="texte ou json"
    )

    diagnose = subparsers.add_parser(
        "diagnose", help="diagnostic d'implantation électrique d'une adresse"
    )
    diagnose.add_argument("--adresse", required=True, help="adresse à diagnostiquer")
    diagnose.add_argument(
        "--puissance", type=_parse_puissance, help="puissance du projet en MW (ex. 0.5, 12)"
    )
    diagnose.add_argument(
        "--conso",
        type=_parse_conso,
        help="consommation annuelle en MWh (ex. 350) : prix de la tranche Eurostat",
    )
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
        help="ne consulte pas Géorisques (usage hors ligne)",
    )
    diagnose.add_argument(
        "--sans-graphiques",
        action="store_true",
        help="Markdown uniquement : texte seul, sans images PNG",
    )

    compare = subparsers.add_parser(
        "compare", help="compare plusieurs adresses côte à côte, sans classement"
    )
    compare.add_argument(
        "--adresse",
        action="append",
        required=True,
        help="adresse à comparer (répéter l'option : 2 à 6 adresses)",
    )
    compare.add_argument(
        "--puissance", type=_parse_puissance, help="puissance du projet en MW (ex. 0.5, 12)"
    )
    compare.add_argument(
        "--conso", type=_parse_conso, help="consommation annuelle en MWh : prix de la tranche"
    )
    compare.add_argument(
        "--format",
        choices=["texte", "md", "csv", "json"],
        default="texte",
        help="tableau en texte (terminal), Markdown, CSV (tableur) ou JSON",
    )
    compare.add_argument("--output", help="fichier de sortie (défaut : sortie standard)")
    compare.add_argument("--sans-risques", action="store_true", help="ne consulte pas Géorisques")

    carte = subparsers.add_parser(
        "carte", help="carte 3D des territoires : relief, industrie, réseau (page HTML)"
    )
    carte.add_argument("--adresse", help="adresse à épingler sur la carte, entre guillemets")
    carte.add_argument("--output", help="chemin du fichier (défaut : data/reports/carte.html)")
    carte.add_argument(
        "--sans-ouvrir", action="store_true", help="écrit la page sans ouvrir le navigateur"
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
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
                logger.error("geocodage impossible : %s", error)
                return 1
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
                territoire = geocode(args.adresse, settings=settings)
                diagnostic = lire_diagnostic(territoire, conso_mwh=args.conso, settings=settings)
            except AdresseIntrouvableError as error:
                logger.error(
                    "adresse introuvable : %s. Preciser le numero, la rue et la commune"
                    " (adresse en France uniquement).",
                    error,
                )
                return 1
            except GeocodingError as error:
                logger.error("geocodage impossible : %s", error)
                return 1
            except DiagnosticIndisponibleError as error:
                logger.error("donnees indisponibles : %s", error)
                return 1
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if args.format == "json":
                print(json.dumps(essentiel_en_dict(diagnostic), ensure_ascii=False, indent=2))
            else:
                print(formater_essentiel(diagnostic))
        case "diagnose":
            try:
                territoire = geocode(args.adresse, settings=settings)
                diagnostic = lire_diagnostic(
                    territoire,
                    puissance_mw=args.puissance,
                    conso_mwh=args.conso,
                    settings=settings,
                )
            except (GeocodingError, DiagnosticIndisponibleError) as error:
                logger.error("diagnostic impossible : %s", error)
                return 1
            if not args.sans_risques:
                diagnostic = completer_risques(diagnostic, settings=settings)
            # Sortie redirigée sous Windows : cp1252 ne connaît ni « → » ni « ≤ »
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
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
                )
                logger.info("rapport ecrit %s", path)
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
                    diagnostic = lire_diagnostic(
                        geocode(adresse, settings=settings),
                        puissance_mw=args.puissance,
                        conso_mwh=args.conso,
                        settings=settings,
                    )
                except (GeocodingError, DiagnosticIndisponibleError) as error:
                    logger.error("comparaison impossible pour %r : %s", adresse, error)
                    return 1
                if not args.sans_risques:
                    diagnostic = completer_risques(diagnostic, settings=settings)
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
                logger.info("comparaison ecrite %s", chemin)
            else:
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8")
                print(sortie)
        case "carte":
            # Import paresseux : la carte n'est utile qu'à cette commande
            import webbrowser

            from reporting.carte import generer_carte

            try:
                territoire = geocode(args.adresse, settings=settings) if args.adresse else None
                path = generer_carte(
                    settings=settings,
                    territoire=territoire,
                    output=Path(args.output) if args.output else None,
                )
            except (GeocodingError, DiagnosticIndisponibleError) as error:
                logger.error("carte impossible : %s", error)
                return 1
            logger.info("carte ecrite %s", path)
            if not args.sans_ouvrir:
                webbrowser.open(path.resolve().as_uri())
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
