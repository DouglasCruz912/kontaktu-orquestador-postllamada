"""Ejecuta un lote como lo hará Kontaktu: desde cero, un proceso nuevo por evento, en el orden de orden.txt.

Después valida cada línea contra los esquemas y, si el lote es el de ejemplo, compara con la tabla dorada.

    uv run python scripts/ejecutar_lote.py                 # eventos/ → salida/
    uv run python scripts/ejecutar_lote.py --dir X --salida Y
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from orquestador.dominio import Orden  # noqa: E402
from orquestador.salida import errores_decision, errores_orden  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, default=RAIZ / "eventos")
    parser.add_argument("--salida", type=Path, default=RAIZ / "salida")
    args = parser.parse_args()

    if args.salida.exists():
        shutil.rmtree(args.salida)  # desde cero: ni salida ni estado de ejecuciones anteriores
    entorno = {**os.environ, "SALIDA_DIR": str(args.salida), "PYTHONIOENCODING": "utf-8"}

    nombres = [
        linea.strip()
        for linea in (args.dir / "orden.txt").read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.startswith("#")
    ]
    inicio = time.time()
    for nombre in nombres:
        t = time.time()
        proceso = subprocess.run(
            [sys.executable, str(RAIZ / "run.py"), str(args.dir / nombre)],
            env=entorno,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        estado = "ok" if proceso.returncode == 0 else f"EXIT {proceso.returncode}"
        print(f"{nombre:42} {estado:8} {time.time() - t:5.1f}s  {proceso.stdout.strip()}")
        if proceso.stderr.strip():
            print("    " + proceso.stderr.strip().replace("\n", "\n    "))
    print(f"total {time.time() - inicio:.1f}s")

    decisiones = _leer(args.salida / "decisiones.jsonl")
    ordenes = _leer(args.salida / "ordenes.jsonl")
    fallos = [f"decisión {d['event_id']}: {e}" for d in decisiones for e in errores_decision(d)]
    fallos += [
        f"orden {o['orden_id']}: {e}"
        for o in ordenes
        for e in errores_orden(Orden(o["operacion"], o["idempotency_key"], o["cuerpo"]))
    ]
    if len(decisiones) != len(nombres):
        fallos.append(f"{len(decisiones)} decisiones para {len(nombres)} eventos")
    print(f"\n{len(decisiones)} decisiones · {len(ordenes)} órdenes · {len(fallos)} errores de esquema")
    for fallo in fallos:
        print("  ESQUEMA", fallo)

    if args.dir.resolve() == (RAIZ / "eventos").resolve():
        fallos += _comparar_con_dorada(decisiones, ordenes)
    return 1 if fallos else 0


def _comparar_con_dorada(decisiones: list[dict], ordenes: list[dict]) -> list[str]:
    from tests.test_lote_ejemplo import DORADA

    fallos = []
    for decision in decisiones:
        etiqueta, esperadas = DORADA[decision["event_id"]]
        propias = [o["operacion"] for o in ordenes if o["event_id"] == decision["event_id"]]
        if decision["etiqueta"] != etiqueta or propias != [op for op, _, _ in esperadas]:
            fallos.append(f"{decision['event_id']}: {decision['etiqueta']} {propias} ≠ {etiqueta}")
    print(f"tabla dorada: {len(decisiones) - len(fallos)}/{len(decisiones)} eventos coinciden")
    for fallo in fallos:
        print("  DORADA", fallo)
    return fallos


def _leer(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]


if __name__ == "__main__":
    sys.exit(main())
