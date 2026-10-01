"""Hook Stop: Claude no puede dar el turno por terminado con los tests en rojo.

Si ya estamos dentro de un Stop forzado por este hook (stop_hook_active), se deja
parar para no entrar en bucle. Sin pyproject.toml o sin tests no hace nada.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# En Windows stderr sale en cp1252 y los acentos llegan rotos a Claude.
sys.stderr.reconfigure(encoding="utf-8")

payload = json.loads(sys.stdin.read() or "{}")
project = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))

if payload.get("stop_hook_active") or not (project / "tests").is_dir():
    sys.exit(0)

result = subprocess.run(
    ["uv", "run", "pytest", "-q", "-x", "--no-header"],
    cwd=project,
    capture_output=True,
    text=True,
)
# 5 = pytest no encontró tests: no es un fallo.
if result.returncode not in (0, 5):
    tail = (result.stdout + result.stderr).strip().splitlines()[-40:]
    sys.stderr.write("Hay tests en rojo; arréglalos antes de terminar:\n" + "\n".join(tail) + "\n")
    sys.exit(2)
