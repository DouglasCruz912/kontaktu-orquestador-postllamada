"""Orquestador post-llamada. Uso: python run.py eventos/01-call-ended-nuria.json

Procesa UN evento por proceso y añade sus líneas a salida/decisiones.jsonl y salida/ordenes.jsonl.
La configuración (OPENAI_API_KEY, MODELO) se lee del entorno o de un fichero .env en la raíz.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))  # permite ejecutar run.py desde cualquier directorio

from orquestador.aplicacion import procesar  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: python run.py <ruta-del-evento.json>", file=sys.stderr)
        return 2
    load_dotenv(RAIZ / ".env")
    return procesar(Path(argv[1]))


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")  # en Windows la consola no es UTF-8 por defecto
    sys.exit(main(sys.argv))
