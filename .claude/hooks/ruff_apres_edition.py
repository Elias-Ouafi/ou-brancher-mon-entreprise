import json
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]

# Hook PostToolUse : formate le fichier Python édité et signale à Claude le lint restant.
# Pas de --fix : un import ajouté avant son usage serait supprimé entre deux éditions.
chemin = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
if not chemin.endswith(".py"):
    sys.exit(0)

ruff = [sys.executable, "-m", "ruff"]
subprocess.run([*ruff, "format", "--quiet", chemin], cwd=RACINE)
lint = subprocess.run(
    [*ruff, "check", "--output-format", "concise", chemin],
    cwd=RACINE,
    capture_output=True,
    text=True,
)
if lint.returncode != 0:
    print(lint.stdout.strip(), file=sys.stderr)
    sys.exit(2)
