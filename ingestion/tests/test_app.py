from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest

from ingestion.config import Settings
from ingestion.geocode import AdresseAmbigueError
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from reporting import app, carte

LYON = territoire("69123", "Lyon", latitude=45.76, longitude=4.83)


@pytest.fixture
def settings_app(medallion_db: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=medallion_db,
        bronze_dir=tmp_path / "bronze",
        reports_dir=tmp_path / "reports",
    )


@pytest.fixture
def sans_carte(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    appels: list[dict] = []
    monkeypatch.setattr(
        carte, "mettre_a_jour_carte", lambda **kwargs: appels.append(kwargs) or Path("c.html")
    )
    return appels


def _json(reponse: app.Reponse) -> dict:
    return json.loads(reponse.corps)


def test_projet_lu_depuis_le_formulaire() -> None:
    projet = app.lire_projet(
        {"adresse": " 1 rue A ", "puissance_mw": "12", "conso_mwh": "70 000", "va_eur": "1,5e6"}
    )
    assert projet.adresse == "1 rue A"
    assert (projet.puissance_mw, projet.conso_mwh, projet.va_eur) == (12, 70_000, 1_500_000)
    assert projet.activite is None and projet.surface_m2 is None and not projet.complet


@pytest.mark.parametrize(
    ("corps", "message"),
    [
        ({}, "adresse manquante"),
        ({"adresse": "a", "puissance_mw": "douze"}, "puissance : nombre attendu"),
        ({"adresse": "a", "conso_mwh": "-3"}, "consommation : valeur positive"),
        ({"adresse": "a", "activite": "bureaux"}, "activité inconnue"),
        ({"adresse": "a", "ca_eur": "1000"}, "demandent la consommation"),
    ],
)
def test_projet_invalide_refuse(corps: dict, message: str) -> None:
    with pytest.raises(app.ProjetInvalideError, match=message):
        app.lire_projet(corps)


def test_commande_equivalente() -> None:
    projet = app.Projet("1 rue A", puissance_mw=12.0, conso_mwh=70_000.0, activite="irve")
    assert app.commande(projet, "pdf") == (
        'python -m ingestion.cli diagnose --adresse "1 rue A" --puissance 12 --conso 70000'
        " --activite irve --sans-risques --format pdf"
    )
    complet = app.Projet("1 rue A", complet=True)
    assert app.commande(complet, "md").endswith('--adresse "1 rue A" --format md')


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé")
def test_analyse_epingle_le_verdict(
    settings_app: Settings,
    sans_carte: list[dict],
    monkeypatch: pytest.MonkeyPatch,
    no_network: None,
) -> None:
    monkeypatch.setattr(app, "geocode", lambda *args, **kwargs: LYON)
    corps = json.dumps({"adresse": "Lyon", "puissance_mw": 2, "conso_mwh": 15000}).encode()
    reponse = app.traiter("POST", "/api/analyse", corps, settings_app)

    assert reponse.statut == HTTPStatus.OK
    donnees = _json(reponse)
    assert donnees["texte"].startswith("1 rue de test, Lyon")
    assert donnees["commande"].endswith("--conso 15000 --sans-risques --format pdf")
    assert sans_carte[0]["territoire"] == LYON
    assert sans_carte[0]["verdict_adresse"]["phrase"]


def test_adresse_ambigue_renvoie_les_candidats(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ambigue(*args: object, **kwargs: object) -> None:
        raise AdresseAmbigueError("rue de Paris", ["Rue de Paris 93380", "Rue de Paris 97400"])

    monkeypatch.setattr(app, "geocode", ambigue)
    reponse = app.traiter("POST", "/api/analyse", b'{"adresse": "rue de Paris"}', settings)
    assert reponse.statut == HTTPStatus.CONFLICT
    assert _json(reponse)["candidats"] == ["Rue de Paris 93380", "Rue de Paris 97400"]


@pytest.mark.parametrize(
    ("corps", "statut"),
    [(b"pas du json", HTTPStatus.BAD_REQUEST), (b"[1]", HTTPStatus.BAD_REQUEST)],
)
def test_corps_illisible(settings: Settings, corps: bytes, statut: HTTPStatus) -> None:
    assert app.traiter("POST", "/api/analyse", corps, settings).statut == statut


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé")
@pytest.mark.parametrize(("format", "debut"), [("md", b"# "), ("json", b"{")])
def test_rapport_telechargeable(
    settings_app: Settings,
    monkeypatch: pytest.MonkeyPatch,
    no_network: None,
    format: str,
    debut: bytes,
) -> None:
    monkeypatch.setattr(app, "geocode", lambda *args, **kwargs: LYON)
    corps = json.dumps({"adresse": "Lyon", "format": format}).encode()
    reponse = app.traiter("POST", "/api/rapport", corps, settings_app)

    assert reponse.statut == HTTPStatus.OK
    assert reponse.type == app.TYPES[format]
    assert reponse.fichier and reponse.fichier.endswith("." + format)
    assert reponse.corps.lstrip().startswith(debut)
    assert (settings_app.reports_dir / reponse.fichier).exists()


def test_format_inconnu(settings: Settings) -> None:
    reponse = app.traiter("POST", "/api/rapport", b'{"adresse": "a", "format": "docx"}', settings)
    assert reponse.statut == HTTPStatus.BAD_REQUEST


def test_page_servie_en_mode_application(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings.model_copy(update={"reports_dir": tmp_path / "reports"})
    page = settings.reports_dir / "carte.html"
    page.parent.mkdir()
    page.write_text(
        f'<script>const APP = {app.MARQUEUR_APP};\nDATA.adresse = /*__ADRESSE__*/{{"a": 1}};'
        "</script>",
        encoding="utf-8",
    )
    monkeypatch.setattr(carte, "carte_a_jour", lambda *args: True)
    reponse = app.traiter("GET", "/", b"", settings)
    assert b"const APP = true;" in reponse.corps

    # Carte périmée : refaite, l'épingle de la dernière analyse reprise
    def refaire(*, settings: Settings, output: Path) -> Path:
        output.write_text("DATA.adresse = /*__ADRESSE__*/null;\nconst APP = /*__APP__*/false;")
        return output

    monkeypatch.setattr(carte, "carte_a_jour", lambda *args: False)
    monkeypatch.setattr(carte, "generer_carte", refaire)
    corps = app.traiter("GET", "/", b"", settings).corps.decode()
    assert 'DATA.adresse = /*__ADRESSE__*/{"a": 1};' in corps
    assert "const APP = true;" in corps


def test_serveur_local(settings: Settings) -> None:
    serveur = app.creer_serveur(settings, port=0)
    assert serveur.server_address[0] == "127.0.0.1"
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as erreur:
            urllib.request.urlopen(app.url(serveur) + "inconnu", timeout=5)
        assert erreur.value.code == HTTPStatus.NOT_FOUND
        assert "inconnu" in json.loads(erreur.value.read())["erreur"]
    finally:
        serveur.shutdown()
        serveur.server_close()


def test_gabarit_porte_les_marqueurs_de_l_application() -> None:
    gabarit = carte.GABARIT.read_text(encoding="utf-8")
    assert app.MARQUEUR_APP in gabarit
    assert "connect-src 'self'" in gabarit
