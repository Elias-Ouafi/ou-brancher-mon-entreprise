from __future__ import annotations

import json
import math
import os
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
from pytest_httpx import HTTPXMock

from ingestion import cli
from ingestion.config import Settings
from ingestion.extract_geo import (
    CONTOURS_JEU,
    RELIEF_JEU,
    TERRES_JEU,
    AltimetrieClient,
    completer_altitudes,
    lire_altitudes,
)
from ingestion.geocode import AdresseIntrouvableError
from ingestion.hexagones import (
    RAYON_SCENE,
    construire_grilles,
    lire_contours,
    lire_terres,
    points_terrestres,
)
from ingestion.tests.test_diagnostic import territoire as _territoire
from reporting import carte


def territoire():
    return _territoire("69381", "Lyon", latitude=45.5, longitude=5.5)


ELEVATION_URL = "https://alti.test/1.0/calcul/alti/rest/elevation.json"


def _carre(lon: float, lat: float, cote: float) -> list[list[list[float]]]:
    sommets = [(lon, lat), (lon + cote, lat), (lon + cote, lat + cote), (lon, lat + cote)]
    return [[list(p) for p in [*sommets, sommets[0]]]]


def _feature(code: str, nom: str, region: str, anneaux: list) -> dict:
    return {
        "type": "Feature",
        "properties": {"code": code, "nom": nom, "region": region},
        "geometry": {"type": "Polygon", "coordinates": anneaux},
    }


# Deux départements voisins d'un degré, un département minuscule, une île d'outre-mer
CONTOURS = {
    "type": "FeatureCollection",
    "features": [
        _feature("01", "Ain", "84", _carre(4.0, 45.0, 1.0)),
        _feature("69", "Rhône", "84", _carre(5.0, 45.0, 1.0)),
        _feature("75", "Paris", "11", _carre(5.48, 45.48, 0.04)),
        _feature("971", "Guadeloupe", "01", _carre(-61.6, 16.0, 0.4)),
        _feature("977", "Saint-Barthélemy", "", _carre(-62.9, 17.9, 0.05)),
    ],
}
# Terre voisine à l'est des deux départements
TERRES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": _carre(6.0, 44.5, 2.0)},
        }
    ],
}


@pytest.fixture
def contours(tmp_path: Path) -> Path:
    path = tmp_path / "departements.geojson"
    path.write_text(json.dumps(CONTOURS), encoding="utf-8")
    return path


@pytest.fixture
def terres(tmp_path: Path) -> Path:
    path = tmp_path / "terres.geojson"
    path.write_text(json.dumps(TERRES), encoding="utf-8")
    return path


@pytest.fixture
def grilles(contours: Path, terres: Path) -> list:
    return construire_grilles(lire_contours(contours), terres=lire_terres(terres), rayon_km=10)


def test_chaque_departement_a_au_moins_une_case(grilles: list) -> None:
    metropole, guadeloupe = grilles
    codes = {case.departement for case in metropole.cases if case.departement}
    assert codes == {"01", "69", "75"}
    assert {case.departement for case in guadeloupe.cases if case.departement} == {"971"}


def test_collectivites_hors_carte_et_encart_a_l_ouest(grilles: list) -> None:
    metropole, guadeloupe = grilles
    assert [g.code for g in grilles] == [None, "971"]
    largeur = math.sqrt(3) * RAYON_SCENE
    assert guadeloupe.scene_x0 + guadeloupe.colonnes * largeur < metropole.scene_x0


def test_un_encart_compte_environ_70_cases(grilles: list) -> None:
    terrestres = [case for case in grilles[1].cases if case.departement]
    assert 40 <= len(terrestres) <= 110
    assert grilles[1].rayon_km < grilles[0].rayon_km


def test_terre_voisine_en_brouillard_et_mer_ailleurs(grilles: list) -> None:
    metropole = grilles[0]
    hors_france = [case for case in metropole.cases if not case.departement]
    assert {case.etranger for case in hors_france if case.lon > 6.2} == {True}
    assert {case.etranger for case in hors_france if case.lon < 3.8} == {False}


def test_echantillons_terrestres_dedoublonnes(grilles: list) -> None:
    points = points_terrestres(grilles)
    assert len(points) == len(set(points))
    assert all(4.0 <= lon <= 6.0 or lon < -60 for lon, _ in points)


@pytest.fixture
def settings_alti(settings: Settings) -> Settings:
    return settings.model_copy(update={"altimetrie_base_url": "https://alti.test"})


def test_altitudes_mises_en_cache_puis_relues(
    httpx_mock: HTTPXMock, settings_alti: Settings
) -> None:
    httpx_mock.add_response(method="POST", url=ELEVATION_URL, json={"elevations": [-99999, 812.4]})
    points = [(5.123456, 45.5), (4.0, 45.1)]

    altitudes = completer_altitudes(points, settings=settings_alti)
    assert altitudes == {(5.12346, 45.5): 812.4, (4.0, 45.1): None}
    corps = json.loads(httpx_mock.get_requests()[0].content)
    assert corps["zonly"] == "true" and corps["lon"] == "4.00000|5.12346"

    # Deuxième passage : tout est en bronze, aucune requête
    assert completer_altitudes(points, settings=settings_alti) == altitudes
    assert len(httpx_mock.get_requests()) == 1
    fichiers = list((settings_alti.bronze_dir / RELIEF_JEU).glob("ingest_date=*/*.parquet"))
    assert len(fichiers) == 1 and pq.read_table(fichiers[0]).num_rows == 2


def test_altitudes_par_lots_de_5000(httpx_mock: HTTPXMock, settings_alti: Settings) -> None:
    httpx_mock.add_response(method="POST", json={"elevations": [1.0] * 5000})
    httpx_mock.add_response(method="POST", json={"elevations": [2.0]})
    points = [(i / 1000, 45.0) for i in range(5001)]
    with AltimetrieClient(settings_alti) as client:
        altitudes = client.altitudes(points)
    assert len(altitudes) == 5001 and altitudes[-1] == 2.0 and client.calls == 2


@pytest.fixture
def settings_carte(
    settings_alti: Settings, medallion_db: Path, contours: Path, terres: Path
) -> Settings:
    for jeu, source in ((CONTOURS_JEU, contours), (TERRES_JEU, terres)):
        cible = settings_alti.bronze_dir / jeu / "ingest_date=2026-10-18" / f"{jeu}.geojson"
        cible.parent.mkdir(parents=True)
        cible.write_bytes(source.read_bytes())
    return settings_alti.model_copy(
        update={"duckdb_path": medallion_db, "reports_dir": settings_alti.bronze_dir.parent}
    )


def test_donnees_de_la_carte(settings_carte: Settings, grilles: list) -> None:
    gold = carte.lire_donnees_gold(settings_carte.duckdb_path)
    altitudes = {point: 300.0 for point in points_terrestres(grilles)}
    donnees = carte.construire_donnees(grilles, altitudes, gold, territoire=territoire())

    longueurs = {len(case) for case in donnees["cases"]}
    assert longueurs == {3, 4, 8}
    francaises = [case for case in donnees["cases"] if len(case) == 8]
    assert {case[4] for case in francaises} == {300}
    assert donnees["grilles"][0]["code"] is None and donnees["grilles"][1]["code"] == "971"
    assert donnees["departements"] and donnees["zones"]
    assert all(site["case"] for site in donnees["sites"])
    assert donnees["adresse"]["departement"] == territoire().code_departement
    assert donnees["donnees"].startswith("Données chargées le 01/07/2026")


def test_page_autonome_et_echappee(settings_carte: Settings, grilles: list) -> None:
    gold = carte.lire_donnees_gold(settings_carte.duckdb_path)
    gold["departements"][0]["n"] = "</script><b>"
    html = carte.rendre_html(carte.construire_donnees(grilles, {}, gold))

    assert carte.MARQUEUR_DONNEES not in html and carte.MARQUEUR_SCRIPTS not in html
    assert "Three.js Authors" in html
    assert "<\\/script><b>" in html and "</script><b>" not in html
    assert "cdnjs" not in html and "jsdelivr" not in html


def test_carte_hors_ligne_a_plat(
    settings_carte: Settings, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("hors ligne"), is_reusable=True)
    path = carte.generer_carte(settings=settings_carte)

    assert path == settings_carte.reports_dir / "carte.html"
    assert "Atlas électrique de France" in path.read_text(encoding="utf-8")
    assert "relief incomplet" in caplog.text
    assert lire_altitudes(settings_carte) == {}


def test_carte_sans_warehouse(settings: Settings) -> None:
    with pytest.raises(carte.DiagnosticIndisponibleError, match="warehouse introuvable"):
        carte.generer_carte(settings=settings)


def _libelle_epingle(path: Path) -> str | None:
    ligne = next(
        ligne
        for ligne in path.read_text(encoding="utf-8").splitlines()
        if ligne.startswith("DATA.adresse = ")
    )
    adresse = json.loads(ligne.removeprefix("DATA.adresse = /*__ADRESSE__*/").removesuffix(";"))
    return adresse and adresse["libelle"]


def test_carte_a_jour_seule_l_epingle_bouge(
    settings_carte: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("hors ligne"), is_reusable=True)
    path = carte.mettre_a_jour_carte(settings=settings_carte, territoire=territoire())
    assert _libelle_epingle(path) == territoire().adresse

    def interdit(**kwargs: object) -> None:
        raise AssertionError("carte reconstruite alors qu'elle est à jour")

    monkeypatch.setattr(carte, "generer_carte", interdit)
    ailleurs = _territoire("01053", "Bourg-en-Bresse", latitude=45.5, longitude=4.5)
    carte.mettre_a_jour_carte(settings=settings_carte, territoire=ailleurs)
    assert _libelle_epingle(path) == ailleurs.adresse
    assert path.read_text(encoding="utf-8").count(carte.MARQUEUR_ADRESSE) == 1

    carte.mettre_a_jour_carte(settings=settings_carte)
    assert _libelle_epingle(path) is None


def test_carte_refaite_quand_le_warehouse_change(
    settings_carte: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("hors ligne"), is_reusable=True)
    path = carte.generer_carte(settings=settings_carte)
    plus_tard = path.stat().st_mtime + 60
    os.utime(settings_carte.duckdb_path, (plus_tard, plus_tard))

    appels: list[dict] = []
    monkeypatch.setattr(carte, "generer_carte", lambda **kwargs: appels.append(kwargs) or path)
    carte.mettre_a_jour_carte(settings=settings_carte, territoire=territoire())
    assert appels and appels[0]["territoire"] == territoire()


def test_essentiel_place_l_adresse_sur_la_carte(
    settings_carte: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    appels: list[dict] = []
    monkeypatch.setattr(cli, "get_settings", lambda: settings_carte)
    monkeypatch.setattr(cli, "geocode", lambda *args, **kwargs: territoire())
    monkeypatch.setattr(
        carte, "mettre_a_jour_carte", lambda **kwargs: appels.append(kwargs) or Path("c.html")
    )
    assert cli.main(["essentiel", "--adresse", "Lyon", "--puissance", "100"]) == 0
    assert appels[0]["territoire"] == territoire()
    verdict_adresse = appels[0]["verdict_adresse"]
    assert verdict_adresse["criteres"][0]["etat"] == "attention"
    assert verdict_adresse["phrase"].startswith("À vérifier avant de s'engager : raccordement")
    assert cli.main(["essentiel", "--adresse", "Lyon", "--sans-carte"]) == 0
    assert len(appels) == 1

    def en_panne(**kwargs: object) -> None:
        raise carte.DiagnosticIndisponibleError("contours introuvables")

    monkeypatch.setattr(carte, "mettre_a_jour_carte", en_panne)
    assert cli.main(["essentiel", "--adresse", "Lyon"]) == 0
    assert "carte non mise a jour : contours introuvables" in caplog.text


def test_commande_carte(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    appels: list[dict] = []
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        carte, "mettre_a_jour_carte", lambda **kwargs: appels.append(kwargs) or Path("carte.html")
    )
    monkeypatch.setattr(carte, "generer_carte", lambda **kwargs: Path("refaite.html"))
    assert cli.main(["carte", "--sans-ouvrir", "--output", "x.html"]) == 0
    assert appels[0]["territoire"] is None and appels[0]["output"] == Path("x.html")
    assert cli.main(["carte", "--sans-ouvrir", "--reconstruire"]) == 0
    assert len(appels) == 1

    def introuvable(*args: object, **kwargs: object) -> None:
        raise AdresseIntrouvableError("rien")

    monkeypatch.setattr(cli, "geocode", introuvable)
    assert cli.main(["carte", "--adresse", "nulle part", "--sans-ouvrir"]) == 1
