import argparse
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]

# Exemple de référence du plan : HTA, tranche IF
ESSAI = [
    "-m",
    "ingestion.cli",
    "essentiel",
    "--adresse",
    "3 allée des peupliers 91380 Chilly-Mazarin",
    "--puissance",
    "12",
    "--conso",
    "70000",
    "--sans-carte",
]


def lancer(titre: str, args: list[str]) -> bool:
    print(f"\n== {titre}", flush=True)
    debut = time.monotonic()
    ok = subprocess.run([sys.executable, *args], cwd=RACINE).returncode == 0
    print(f"-- {'OK' if ok else 'ÉCHEC'} ({time.monotonic() - debut:.0f} s)", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="ruff, pytest et essai réel, comme la CI")
    parser.add_argument("--rapide", action="store_true", help="sans pytest")
    parser.add_argument("--sans-essai", action="store_true", help="sans l'essai réel")
    parser.add_argument("pytest_args", nargs="*", help="arguments passés à pytest")
    args = parser.parse_args()
    # La console Windows est en cp1252 quand la sortie est redirigée
    sys.stdout.reconfigure(encoding="utf-8")

    etapes = [
        ("ruff check", ["-m", "ruff", "check", "."]),
        ("ruff format --check", ["-m", "ruff", "format", "--check", "."]),
    ]
    if not args.rapide:
        etapes.append(("pytest", ["-m", "pytest", *args.pytest_args]))
    if not args.sans_essai:
        etapes.append(("essai réel : essentiel, Chilly-Mazarin 12 MW", ESSAI))

    echecs = [titre for titre, commande in etapes if not lancer(titre, commande)]
    print("\n" + ("Tout est vert." if not echecs else "En échec : " + ", ".join(echecs)))
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
