from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import UTC, date, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ingestion import cli, datasets, pipeline
from ingestion.config import Settings
from ingestion.geocode import AdresseIntrouvableError
from ingestion.tests import medallion
from ingestion.tests.test_diagnostic import territoire
from ingestion.transform import records_to_table, write_parquet
from ingestion.warehouse import build_loader

MAINTENANT = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)


# Pas de `no_network` ici : PySpark parle à sa JVM par une socket locale
@pytest.fixture
def commande(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Callable[..., int]:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return lambda *arguments: cli.main([str(a) for a in arguments])


def _lignes(settings: Settings, spec: datasets.DatasetSpec) -> int:
    loader = build_loader(settings, spec=spec, read_only=True)
    try:
        return loader.row_count()
    finally:
        loader.close()


# Client d'API réduit à ce que le pipeline en attend : un contexte, des lignes, un compteur
class _Client:
    calls = 1

    def __init__(self, **methodes: Callable[[], list[dict[str, Any]]]) -> None:
        self._methodes = methodes

    def __call__(self, settings: Settings) -> _Client:
        return self

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *erreur: object) -> None:
        return None

    def __getattr__(self, nom: str) -> Callable[..., list[dict[str, Any]]]:
        return lambda *args, **kwargs: self._methodes[nom]()


# (enchaînement, extractions remplacées par les lignes de la base synthétique, tables chargées)
CHAINES = [
    (
        "run_zonage_sismique_ingestion",
        {"telecharger_zonage": medallion.zonage_sismique},
        [(datasets.ZONAGE_SISMIQUE, medallion.zonage_sismique)],
    ),
    (
        "run_zones_repartition_ingestion",
        {"telecharger_zones": medallion.zones_repartition_eaux},
        [(datasets.ZONES_REPARTITION_EAUX, medallion.zones_repartition_eaux)],
    ),
    (
        "run_gaspar_ingestion",
        {
            "telecharger_archive": lambda: b"archive",
            "lire_plans": medallion.gaspar_plans,
            "lire_export": medallion.gaspar_risques,
        },
        [
            (datasets.GASPAR_PLANS, medallion.gaspar_plans),
            (datasets.GASPAR_RISQUES, medallion.gaspar_risques),
        ],
    ),
    (
        "run_production_ingestion",
        {
            "telecharger_centres": medallion.communes_centres,
            "telecharger_sites": medallion.sites_production,
        },
        [
            (datasets.COMMUNES_CENTRES, medallion.communes_centres),
            (datasets.SITES_PRODUCTION, medallion.sites_production),
        ],
    ),
    (
        "run_fret_ingestion",
        {
            "telecharger_ports": medallion.ports_fret,
            "telecharger_aeroports": medallion.aeroports_fret,
            "telecharger_chantiers": medallion.chantiers_rail_route,
        },
        [
            (datasets.PORTS_FRET, medallion.ports_fret),
            (datasets.AEROPORTS_FRET, medallion.aeroports_fret),
            (datasets.CHANTIERS_RAIL_ROUTE, medallion.chantiers_rail_route),
        ],
    ),
    (
        "run_locaux_ingestion",
        {"telecharger_ventes_locaux": medallion.ventes_locaux},
        [(datasets.VENTES_LOCAUX, medallion.ventes_locaux)],
    ),
    (
        "run_friches_ingestion",
        {"telecharger_friches": medallion.friches},
        [(datasets.FRICHES, medallion.friches)],
    ),
    (
        "run_edf_sei_ingestion",
        {"telecharger_production": medallion.edf_sei_production},
        [(datasets.EDF_SEI_PRODUCTION, medallion.edf_sei_production)],
    ),
    (
        "run_enedis_entreprises_ingestion",
        {"telecharger_conso_entreprises": medallion.conso_entreprises},
        [(datasets.ENEDIS_CONSO_ENTREPRISES, medallion.conso_entreprises)],
    ),
    (
        "run_profils_charge_ingestion",
        {"telecharger_profils_charge": medallion.profils_charge_sup36},
        [(datasets.PROFILS_CHARGE_SUP36, medallion.profils_charge_sup36)],
    ),
    (
        "run_continuite_ingestion",
        {"telecharger_continuite": medallion.continuite_alimentation},
        [(datasets.CONTINUITE_ALIMENTATION, medallion.continuite_alimentation)],
    ),
    (
        "run_europe_ingestion",
        {
            "EurostatClient": _Client(fetch_prix_entreprises_ue=medallion.prix_ue),
            "telecharger_intensites": medallion.ember_intensites,
        },
        [
            (datasets.EUROSTAT_PRIX_UE, medallion.prix_ue),
            (datasets.EMBER_INTENSITE_CARBONE, medallion.ember_intensites),
        ],
    ),
    (
        "run_prix_entreprises_ingestion",
        {"EurostatClient": _Client(fetch_prix_entreprises=medallion.prix_entreprises)},
        [(datasets.EUROSTAT_PRIX_ENTREPRISES, medallion.prix_entreprises)],
    ),
    (
        "run_ecowatt_ingestion",
        {"RteEcowattClient": _Client(fetch_signaux=medallion.ecowatt)},
        [(datasets.RTE_ECOWATT, medallion.ecowatt)],
    ),
]


# Chaque source suit la même chaîne : extraction, Parquet bronze, MERGE. La rejouer ne duplique
# rien dans le warehouse et n'écrase jamais le Parquet du premier passage
@pytest.mark.parametrize(
    ("enchainement", "extractions", "tables"), CHAINES, ids=[chaine[0] for chaine in CHAINES]
)
def test_chaine_de_chargement_idempotente(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    enchainement: str,
    extractions: dict[str, Any],
    tables: list[tuple[datasets.DatasetSpec, Callable[[], list[dict[str, Any]]]]],
) -> None:
    for nom, remplacant in extractions.items():
        if isinstance(remplacant, _Client):
            monkeypatch.setattr(pipeline, nom, remplacant)
        else:
            monkeypatch.setattr(pipeline, nom, lambda *a, _r=remplacant, **k: _r())
    lancer = getattr(pipeline, enchainement)

    lancer(settings=settings, now=MAINTENANT)
    premier = {spec.table_name: _lignes(settings, spec) for spec, _ in tables}
    lancer(settings=settings, now=MAINTENANT)

    for spec, lignes in tables:
        assert 0 < premier[spec.table_name] <= len(lignes())
        assert _lignes(settings, spec) == premier[spec.table_name]
        fichiers = list(spec.bronze_dataset_dir(settings.bronze_dir).rglob("*.parquet"))
        assert len(fichiers) == 2


def test_chaine_tempo(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "RteTempoClient", _Client(fetch_calendar=medallion.tempo))
    jours = pipeline.tempo_days_for_month(date(2026, 6, 1))
    assert (jours.start, jours.end) == (date(2026, 5, 31), date(2026, 7, 1))

    extraction, chargement = pipeline.run_tempo_ingestion(jours, settings=settings, now=MAINTENANT)
    assert extraction.rows == chargement.rows_inserted == len(medallion.tempo())
    assert (
        pipeline.run_tempo_ingestion(jours, settings=settings, now=MAINTENANT)[1].rows_inserted == 0
    )


def test_geocode_affiche_le_territoire(
    commande: Callable[..., int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "geocode", lambda *a, **k: territoire("69123", "Lyon"))
    assert commande("geocode", "--adresse", "Lyon") == 0
    sortie = capsys.readouterr().out
    assert "commune           : Lyon (69123)" in sortie
    assert "region            :  (code inconnu)" in sortie

    def introuvable(*args: object, **kwargs: object) -> None:
        raise AdresseIntrouvableError("nulle part")

    monkeypatch.setattr(cli, "geocode", introuvable)
    assert commande("geocode", "--adresse", "nulle part") == 1


# Warehouse vide : les deux contrôles échouent avec un message, sans trace Python
@pytest.mark.parametrize(
    ("controle", "message"),
    [("freshness", "donnee obsolete"), ("coverage", "mois incomplet")],
)
def test_controles_sur_un_warehouse_vide(
    commande: Callable[..., int],
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    controle: str,
    message: str,
) -> None:
    pipeline.ensure_bronze_tables(settings=settings)
    assert commande(controle) == 1
    assert message in caplog.text


def test_load_charge_un_parquet_deja_extrait(
    commande: Callable[..., int], settings: Settings, tmp_path: Path
) -> None:
    table = records_to_table(
        medallion.temps_reel(), spec=datasets.NATIONAL_TR, ingested_at=MAINTENANT
    )
    fichier = write_parquet(
        table, dataset_dir=tmp_path / "extrait", run_id="test", ingest_date=MAINTENANT.date()
    )
    assert commande("load", fichier) == 0
    assert _lignes(settings, datasets.NATIONAL_TR) == table.num_rows
    # Chargé deux fois, compté une fois
    assert commande("load", fichier) == 0
    assert _lignes(settings, datasets.NATIONAL_TR) == table.num_rows


@pytest.mark.parametrize(
    ("commande_cli", "fonction", "jeu"),
    [
        ("extract", "extract_to_parquet", datasets.NATIONAL_TR),
        ("ingest", "run_ingestion", datasets.NATIONAL_TR),
        ("consolidate", "run_consolidation", datasets.NATIONAL_CONS_DEF),
    ],
)
def test_commandes_d_entretien_passent_le_jeu_et_la_fenetre(
    commande: Callable[..., int],
    monkeypatch: pytest.MonkeyPatch,
    commande_cli: str,
    fonction: str,
    jeu: datasets.DatasetSpec,
) -> None:
    appels: list[dict[str, Any]] = []
    resultat = pipeline.LoadResult("t", 1, 1, 1, 0)

    def espion(**kwargs: Any) -> Any:
        appels.append(kwargs)
        return "extraction" if commande_cli == "extract" else ("extraction", resultat)

    monkeypatch.setattr(cli, fonction, espion)
    fenetre = (
        ["--months", 2]
        if commande_cli == "consolidate"
        else ["--start", "2026-06-01T00:00:00+00:00", "--end", "2026-06-02T00:00:00+00:00"]
    )
    assert commande(commande_cli, *fenetre, "--run-id", "essai") == 0
    assert appels[0]["spec"] == jeu and appels[0]["run_id"] == "essai"
    if commande_cli == "consolidate":
        assert appels[0]["months"] == 2
    else:
        assert appels[0]["window"].start == datetime(2026, 6, 1, tzinfo=UTC)
        with pytest.raises(SystemExit, match="vont par paire"):
            commande(commande_cli, "--start", "2026-06-01T00:00:00+00:00")


# Sans compte RTE, `tempo` et `ecowatt` disent quoi renseigner et n'appellent personne
@pytest.mark.parametrize("arguments", [["tempo", "--month", "2026-06"], ["ecowatt"]])
def test_sans_identifiants_rte(
    commande: Callable[..., int], caplog: pytest.LogCaptureFixture, arguments: list[str]
) -> None:
    assert commande(*arguments) == 1
    assert "absents : renseigner ECO2MIX_" in caplog.text


def test_tempo_demande_une_periode(commande: Callable[..., int]) -> None:
    with pytest.raises(SystemExit, match="--month, ou --start et --end"):
        commande("tempo")


def test_report_passe_le_mois_et_le_format(
    commande: Callable[..., int], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from reporting import monthly

    appels: list[tuple[date, dict[str, Any]]] = []

    def espion(mois: date, **kwargs: Any) -> Path:
        appels.append((mois, kwargs))
        return tmp_path / "rapport.md"

    monkeypatch.setattr(monthly, "generate_monthly_report", espion)
    sortie = tmp_path / "rapport.md"
    assert commande("report", "--month", "2026-06", "--format", "md", "--output", sortie) == 0
    mois, options = appels[0]
    assert mois == date(2026, 6, 1)
    assert options["format"] == "md" and options["output"] == sortie and options["graphiques"]


def test_app_sert_puis_s_arrete_proprement(
    commande: Callable[..., int], monkeypatch: pytest.MonkeyPatch
) -> None:
    import webbrowser

    ouvertes: list[str] = []
    monkeypatch.setattr(webbrowser, "open", ouvertes.append)

    def interrompre(self: ThreadingHTTPServer) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", interrompre)
    assert commande("app", "--port", 0) == 0
    assert ouvertes and ouvertes[0].startswith("http://127.0.0.1:")
    assert commande("app", "--port", 0, "--sans-ouvrir") == 0
    assert len(ouvertes) == 1


def test_app_port_deja_pris(commande: Callable[..., int], caplog: pytest.LogCaptureFixture) -> None:
    with socket.socket() as occupe:
        occupe.bind(("127.0.0.1", 0))
        occupe.listen()
        assert commande("app", "--port", occupe.getsockname()[1], "--sans-ouvrir") == 1
    assert "indisponible" in caplog.text
