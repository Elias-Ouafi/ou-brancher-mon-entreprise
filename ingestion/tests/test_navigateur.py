from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

pytest.importorskip("pytest_playwright", reason="groupe e2e non installé (pytest-playwright)")

from playwright.sync_api import Page, expect
from pytest_httpx import HTTPXMock

from ingestion.config import Settings
from ingestion.extract_geo import CONTOURS_JEU, TERRES_JEU
from ingestion.geocode import AdresseAmbigueError
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_carte import CONTOURS, TERRES
from ingestion.tests.test_diagnostic import territoire
from reporting import app, carte

pytestmark = [
    pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)"),
    # Hors ligne, le relief et les pays ne sont demandés que si la carte est refaite
    pytest.mark.httpx_mock(assert_all_responses_were_requested=False),
]

# Coordonnées dans les contours synthétiques de test_carte, pas celles des vraies villes
LIEUX = {
    "1 place Bellecour Lyon": territoire("69123", "Lyon", latitude=45.3, longitude=5.3),
    "1 rue Frébault Pointe-à-Pitre": territoire(
        "97120", "Pointe-à-Pitre", latitude=16.2, longitude=-61.4
    ),
}
CANDIDATS = ["1 place Bellecour Lyon", "1 place Bellecour Villeurbanne"]
COURBE = Path("docs/exemples/courbe_industrie_septembre.csv")


def _geocode(adresse: str, **_: object) -> object:
    if adresse == "place Bellecour":
        raise AdresseAmbigueError(adresse, CANDIDATS)
    return LIEUX[adresse]


@pytest.fixture
def reglages(medallion_db: Path, tmp_path: Path, httpx_mock: HTTPXMock) -> Settings:
    httpx_mock.add_exception(httpx.ConnectError("hors ligne"), is_reusable=True)
    bronze = tmp_path / "bronze"
    for jeu, contenu in ((CONTOURS_JEU, CONTOURS), (TERRES_JEU, TERRES)):
        cible = bronze / jeu / "ingest_date=2026-10-18" / f"{jeu}.geojson"
        cible.parent.mkdir(parents=True)
        cible.write_text(json.dumps(contenu), encoding="utf-8")
    return Settings(
        _env_file=None,
        duckdb_path=medallion_db,
        bronze_dir=bronze,
        reports_dir=tmp_path / "reports",
        retry_wait_seconds=0.0,
    )


# Le vrai serveur de `app` sur un port libre ; seul le géocodage est simulé
@pytest.fixture
def serveur(reglages: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[ThreadingHTTPServer]:
    monkeypatch.setattr(app, "geocode", _geocode)
    serveur = app.creer_serveur(reglages, port=0)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    yield serveur
    serveur.shutdown()
    serveur.server_close()


# Messages de console qu'un parcours attend (le serveur arrêté en produit un)
@pytest.fixture
def erreurs_attendues() -> list[str]:
    return []


# Réseau coupé côté navigateur : toute requête hors de la machine est refusée et notée ;
# toute erreur de la console ou du script fait échouer le parcours
@pytest.fixture
def ecran(page: Page, erreurs_attendues: list[str]) -> Iterator[Page]:
    dehors: list[str] = []
    erreurs: list[str] = []

    def filtrer(route: object) -> None:
        url = route.request.url
        if url.startswith(("http://127.0.0.1", "file:", "blob:", "data:")):
            route.continue_()
        else:
            dehors.append(url)
            route.abort()

    page.route("**/*", filtrer)
    page.on("console", lambda m: erreurs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.set_default_timeout(20_000)
    # Les assertions attendent comme les actions : deux adresses s'analysent en 6 à 7 s sous Windows
    expect.set_options(timeout=20_000)
    yield page
    assert not dehors, f"requêtes hors de la machine : {dehors}"
    inattendues = [e for e in erreurs if not any(a in e for a in erreurs_attendues)]
    assert not inattendues, f"erreurs dans la console : {inattendues}"


def _analyser(page: Page, adresse: str) -> None:
    expect(page.locator("#accueil")).to_be_visible()
    page.fill("#q", adresse)
    page.click("#analyser")


def test_accueil_adresse_verdict_puis_pdf(ecran: Page, serveur: ThreadingHTTPServer) -> None:
    ecran.goto(app.url(serveur))
    _analyser(ecran, "1 place Bellecour Lyon")

    fiche = ecran.locator("#city")
    expect(fiche).to_contain_text("Brancher ici ?")
    expect(fiche).to_contain_text("Lyon")
    expect(ecran.locator("#accueil")).to_be_hidden()
    expect(ecran.locator("#nouvelle")).to_be_visible()

    with ecran.expect_download() as telechargement:
        ecran.click("#city [data-format=pdf]")
    fichier = telechargement.value
    assert fichier.suggested_filename.endswith(".pdf")
    assert Path(fichier.path()).read_bytes().startswith(b"%PDF")
    expect(ecran.locator("#etat")).to_contain_text("téléchargé")


def test_adresse_ambigue_resolue_par_le_choix_d_une_commune(
    ecran: Page, serveur: ThreadingHTTPServer, erreurs_attendues: list[str]
) -> None:
    # Le navigateur journalise toute réponse 4xx, dont le 409 qui porte les candidats
    erreurs_attendues.append("409 (Conflict)")
    ecran.goto(app.url(serveur))
    _analyser(ecran, "place Bellecour")

    candidats = ecran.locator("#candidats0 button")
    expect(candidats).to_have_text(CANDIDATS)
    expect(ecran.locator("#message")).to_contain_text("ambiguë")
    candidats.first.click()

    expect(ecran.locator("#city")).to_contain_text("Brancher ici ?")
    expect(ecran.locator("#accueil")).to_be_hidden()


def test_deux_adresses_cote_a_cote_tableau_puis_csv(
    ecran: Page, serveur: ThreadingHTTPServer
) -> None:
    ecran.goto(app.url(serveur))
    expect(ecran.locator("#accueil")).to_be_visible()
    ecran.fill("#q", "1 place Bellecour Lyon")
    ecran.click("#ajouter2")
    ecran.fill("#q2", "1 rue Frébault Pointe-à-Pitre")
    ecran.click("#analyser")

    fiche = ecran.locator("#city.cote")
    expect(fiche).to_contain_text("Lyon")
    expect(fiche).to_contain_text("Pointe-à-Pitre")

    ecran.click("[data-action=comparer]")
    expect(ecran.locator("#dossierTitre")).to_have_text(
        "Comparaison de 2 adresses, sans classement"
    )
    expect(ecran.locator("#dossierTable thead th")).to_have_count(3)
    expect(ecran.locator("#dossierTable tbody tr").first).to_contain_text("Territoire")

    with ecran.expect_download() as telechargement:
        ecran.click("[data-export=csv]")
    fichier = telechargement.value
    assert fichier.suggested_filename == "comparaison.csv"
    # BOM : Excel lit alors les accents
    contenu = Path(fichier.path()).read_bytes()
    assert contenu.startswith(b"\xef\xbb\xbf") and "Pointe-à-Pitre".encode() in contenu


def test_courbe_gardee_apres_rechargement_puis_retiree(
    ecran: Page, serveur: ThreadingHTTPServer
) -> None:
    ecran.goto(app.url(serveur))
    expect(ecran.locator("#accueil")).to_be_visible()
    ecran.fill("#q", "1 place Bellecour Lyon")
    ecran.click("#reglages summary")
    ecran.set_input_files("#courbe", COURBE)
    gardee = ecran.locator("#courbeGardee")
    expect(gardee).to_contain_text(COURBE.name)
    ecran.click("#analyser")

    expect(ecran.locator("#city")).to_contain_text("Brancher ici ?")
    ecran.reload()
    ecran.click("#nouvelle")
    expect(gardee).to_contain_text(COURBE.name)
    ecran.click("#fermerAccueil")
    ecran.click("[data-action=lire]")
    expect(ecran.locator("#dossierPied")).to_contain_text(f'--courbe "{COURBE.name}"')

    ecran.click("#fermerDossier")
    ecran.click("#nouvelle")
    ecran.click("#retirerCourbe")
    expect(gardee).to_be_hidden()


def test_serveur_arrete_dit_de_le_relancer(
    ecran: Page, serveur: ThreadingHTTPServer, erreurs_attendues: list[str]
) -> None:
    ecran.goto(app.url(serveur))
    expect(ecran.locator("#accueil")).to_be_visible()
    ecran.fill("#q", "1 place Bellecour Lyon")

    serveur.shutdown()
    serveur.server_close()
    erreurs_attendues.append("ERR_CONNECTION_REFUSED")
    ecran.click("#analyser")

    expect(ecran.locator("#message")).to_contain_text("ne répond pas : relancer")
    expect(ecran.locator("#accueil")).to_be_visible()


def test_page_en_file_reste_en_lecture_seule(ecran: Page, reglages: Settings) -> None:
    chemin = carte.generer_carte(settings=reglages)
    ecran.goto(chemin.resolve().as_uri())

    expect(ecran.locator("#city")).to_contain_text("python -m ingestion.cli app")
    expect(ecran.locator("#accueil")).to_be_hidden()
    expect(ecran.locator("#nouvelle")).to_be_hidden()
    assert "app" not in (ecran.locator("body").get_attribute("class") or "").split()


def test_ambiance_sonore_coupee_puis_selon_la_filiere_dominante(
    ecran: Page, reglages: Settings
) -> None:
    ecran.goto(carte.generer_carte(settings=reglages).resolve().as_uri())
    son = ecran.locator("#son")
    expect(son).to_have_attribute("aria-pressed", "false")

    # Plus de la moitié de la puissance de la case, sinon aucun son ; le solaire est silencieux
    regles = ecran.evaluate(
        """() => [
            sonDominant([{ f: "Nucléaire", mw: 2600 }, { f: "Solaire", mw: 12 }]),
            sonDominant([{ f: "Eolien", mw: 50 }, { f: "Hydraulique", mw: 50 }]),
            sonDominant([{ f: "Solaire", mw: 300 }]),
            sonDominant([]),
        ]"""
    )
    assert regles == ["bourdonnement", None, None, None]

    son.click()
    expect(son).to_have_attribute("aria-pressed", "true")
    expect(son).to_have_text("Son")
