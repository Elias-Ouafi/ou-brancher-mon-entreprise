import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parents[2]

# Exemples de référence du plan : 12 MW en HTA, 45 MW en HTB
CHILLY = "3 allée des peupliers 91380 Chilly-Mazarin"
PROJET_12_MW = ["--puissance", "12", "--conso", "70000"]
PROJET_45_MW = ["--puissance", "45", "--conso", "300000"]
HTA = {"domaine de tension": "hta", "raccordement": "HTA, Enedis (ou ELD)"}
CONTINENT = {"système électrique": "France continentale", "décarbonée": True}


def lancer(titre: str, args: list[str]) -> bool:
    print(f"\n== {titre}", flush=True)
    debut = time.monotonic()
    ok = subprocess.run([sys.executable, *args], cwd=RACINE).returncode == 0
    print(f"-- {'OK' if ok else 'ÉCHEC'} ({time.monotonic() - debut:.0f} s)", flush=True)
    return ok


def cli(*args: str) -> dict[str, Any]:
    resultat = subprocess.run(
        [sys.executable, "-m", "ingestion.cli", *args, "--format", "json"],
        cwd=RACINE,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if resultat.returncode:
        raise RuntimeError(resultat.stderr.strip() or f"code de sortie {resultat.returncode}")
    return json.loads(resultat.stdout)


# Le vrai serveur de `app`, sur un port libre ; l'analyse épingle Chilly-Mazarin sur
# data/reports/carte.html, comme le fait l'application
def api_analyse() -> dict[str, Any]:
    os.chdir(RACINE)
    sys.path.insert(0, str(RACINE))
    from reporting.app import creer_serveur, url

    serveur = creer_serveur(port=0)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    try:
        corps = json.dumps({"adresse": CHILLY, "puissance_mw": 12, "conso_mwh": 70000})
        requete = urllib.request.Request(
            url(serveur) + "api/analyse",
            data=corps.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(requete, timeout=120) as reponse:
            return json.load(reponse)["adresses"][0]["essentiel"]
    finally:
        serveur.shutdown()
        serveur.server_close()


def releve(sortie: dict[str, Any]) -> dict[str, Any]:
    if "tableau" in sortie:
        territoires = next(
            ligne["valeurs"] for ligne in sortie["tableau"] if ligne["indicateur"] == "Territoire"
        )
        return {"communes": [t.split(" (")[0] for t in territoires]}
    criteres = {c["nom"]: c["valeur"] for c in sortie["verdict"]["criteres"]}
    return {
        "domaine de tension": sortie["acheminement"]["domaine_turpe"],
        "raccordement": criteres["Raccordement"],
        "système électrique": sortie["carbone"]["systeme_electrique"],
        "décarbonée": sortie["carbone"]["est_decarbone"],
        # Le diagnostic aboutit même si une source à la demande tombe : `--complet` la nomme
        "sources à la demande en échec": sorted(
            cle.removesuffix("_erreur")
            for cle, valeur in sortie.items()
            if cle.endswith("_erreur") and valeur
        ),
    }


Essai = tuple[str, Callable[[], dict[str, Any]], dict[str, Any]]

ESSAIS: list[Essai] = [
    (
        "essentiel, 12 MW à Chilly-Mazarin",
        lambda: cli("essentiel", "--adresse", CHILLY, *PROJET_12_MW, "--sans-carte"),
        HTA | CONTINENT,
    ),
    (
        "diagnose --sans-risques, 45 MW à Chilly-Mazarin",
        lambda: cli(
            "diagnose", "--adresse", CHILLY, *PROJET_45_MW, "--sans-risques", "--sans-carte"
        ),
        {"domaine de tension": "htb1", "raccordement": "HTB, RTE"} | CONTINENT,
    ),
    (
        "compare, Chilly-Mazarin et Lyon à 12 MW",
        lambda: cli(
            "compare",
            *("--adresse", CHILLY),
            *("--adresse", "1 place Bellecour 69002 Lyon"),
            *PROJET_12_MW,
            "--sans-risques",
        ),
        {"communes": ["Chilly-Mazarin", "Lyon"]},
    ),
    (
        "essentiel, 12 MW à Ajaccio",
        lambda: cli(
            "essentiel",
            "--adresse",
            "1 cours Napoléon 20000 Ajaccio",
            *PROJET_12_MW,
            "--sans-carte",
        ),
        {
            "domaine de tension": "hta",
            "raccordement": "HTA, EDF SEI",
            "système électrique": "Corse",
            "décarbonée": False,
        },
    ),
    ("application, /api/analyse à 12 MW", api_analyse, HTA | CONTINENT),
]

ESSAI_COMPLET: Essai = (
    "diagnose avec les sources à la demande, 12 MW à Chilly-Mazarin",
    lambda: cli("diagnose", "--adresse", CHILLY, *PROJET_12_MW, "--sans-carte"),
    HTA | CONTINENT | {"sources à la demande en échec": []},
)


def essayer(titre: str, obtenir: Callable[[], dict[str, Any]], attendu: dict[str, Any]) -> bool:
    print(f"\n== essai réel : {titre}", flush=True)
    debut = time.monotonic()
    try:
        obtenu = releve(obtenir())
    except (RuntimeError, OSError, ValueError, LookupError, StopIteration) as error:
        print(f"   {type(error).__name__} : {error}")
        ok = False
    else:
        ok = True
        for cle, valeur in attendu.items():
            juste = obtenu[cle] == valeur
            print(f"   {cle} : {obtenu[cle]}" + ("" if juste else f" (attendu : {valeur})"))
            ok = ok and juste
    print(f"-- {'OK' if ok else 'ÉCHEC'} ({time.monotonic() - debut:.0f} s)", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="ruff, pytest et essai réel, comme la CI")
    parser.add_argument("--rapide", action="store_true", help="sans pytest")
    parser.add_argument("--sans-essai", action="store_true", help="sans l'essai réel")
    parser.add_argument(
        "--complet", action="store_true", help="essai réel avec les sources à la demande"
    )
    parser.add_argument("pytest_args", nargs="*", help="arguments passés à pytest")
    args = parser.parse_args()
    # La console Windows est en cp1252 quand la sortie est redirigée
    sys.stdout.reconfigure(encoding="utf-8")

    etapes = [
        ("ruff check", ["-m", "ruff", "check", "."]),
        ("ruff format --check", ["-m", "ruff", "format", "--check", "."]),
    ]
    if not args.rapide:
        # Le plancher de couverture ne vaut que pour la suite entière
        mesure = [] if args.pytest_args else ["--cov"]
        etapes.append(("pytest", ["-m", "pytest", *mesure, *args.pytest_args]))
    echecs = [titre for titre, commande in etapes if not lancer(titre, commande)]

    if not args.sans_essai:
        essais = [*ESSAIS, ESSAI_COMPLET] if args.complet else ESSAIS
        echecs += [essai[0] for essai in essais if not essayer(*essai)]

    print("\n" + ("Tout est vert." if not echecs else "En échec : " + ", ".join(echecs)))
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
