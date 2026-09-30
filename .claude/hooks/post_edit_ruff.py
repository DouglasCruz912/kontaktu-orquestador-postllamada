"""Hook PostToolUse (Edit|Write): ruff sobre el .py recién editado.

Arregla lo automático (imports, formato) y, si queda algún error, sale con código 2
y lo manda por stderr: Claude lo ve y lo corrige. Sin pyproject.toml no hace nada
(el proyecto aún no está montado).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# En Windows stderr sale en cp1252 y los acentos llegan rotos a Claude.
sys.stderr.reconfigure(encoding="utf-8")

payload = json.loads(sys.stdin.read() or "{}")
file_path = (payload.get("tool_input") or {}).get("file_path", "")
project = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))

if not file_path.endswith(".py") or not (project / "pyproject.toml").exists():
    sys.exit(0)

subprocess.run(
    ["uv", "run", "ruff", "format", file_path], cwd=project, capture_output=True
)
check = subprocess.run(
    ["uv", "run", "ruff", "check", "--fix", "--output-format", "concise", file_path],
    cwd=project,
    capture_output=True,
    text=True,
)
if check.returncode != 0:
    lines = (check.stdout + check.stderr).strip().splitlines()
    sys.stderr.write(
        f"ruff encontró errores en {file_path}:\n" + "\n".join(lines[:30]) + "\n"
    )
    sys.exit(2)
