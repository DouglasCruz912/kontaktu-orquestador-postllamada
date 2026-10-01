import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EVENTOS = RAIZ / "eventos"


def cargar_evento(nombre: str) -> dict:
    return json.loads((EVENTOS / nombre).read_text(encoding="utf-8"))


def eventos_en_orden() -> list[str]:
    lineas = (EVENTOS / "orden.txt").read_text(encoding="utf-8").splitlines()
    return [linea.strip() for linea in lineas if linea.strip() and not linea.startswith("#")]
