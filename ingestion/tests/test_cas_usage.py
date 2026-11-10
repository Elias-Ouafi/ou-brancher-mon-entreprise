from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from ingestion import cli
from ingestion.config import Settings
from ingestion.diagnostic import ACTIVITES, ETATS_CRITERE
from ingestion.tests.medallion import DBT_BIN
from ingestion.tests.test_diagnostic import territoire
from ingestion.tests.test_recette_mvp import RECETTE
from reporting import app

pytestmark = pytest.mark.skipif(DBT_BIN is None, reason="dbt non installé (uv sync --group dbt)")

# Invariants plutôt que valeurs : chaque cas d'usage du persona doit aboutir, dire qui raccorde,
# et dire « non disponible » là où la base synthétique n'a pas la donnée (la tranche IF, exprès)
ACTIVITES_TESTEES = [None, *sorted(ACTIVITES)]

# (MW, MWh par an, domaine de tension, gestionnaire, grille TURPE) : chaque borne de
# `seuils_raccordement` est jouée des deux côtés
PALIERS = [
    (0.03, 100, "BT ≤ 36 kVA", "Enedis (ou ELD)", None),
    (0.036, 150, "BT > 36 kVA", "Enedis (ou ELD)", "bt_sup36"),
    (0.2, 800, "BT > 36 kVA", "Enedis (ou ELD)", "bt_sup36"),
    (0.25, 1_000, "HTA", "Enedis (ou ELD)", "hta"),
    (1, 5_000, "HTA", "Enedis (ou ELD)", "hta"),
    (12, 70_000, "HTA", "Enedis (ou ELD)", "hta"),
    (20, 100_000, "HTA – zone grise", "Enedis ou RTE", "hta"),
    (30, 140_000, "HTA – zone grise", "Enedis ou RTE", "hta"),
    (40, 250_000, "HTB", "RTE", "htb1"),
    (45, 300_000, "HTB", "RTE", "htb1"),
    (60, 400_000, "HTB", "RTE", "htb1"),
]
CRITERES = ["Raccordement", "Risques", "Carbone", "Facture", "Foncier"]
LYON = "69123|Lyon"
AJACCIO = "2A004|Ajaccio"

Commande = Callable[..., tuple[int, str]]


@pytest.fixture
def reglages(medallion_db: Path, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=medallion_db,
        bronze_dir=tmp_path / "bronze",
        reports_dir=tmp_path / "reports",
    )


# Lance la vraie ligne de commande ; l'adresse « code|commune » tient lieu de géocodage
@pytest.fixture
def commande(
    reglages: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> Commande:
    monkeypatch.setattr(cli, "get_settings", lambda: reglages)
    monkeypatch.setattr(cli, "geocode", lambda adresse, **_: territoire(*adresse.split("|")))

    def lancer(*arguments: object) -> tuple[int, str]:
        code = cli.main([str(a) for a in arguments])
        return code, capsys.readouterr().out

    return lancer


def _projet(puissance: float, conso: float, activite: str | None) -> list[object]:
    options: list[object] = ["--puissance", puissance, "--conso", conso]
    return options if activite is None else [*options, "--activite", activite]


def _criteres(verdict: dict) -> dict[str, dict]:
    assert [c["nom"] for c in verdict["criteres"]] == CRITERES
    assert all(c["etat"] in ETATS_CRITERE and c["valeur"] for c in verdict["criteres"])
    assert verdict["phrase"]
    return {c["nom"]: c for c in verdict["criteres"]}


@pytest.mark.parametrize("activite", ACTIVITES_TESTEES)
@pytest.mark.parametrize(("puissance", "conso", "domaine", "gestionnaire", "turpe"), PALIERS)
def test_essentiel_pour_chaque_activite_et_chaque_palier(
    commande: Commande,
    activite: str | None,
    puissance: float,
    conso: float,
    domaine: str,
    gestionnaire: str,
    turpe: str | None,
) -> None:
    projet = ["essentiel", "--adresse", LYON, *_projet(puissance, conso, activite), "--sans-carte"]
    code, sortie = commande(*projet, "--format", "json")
    assert code == 0
    reponse = json.loads(sortie)
    criteres = _criteres(reponse["verdict"])

    # Qui raccorde : conforme au seed, « à vérifier » dès que RTE peut être concerné
    assert criteres["Raccordement"]["valeur"] == f"{domaine}, {gestionnaire}"
    assert (criteres["Raccordement"]["etat"] == "attention") == ("RTE" in gestionnaire)

    acheminement = reponse["acheminement"]
    if turpe is None:
        assert acheminement is None
    else:
        assert acheminement["domaine_turpe"] == turpe
        assert acheminement["profil"] == ("activite" if activite == "industrie" else "plat")
        parts = ("gestion_comptage_eur", "puissance_eur", "energie_eur")
        assert acheminement["total_eur"] == pytest.approx(sum(acheminement[p] for p in parts))
        assert acheminement["prix_eur_mwh"] == pytest.approx(acheminement["total_eur"] / conso)

    # Un prix absent se dit, il ne se devine pas
    prix = reponse["prix"]
    if prix is None:
        assert criteres["Facture"]["etat"] == "inconnu"
    else:
        attendue = prix["prix_htva_eur_kwh"] * conso * 1000
        assert prix["facture_htva_eur_an"] == pytest.approx(attendue)
        assert criteres["Facture"]["etat"] != "inconnu"
    assert (reponse["activite"] or {}).get("code") == activite

    code, texte = commande(*projet)
    assert code == 0
    assert texte.startswith("1 rue de test, Lyon")
    assert "Données chargées le" in texte
    assert f"Brancher ici ? {reponse['verdict']['phrase']}" in texte
    assert ("non publié par Eurostat" in texte) == (prix is None)


@pytest.mark.parametrize("activite", ACTIVITES_TESTEES)
@pytest.mark.parametrize("format", ["texte", "json", "md", "pdf"])
def test_diagnose_pour_chaque_activite_et_chaque_format(
    commande: Commande, tmp_path: Path, activite: str | None, format: str
) -> None:
    fichier = tmp_path / f"diagnostic.{format}"
    sortie_fichier = ["--output", fichier] if format in ("md", "pdf") else []
    code, sortie = commande(
        "diagnose",
        "--adresse",
        LYON,
        *_projet(45, 300_000, activite),
        "--sans-risques",
        "--sans-carte",
        "--format",
        format,
        *sortie_fichier,
    )
    assert code == 0
    match format:
        case "texte":
            assert "Puissance 45 MW → HTB, RTE" in sortie
            assert "Brancher ici ?" in sortie
            assert ("Activité :" in sortie) == (activite is not None)
        case "json":
            reponse = json.loads(sortie)
            assert reponse["seuil"]["domaine_turpe"] == "htb1"
            assert reponse["activite"] == activite
            _criteres(reponse["verdict"])
        case "md":
            texte = fichier.read_text(encoding="utf-8")
            assert texte.startswith("# ")
            assert "Brancher ici" in texte
        case "pdf":
            assert fichier.read_bytes().startswith(b"%PDF")
            assert fichier.stat().st_size > 10_000


@pytest.mark.parametrize("activite", ACTIVITES_TESTEES)
@pytest.mark.parametrize("format", ["texte", "md", "csv", "json"])
def test_compare_pour_chaque_activite_et_chaque_format(
    commande: Commande, activite: str | None, format: str
) -> None:
    code, sortie = commande(
        "compare",
        "--adresse",
        LYON,
        "--adresse",
        AJACCIO,
        *_projet(12, 70_000, activite),
        "--sans-risques",
        "--format",
        format,
    )
    assert code == 0
    match format:
        case "texte":
            assert sortie.startswith("COMPARAISON DE 2 ADRESSES (sans classement)")
        case "md":
            assert sortie.startswith("# Comparaison de 2 adresses")
        case "csv":
            entete = sortie.splitlines()[0]
            assert entete == "indicateur;1 rue de test, Lyon;1 rue de test, Ajaccio;limite"
        case "json":
            reponse = json.loads(sortie)
            assert len(reponse["diagnostics"]) == 2
            assert reponse["tableau"][-1]["indicateur"] == "Brancher ici ?"
            assert all(len(ligne["valeurs"]) == 2 for ligne in reponse["tableau"])


@pytest.mark.parametrize("nombre", [1, 7])
def test_compare_refuse_hors_de_2_a_6_adresses(commande: Commande, nombre: int) -> None:
    adresses = ["--adresse", LYON] * nombre
    assert commande("compare", *adresses, "--sans-risques")[0] == 1


@pytest.mark.parametrize(("code_commune", "commune", "systeme", "decarbone"), RECETTE)
@pytest.mark.parametrize("puissance", [12, 45])
def test_diagnose_dans_chaque_territoire(
    commande: Commande,
    code_commune: str,
    commune: str,
    systeme: str,
    decarbone: bool,
    puissance: float,
) -> None:
    code, sortie = commande(
        "diagnose",
        "--adresse",
        f"{code_commune}|{commune}",
        *_projet(puissance, puissance * 6_000, "data-center"),
        "--sans-risques",
        "--sans-carte",
        "--format",
        "json",
    )
    assert code == 0
    reponse = json.loads(sortie)
    assert reponse["est_zni"] == (systeme != "France continentale")
    assert reponse["carbone"]["systeme_electrique"] == systeme
    criteres = _criteres(reponse["verdict"])
    assert criteres["Carbone"]["etat"] == ("ok" if decarbone else "attention")


# Défaut connu (9 novembre 2026) : en Corse et outre-mer, le réseau n'est pas exploité par RTE,
# ce que la section « Tension du réseau » dit déjà ; le verdict annonce pourtant « HTB, RTE »
@pytest.mark.xfail(strict=True, reason="gestionnaire des zones non interconnectées à sourcer")
def test_raccordement_hors_rte_en_zone_non_interconnectee(commande: Commande) -> None:
    code, sortie = commande(
        "essentiel",
        "--adresse",
        "97411|Saint-Denis",
        *_projet(45, 300_000, None),
        "--sans-carte",
        "--format",
        "json",
    )
    assert code == 0
    assert "RTE" not in _criteres(json.loads(sortie)["verdict"])["Raccordement"]["valeur"]


# Commune absente des jeux locaux : le diagnostic se replie sur le département, sans échouer
def test_commune_sans_donnee_propre(commande: Commande) -> None:
    code, sortie = commande(
        "diagnose", "--adresse", "01001|Ailleurs", "--sans-risques", "--sans-carte"
    )
    assert code == 0
    assert "Brancher ici ?" in sortie


def test_adresse_seule(commande: Commande) -> None:
    code, sortie = commande("essentiel", "--adresse", LYON, "--sans-carte", "--format", "json")
    assert code == 0
    reponse = json.loads(sortie)
    assert reponse["prix"]["est_tranche_par_defaut"] is True
    assert reponse["acheminement"] is None
    assert _criteres(reponse["verdict"])["Raccordement"]["etat"] == "inconnu"


@pytest.mark.parametrize(
    ("options", "cle"),
    [
        (["--ca", 50_000_000], "part_chiffre_affaires"),
        (["--va", 500_000_000], "part_valeur_ajoutee"),
        (["--surface", 2_000], "locaux"),
    ],
)
def test_chaque_option_du_projet(commande: Commande, options: list[object], cle: str) -> None:
    code, sortie = commande(
        "essentiel",
        "--adresse",
        LYON,
        *_projet(45, 300_000, None),
        *options,
        "--sans-carte",
        "--format",
        "json",
    )
    assert code == 0
    reponse = json.loads(sortie)
    assert reponse[cle] is not None
    if cle == "locaux":
        assert reponse[cle]["surface_m2"] == 2_000


# Accise réduite : la catégorie suit l'activité et l'électro-intensité, jamais l'inverse
@pytest.mark.parametrize(
    ("activite", "va", "categorie"),
    [
        (None, None, None),
        (None, 500_000_000, "grande_consommatrice"),
        (None, 5_000_000, "electro_intensive"),
        ("industrie", 500_000_000, "grande_consommatrice"),
        ("industrie", None, None),
        ("data-center", None, "centre_stockage_donnees"),
        ("data-center", 5_000_000, "centre_stockage_donnees"),
        # Moins de 2,25 % d'électro-intensité : la condition du tarif réduit n'est pas remplie
        ("data-center", 500_000_000, None),
        ("irve", 5_000_000, None),
        ("logistique", 5_000_000, None),
    ],
)
def test_accise_reduite_selon_l_activite(
    commande: Commande, activite: str | None, va: float | None, categorie: str | None
) -> None:
    options = [] if va is None else ["--va", va]
    code, sortie = commande(
        "essentiel",
        "--adresse",
        LYON,
        *_projet(45, 300_000, activite),
        *options,
        "--sans-carte",
        "--format",
        "json",
    )
    assert code == 0
    reduite = json.loads(sortie)["accise_reduite"]
    assert (reduite or {}).get("categorie") == categorie
    if reduite is not None:
        assert 0 < reduite["mwh_reduits"] <= 300_000
        assert reduite["tarif_eur_mwh"] * reduite["mwh_reduits"] < reduite["accise_normale_eur"]


@pytest.mark.parametrize("commande_cli", ["essentiel", "diagnose", "compare"])
@pytest.mark.parametrize(
    "options",
    [
        # 12 MW ne peuvent pas consommer 300 GWh en un an
        ["--puissance", "12", "--conso", "300000"],
        ["--ca", "1000000"],
        ["--va", "1000000"],
        ["--puissance", "-1"],
        ["--conso", "0"],
        ["--activite", "bureaux"],
    ],
)
def test_projet_incoherent_refuse_avant_tout_calcul(
    commande_cli: str, options: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    adresses = ["--adresse", LYON] * (2 if commande_cli == "compare" else 1)
    with pytest.raises(SystemExit) as sortie:
        cli.main([commande_cli, *adresses, *options])
    assert sortie.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_application_refuse_la_meme_incoherence() -> None:
    with pytest.raises(app.ProjetInvalideError, match="impossible avec 12 MW"):
        app.lire_projet({"adresse": "a", "puissance_mw": "12", "conso_mwh": "300000"})


# Warehouse jamais chargé : chaque commande de question renvoie à `refresh`, sans trace Python
@pytest.mark.parametrize("commande_cli", ["essentiel", "diagnose", "compare", "carte"])
def test_sans_warehouse_la_commande_renvoie_a_refresh(
    commande_cli: str,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "geocode", lambda adresse, **_: territoire(*adresse.split("|")))
    adresses = ["--adresse", LYON] * (2 if commande_cli == "compare" else 1)
    suite = ["--sans-ouvrir"] if commande_cli == "carte" else []

    assert cli.main([commande_cli, *adresses, *suite]) == 1
    assert "refresh" in caplog.text + capsys.readouterr().err
