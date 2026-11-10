from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest

from ingestion.config import Settings
from ingestion.geocode import AdresseAmbigueError, AdresseIntrouvableError
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
    (donnees,) = _json(reponse)["adresses"]
    assert donnees["texte"].startswith("1 rue de test, Lyon")
    assert donnees["saisie"] == "Lyon"
    assert donnees["commande"].endswith("--conso 15000 --sans-risques --format pdf")
    assert sans_carte[0]["territoire"] == LYON
    assert sans_carte[0]["verdict_adresse"]["phrase"]
    assert sans_carte[0]["autre"] is None


AJACCIO = territoire("2A004", "Ajaccio", latitude=41.92, longitude=8.74)


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé")
def test_deux_adresses_epinglees_et_pas_une_de_plus(
    settings_app: Settings,
    sans_carte: list[dict],
    monkeypatch: pytest.MonkeyPatch,
    no_network: None,
) -> None:
    lieux = {"Lyon": LYON, "Ajaccio": AJACCIO}
    monkeypatch.setattr(app, "geocode", lambda adresse, **_: lieux[adresse])
    corps = {"adresse": "Lyon", "autres": ["Ajaccio"], "puissance_mw": 2}
    reponse = app.traiter("POST", "/api/analyse", json.dumps(corps).encode(), settings_app)

    assert reponse.statut == HTTPStatus.OK
    assert [a["saisie"] for a in _json(reponse)["adresses"]] == ["Lyon", "Ajaccio"]
    territoire_autre, verdict_autre = sans_carte[0]["autre"]
    assert territoire_autre == AJACCIO and verdict_autre["phrase"]

    corps["autres"] = ["Ajaccio", "Lyon"]
    reponse = app.traiter("POST", "/api/analyse", json.dumps(corps).encode(), settings_app)
    assert reponse.statut == HTTPStatus.BAD_REQUEST
    assert "2 adresses au plus" in _json(reponse)["erreur"]
    assert len(sans_carte) == 1


# L'adresse en cause est désignée par son rang : la page propose les communes sous le bon champ
@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé")
def test_seconde_adresse_ambigue_ou_introuvable(
    settings_app: Settings, sans_carte: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    def geocode(adresse: str, **_: object) -> object:
        if adresse == "rue de Paris":
            raise AdresseAmbigueError(adresse, ["Rue de Paris 93380", "Rue de Paris 97400"])
        if adresse == "nulle part":
            raise AdresseIntrouvableError("aucun résultat")
        return LYON

    monkeypatch.setattr(app, "geocode", geocode)
    corps = {"adresse": "Lyon", "autres": ["rue de Paris"]}
    reponse = app.traiter("POST", "/api/analyse", json.dumps(corps).encode(), settings_app)
    assert reponse.statut == HTTPStatus.CONFLICT
    assert _json(reponse)["rang"] == 1 and len(_json(reponse)["candidats"]) == 2

    corps["autres"] = ["nulle part"]
    reponse = app.traiter("POST", "/api/analyse", json.dumps(corps).encode(), settings_app)
    assert reponse.statut == HTTPStatus.BAD_REQUEST
    assert "« nulle part » est introuvable" in _json(reponse)["erreur"]
    assert not sans_carte


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
    # Fenêtre d'accueil : une adresse, une seconde à la demande, jamais une troisième
    assert all(f'id="{champ}"' in gabarit for champ in ("accueil", "q", "q2", "ajouter2"))
    assert 'id="q3"' not in gabarit


def test_seconde_adresse_dans_la_ligne_de_la_premiere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(carte, "reperer", lambda *args: [0, 1, 2])
    adresse = carte.construire_adresse([], LYON, {"phrase": "a"}, (AJACCIO, {"phrase": "b"}))
    assert adresse["libelle"] == LYON.adresse and adresse["verdict"] == {"phrase": "a"}
    assert adresse["autre"]["libelle"] == AJACCIO.adresse
    assert adresse["autre"]["verdict"] == {"phrase": "b"} and "autre" not in adresse["autre"]
    assert "autre" not in carte.construire_adresse([], LYON)


COURBE = "Horodate;Valeur\n2026-09-01T00:30:00+02:00;12000000\n2026-09-01T01:00:00+02:00;6000000\n"


def test_courbe_lue_depuis_le_formulaire() -> None:
    projet = app.lire_projet(
        {"adresse": "a", "va_eur": "1e6", "courbe_csv": COURBE, "courbe_nom": "site.csv"}
    )
    assert projet.courbe is not None and projet.courbe.pas_minutes == 30
    assert app.commande(projet, "pdf").endswith(
        '--va 1e+06 --courbe "site.csv" --sans-risques --format pdf'
    )
    with pytest.raises(app.ProjetInvalideError, match="courbe de charge illisible"):
        app.lire_projet({"adresse": "a", "courbe_csv": "Horodate;Valeur\nhier;beaucoup\n"})


@pytest.mark.parametrize("autres", [[], [" "], ["b", "c"], "b"])
def test_comparaison_refusee_hors_de_deux_adresses(settings: Settings, autres: object) -> None:
    corps = json.dumps({"adresse": "a", "autres": autres}).encode()
    reponse = app.traiter("POST", "/api/compare", corps, settings)
    assert reponse.statut == HTTPStatus.BAD_REQUEST


@pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé")
def test_comparaison_rend_le_tableau_et_ses_fichiers(
    settings_app: Settings, monkeypatch: pytest.MonkeyPatch, no_network: None
) -> None:
    def geocoder(adresse: str, **kwargs: object) -> object:
        if adresse == "nulle part":
            raise AdresseAmbigueError(adresse, ["Nulle Part 01000", "Nulle Part 02000"])
        return LYON

    monkeypatch.setattr(app, "geocode", geocoder)
    corps = {"adresse": "Lyon", "autres": ["Lyon bis", ""], "puissance_mw": 2}
    reponse = app.traiter("POST", "/api/compare", json.dumps(corps).encode(), settings_app)

    assert reponse.statut == HTTPStatus.OK
    donnees = _json(reponse)
    assert donnees["commande"] == (
        'python -m ingestion.cli compare --adresse "Lyon" --adresse "Lyon bis" --puissance 2'
        " --sans-risques --format md"
    )
    tableau = json.loads(donnees["fichiers"]["json"])
    assert len(tableau["adresses"]) == 2
    assert tableau["tableau"][-1]["indicateur"] == "Brancher ici ?"
    assert donnees["fichiers"]["csv"].startswith("indicateur;")
    assert donnees["fichiers"]["md"].startswith("# Comparaison de 2 adresses")

    corps["autres"] = ["nulle part"]
    reponse = app.traiter("POST", "/api/compare", json.dumps(corps).encode(), settings_app)
    assert reponse.statut == HTTPStatus.BAD_REQUEST
    assert "« nulle part » est ambiguë" in _json(reponse)["erreur"]
