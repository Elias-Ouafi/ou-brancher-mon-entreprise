from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli
from ingestion.config import Settings
from ingestion.diagnostic import CoutProfil, Diagnostic, lire_diagnostic
from ingestion.georisques import lire_rapport
from ingestion.tests.conftest import FIXTURES_DIR
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from reporting import implantation, implantation_charts
from reporting.implantation import generer_rapport_implantation, lire_comparaisons, nom_fichier
from reporting.implantation_pdf import _pdf

pytestmark = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

MAINTENANT = datetime(2026, 10, 10, 9, 0)

COUT = CoutProfil(
    mois=date(2026, 9, 1),
    libelle="Bureau tertiaire, 36 kVA",
    conso_kwh=9_600.0,
    part_hc=0.15,
    cout_htva_eur=1_586.0,
    prix_moyen_htva_eur_kwh=0.1652,
    intensite_moyenne_g_kwh=22.0,
    kwh_decales=528.0,
    gain_decalage_eur=22.0,
    co2_evite_kg=-1.4,
)


@pytest.fixture
def reglages(settings: Settings, medallion_db: Path, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "ban_base_url": "https://ban.test/geocodage",
            "duckdb_path": medallion_db,
            "reports_dir": tmp_path / "rapports",
        }
    )


def _lyon(medallion_db: Path) -> Diagnostic:
    diagnostic = lire_diagnostic(
        territoire("69383", "Lyon"),
        puissance_mw=12,
        conso_mwh=350,
        ca_eur=5_000_000,
        va_eur=1_000_000,
        activite="industrie",
        database=medallion_db,
    )
    rapport = json.loads(
        (FIXTURES_DIR / "georisques_rapport_lyon.json").read_text(encoding="utf-8")
    )
    return replace(diagnostic, cout=COUT, risques=lire_rapport(rapport))


def test_markdown_report_carries_every_limit_and_its_charts(
    medallion_db: Path, reglages: Settings
) -> None:
    diagnostic = _lyon(medallion_db)
    chemin = generer_rapport_implantation(
        diagnostic, format="md", settings=reglages, now=MAINTENANT
    )

    texte = chemin.read_text(encoding="utf-8")
    assert chemin.parent == reglages.reports_dir
    assert texte.startswith("# Diagnostic d'implantation — 1 rue de test, Lyon")
    assert "Établi le 10/10/2026" in texte
    for limite in diagnostic.limites.values():
        assert f"> **Limite.** {limite}" in texte
    # Alignement du terminal retiré, et une limite ne colle jamais à une liste
    assert "Commune     :" not in texte
    assert "\n- " in texte
    assert "\n\n\n" not in texte
    graphiques = chemin.with_name(f"{chemin.stem}_graphiques")
    for nom in ("pression_departements", "couverture_regions", "prix_tranche", "profil_journalier"):
        assert f"]({graphiques.name}/{nom}.png)" in texte
        assert (graphiques / f"{nom}.png").read_bytes().startswith(b"\x89PNG")


def test_markdown_without_charts_writes_no_image(medallion_db: Path, tmp_path: Path) -> None:
    chemin = generer_rapport_implantation(
        _lyon(medallion_db),
        format="md",
        settings=Settings(_env_file=None, duckdb_path=medallion_db),
        output=tmp_path / "sans.md",
        graphiques=False,
    )
    assert "![" not in chemin.read_text(encoding="utf-8")
    assert not (tmp_path / "sans_graphiques").exists()


def test_pdf_report_is_built(medallion_db: Path, reglages: Settings, tmp_path: Path) -> None:
    chemin = generer_rapport_implantation(
        _lyon(medallion_db),
        format="pdf",
        settings=reglages,
        output=tmp_path / "lyon.pdf",
        commande="python -m ingestion.cli diagnose --adresse Lyon --format pdf",
    )
    contenu = chemin.read_bytes()
    assert contenu.startswith(b"%PDF")
    assert len(contenu) > 20_000


# Outre-mer sans équilibre régional, Corse, commune sans site, aucun coût : rien ne casse
@pytest.mark.parametrize("code", ["97411", "2A004", "69029"])
def test_reports_hold_for_territories_with_missing_data(
    medallion_db: Path, reglages: Settings, tmp_path: Path, code: str
) -> None:
    diagnostic = lire_diagnostic(territoire(code), database=medallion_db)
    assert diagnostic.cout is None
    for format in ("md", "pdf"):
        chemin = generer_rapport_implantation(
            diagnostic, format=format, settings=reglages, output=tmp_path / f"{code}.{format}"
        )
        assert chemin.stat().st_size > 0
    # Sans coût, pas de courbe de profil
    assert not (tmp_path / f"{code}_graphiques" / "profil_journalier.png").exists()


def test_department_outside_the_top_is_still_shown(
    medallion_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(medallion_db), read_only=True) as connection:
        comparaisons = lire_comparaisons(connection)
    assert comparaisons.departements[0][0] == "69"
    dernier = comparaisons.departements[-1][0]
    diagnostic = lire_diagnostic(territoire(f"{dernier}001"), database=medallion_db)
    monkeypatch.setattr(implantation_charts, "NB_DEPARTEMENTS_AFFICHES", 1)

    assert implantation_charts.pression_departements(diagnostic, comparaisons) is not None


def test_json_keeps_field_names_and_iso_dates(medallion_db: Path, reglages: Settings) -> None:
    chemin = generer_rapport_implantation(_lyon(medallion_db), format="json", settings=reglages)

    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    assert contenu["code_insee_commune"] == "69123"
    assert contenu["territoire"]["code_insee_commune"] == "69383"
    assert contenu["seuil"]["domaine_tension"] == "HTA"
    assert contenu["cout"]["mois"] == "2026-09-01"
    assert contenu["commune_a_des_sites"] is True
    assert set(contenu["limites"]) >= {"pression_industrielle", "capacite_raccordement"}


def test_unknown_format_is_rejected(medallion_db: Path) -> None:
    with pytest.raises(ValueError, match="format inconnu"):
        generer_rapport_implantation(_lyon(medallion_db), format="docx")  # type: ignore[arg-type]


def test_file_name_is_ascii_and_keyed_by_commune(medallion_db: Path) -> None:
    diagnostic = replace(
        _lyon(medallion_db),
        territoire=territoire("69382", adresse="20 Rue de la République 69002 Lyon"),
    )
    assert (
        nom_fichier(diagnostic, "pdf")
        == "implantation_69123_20-rue-de-la-republique-69002-lyon.pdf"
    )


def test_pdf_text_replaces_glyphs_missing_from_standard_fonts() -> None:
    assert _pdf("BT ≤ 36 kVA") == "BT &lt;= 36 kVA"
    assert _pdf("12 MW → HTA & co") == "12 MW — HTA &amp; co"
    assert _pdf("22 gCO₂/kWh") == "22 gCO<sub>2</sub>/kWh"
    assert _pdf("Commune     : 1 site") == "Commune : 1 site"


def _ban(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [4.85, 45.75]},
                    "properties": {
                        "label": "10 Rue Garibaldi 69003 Lyon",
                        "score": 0.95,
                        "type": "housenumber",
                        "citycode": "69383",
                        "city": "Lyon",
                        "postcode": "69003",
                        "context": "69, Rhône, Auvergne-Rhône-Alpes",
                    },
                }
            ],
        }
    )


def test_diagnose_json_goes_to_standard_output(
    httpx_mock: HTTPXMock,
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ban(httpx_mock)
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)

    code = cli.main(
        [
            "diagnose",
            "--adresse",
            "10 rue Garibaldi, Lyon",
            "--format",
            "json",
            "--sans-risques",
            "--sans-carte",
        ]
    )

    assert code == 0
    contenu = json.loads(capsys.readouterr().out)
    assert contenu["departement"] == "Rhône"
    assert contenu["seuil"] is None
    assert not reglages.reports_dir.exists()


def test_diagnose_writes_the_requested_report(
    httpx_mock: HTTPXMock,
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ban(httpx_mock)
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)
    sortie = tmp_path / "rapport.md"

    code = cli.main(
        [
            "diagnose", "--adresse", "10 rue Garibaldi, Lyon", "--puissance", "0,5",
            "--format", "md", "--output", str(sortie), "--sans-graphiques",
            "--sans-risques", "--sans-carte",
        ]
    )  # fmt: skip

    assert code == 0
    texte = sortie.read_text(encoding="utf-8")
    assert "Puissance 0,5 MW → HTA" in texte
    assert "![" not in texte
    # Pied de page : de quoi refaire le rapport et dater ses données
    assert (
        'Pour le refaire : python -m ingestion.cli diagnose --adresse "10 rue Garibaldi, Lyon"'
        in texte
    )
    assert "Données chargées le 01/07/2026" in texte.split("---")[-1]


@pytest.mark.parametrize("format", implantation.FORMATS)
def test_every_report_format_is_offered_by_the_command(format: str) -> None:
    args = cli.build_parser().parse_args(["diagnose", "--adresse", "x", "--format", format])
    assert args.format == format


def test_diagnose_rejects_unknown_format() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["diagnose", "--adresse", "x", "--format", "docx"])
