from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fontTools.ttLib import TTFont
from pypdf import PdfReader

from ingestion import cli, demo, diagnostic, turpe
from ingestion.config import Settings
from ingestion.extract_geo import CONTOURS_JEU
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from reporting import charte, implantation, implantation_pdf

pytestmark = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

# Sorties de l'exemple de référence figées sur la base synthétique : un libellé qui change sans
# qu'on l'ait voulu fait échouer la comparaison. Changement voulu : relancer ce fichier avec
# `--maj-references`, puis relire le diff des références avant de commiter
REFERENCES = Path(__file__).parent / "references"
JOUR = date(2026, 7, 15)

ADRESSES = {
    "Lyon": demo.TERRITOIRE,
    "Ajaccio": territoire(
        "2A004",
        "Ajaccio",
        departement="Corse-du-Sud",
        code_insee_region="94",
        region="Corse",
        latitude=41.9192,
        longitude=8.7386,
    ),
}
PROJET = ["--puissance", "12", "--conso", "70000", "--activite", "data-center"]
ESSENTIEL = ["essentiel", "--adresse", "Lyon", *PROJET, "--sans-carte"]
DIAGNOSE = ["diagnose", "--adresse", "Lyon", *PROJET, "--sans-risques", "--sans-carte"]
COMPARE = ["compare", "--adresse", "Lyon", "--adresse", "Ajaccio", *PROJET, "--sans-risques"]
# La base synthétique n'a pas de prix pour la tranche IF de l'exemple de référence (exprès : c'est
# elle qui joue « non publié ») : deux autres projets figent les libellés du prix, des scénarios,
# de la HTB et de l'accise réduite
IRVE = ["--adresse", "Lyon", "--puissance", "2", "--conso", "15000", "--activite", "irve"]
INDUSTRIE_HTB = [
    *["--adresse", "Lyon", "--puissance", "45", "--conso", "300000", "--activite", "industrie"],
    *["--ca", "900000000", "--va", "500000000", "--surface", "2000"],
]
CAS = {
    "essentiel_irve.txt": ["essentiel", *IRVE, "--sans-carte"],
    "diagnose_irve.txt": ["diagnose", *IRVE, "--sans-risques", "--sans-carte"],
    "essentiel_industrie_htb.txt": ["essentiel", *INDUSTRIE_HTB, "--sans-carte"],
    "diagnose_industrie_htb.txt": ["diagnose", *INDUSTRIE_HTB, "--sans-risques", "--sans-carte"],
    "essentiel.txt": ESSENTIEL,
    "essentiel.json": [*ESSENTIEL, "--format", "json"],
    "diagnose.txt": DIAGNOSE,
    "diagnose.json": [*DIAGNOSE, "--format", "json"],
    "compare.txt": COMPARE,
    "compare.md": [*COMPARE, "--format", "md"],
    "compare.csv": [*COMPARE, "--format", "csv"],
    "compare.json": [*COMPARE, "--format", "json"],
}

# Le Rhône autour de la place Bellecour : de quoi dessiner la tuile du PDF sans réseau
CONTOURS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"code": "69", "nom": "Rhône", "region": "84"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[4.3, 45.3], [5.3, 45.3], [5.3, 46.3], [4.3, 46.3], [4.3, 45.3]]],
            },
        }
    ],
}


# `date` dont seul `today` est figé ; `isinstance(x, date)` reste vrai pour une vraie date
class _TypeJour(type):
    def __instancecheck__(cls, objet: object) -> bool:
        return isinstance(objet, date)


class _Jour(date, metaclass=_TypeJour):
    @classmethod
    def today(cls) -> date:
        return JOUR


class _Horloge:
    @staticmethod
    def now() -> datetime:
        return datetime(JOUR.year, JOUR.month, JOUR.day, 9, 0)


@pytest.fixture
def reglages(medallion_db: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=medallion_db,
        bronze_dir=tmp_path / "bronze",
        reports_dir=tmp_path / "reports",
    )


@pytest.fixture
def commande(
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> Callable[..., str]:
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)
    monkeypatch.setattr(cli, "geocode", lambda adresse, **_: ADRESSES[adresse])
    # Fraîcheur des données, barèmes à revérifier, échéances : tout ce qui dépend du jour
    monkeypatch.setattr(diagnostic, "date", _Jour)
    monkeypatch.setattr(turpe, "date", _Jour)
    monkeypatch.setattr(implantation, "datetime", _Horloge)

    def lancer(*arguments: str) -> str:
        assert cli.main(list(arguments)) == 0
        return capsys.readouterr().out

    return lancer


# Dix chiffres significatifs : le dernier bit d'une somme peut différer d'un système à l'autre
def _arrondi(valeur: Any) -> Any:
    if isinstance(valeur, float):
        return float(f"{valeur:.10g}")
    if isinstance(valeur, dict):
        return {cle: _arrondi(v) for cle, v in valeur.items()}
    if isinstance(valeur, list):
        return [_arrondi(v) for v in valeur]
    return valeur


def _comparer(nom: str, contenu: str, request: pytest.FixtureRequest) -> None:
    if nom.endswith(".json"):
        contenu = json.dumps(_arrondi(json.loads(contenu)), ensure_ascii=False, indent=2) + "\n"
    fichier = REFERENCES / nom
    if request.config.getoption("--maj-references"):
        fichier.parent.mkdir(exist_ok=True)
        fichier.write_text(contenu, encoding="utf-8", newline="\n")
        return
    assert fichier.exists(), f"référence absente : relancer avec --maj-references ({nom})"
    assert contenu == fichier.read_text(encoding="utf-8"), (
        f"{nom} a changé : si c'est voulu, relancer avec --maj-references et relire le diff"
    )


@pytest.mark.parametrize("nom", CAS)
def test_sortie_identique_a_sa_reference(
    commande: Callable[..., str], request: pytest.FixtureRequest, nom: str
) -> None:
    _comparer(nom, commande(*CAS[nom]), request)


def test_rapport_markdown_identique_a_sa_reference(
    commande: Callable[..., str], reglages: Settings, request: pytest.FixtureRequest
) -> None:
    commande(*DIAGNOSE, "--format", "md", "--sans-graphiques")
    (rapport,) = reglages.reports_dir.glob("*.md")
    _comparer("diagnose.md", rapport.read_text(encoding="utf-8"), request)


def _sans_espaces(texte: str) -> str:
    return re.sub(r"\s+", "", texte)


# Le texte extrait d'un PDF dépend de la mise en page : on y cherche ce qu'il doit dire plutôt
# que de le figer
def test_rapport_pdf_court_et_sans_caractere_remplace(
    commande: Callable[..., str], reglages: Settings
) -> None:
    contours = reglages.bronze_dir / CONTOURS_JEU / "ingest_date=2026-07-01" / "contours.geojson"
    contours.parent.mkdir(parents=True)
    contours.write_text(json.dumps(CONTOURS), encoding="utf-8")

    commande(*DIAGNOSE, "--format", "pdf")
    (rapport,) = reglages.reports_dir.glob("*.pdf")
    pages = PdfReader(rapport).pages
    texte = "\n".join(page.extract_text() for page in pages)

    assert 2 <= len(pages) <= 4
    # Chaque page porte son pied de page, la première la tuile et sa note
    assert all("Diagnostic d'implantation électrique — Lyon" in p.extract_text() for p in pages)
    assert "Tuile de la carte, large de" in texte
    assert f"Établi le {JOUR:%d/%m/%Y}" in texte
    assert "Pour le refaire : python -m ingestion.cli diagnose" in texte

    # Le verdict, puis une fiche par critère avec la limite qui pèse sur la décision et son conseil
    compact = _sans_espaces(texte)
    assert "Brancher ici ?" in texte and "Les cinq critères" in texte
    for critere, conseil in implantation_pdf.CONSEILS.items():
        assert critere in texte
        assert _sans_espaces(conseil) in compact, f"conseil absent du PDF : {critere}"
    # Un rapport qui se regarde : bien moins de texte que le Markdown, qui garde toutes les limites
    reference = (REFERENCES / "diagnose.md").read_text(encoding="utf-8")
    assert len(compact) < 0.4 * len(_sans_espaces(reference))
    assert "Limite." not in texte

    # Un glyphe absent de la police embarquée s'imprime en carré vide sans que le texte extrait
    # le montre : chaque caractère du rapport doit exister dans Inter ou Spectral
    glyphes = set(TTFont(charte.police(charte.TEXTE)).getBestCmap()) | set(
        TTFont(charte.police(charte.TITRE)).getBestCmap()
    )
    absents = {c for c in texte if not c.isspace() and ord(c) not in glyphes}
    assert not absents, f"caractères sans glyphe : {sorted(absents)}"
    assert "�" not in texte and "■" not in texte
