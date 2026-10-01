"""Procesa un evento de principio a fin. run.py es solo la puerta de entrada de la línea de comandos.

Códigos de salida: 0 procesado · 1 error inesperado (queda una decisión de respaldo) · 2 entrada ilegible.
"""

import json
import os
import sys
import traceback
from pathlib import Path

from jsonschema import Draft202012Validator

from orquestador.clasificador import Clasificador
from orquestador.config import RAIZ, cargar_config
from orquestador.grafo import Contexto, construir_grafo
from orquestador.persistencia import Repositorio
from orquestador.salida import ESQUEMAS, _anadir_lineas

CAMPOS_MINIMOS = ("event_id", "type", "organization_id", "idempotency_key", "occurred_at")


def dir_salida_por_defecto() -> Path:
    return Path(os.environ.get("SALIDA_DIR") or RAIZ / "salida")


def procesar(ruta_evento: Path, dir_salida: Path | None = None, clasificador: Clasificador | None = None) -> int:
    dir_salida = dir_salida or dir_salida_por_defecto()
    try:
        evento = json.loads(Path(ruta_evento).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[error] no se puede leer el evento {ruta_evento}: {e}", file=sys.stderr)
        return 2
    if not isinstance(evento, dict) or not evento.get("event_id"):
        print(f"[error] {ruta_evento} no es un evento (falta event_id)", file=sys.stderr)
        return 2

    # Validación tolerante: un aviso no detiene el proceso si están los campos que se usan.
    for error in _errores_evento(evento):
        print(f"[aviso] el evento no cumple evento.schema.json: {error}", file=sys.stderr)
    faltan = [c for c in CAMPOS_MINIMOS if not evento.get(c)] + [
        c for c in ("contact_id", "phone") if not (evento.get("lead") or {}).get(c)
    ]
    if faltan:
        return _fallo(dir_salida, evento, f"evento no procesable, faltan campos: {faltan}")

    repo = None
    try:
        if clasificador is None:
            from orquestador.clasificador_openai import ClasificadorOpenAI

            clasificador = ClasificadorOpenAI.desde_entorno()
        repo = Repositorio(dir_salida / "estado.sqlite")
        contexto = Contexto(config=cargar_config(), repo=repo, clasificador=clasificador, dir_salida=dir_salida)
        resultado = construir_grafo().invoke({"evento": evento, "avisos": []}, context=contexto)
    except Exception as e:  # R8: este evento falla, pero deja rastro y no bloquea los siguientes
        traceback.print_exc(file=sys.stderr)
        return _fallo(dir_salida, evento, f"error interno: {type(e).__name__}: {e}")
    finally:
        if repo is not None:
            repo.cerrar()

    for aviso in resultado.get("avisos", []):
        print(f"[aviso] {evento['event_id']}: {aviso}", file=sys.stderr)
    decision = resultado["decision"]
    print(f"{decision['event_id']}: {decision['etiqueta']} ({len(decision['ordenes'])} órdenes)")
    return 0


def _errores_evento(evento: dict) -> list[str]:
    esquema = json.loads((ESQUEMAS / "evento.schema.json").read_text(encoding="utf-8"))
    return [e.message for e in Draft202012Validator(esquema).iter_errors(evento)]


def _fallo(dir_salida: Path, evento: dict, motivo: str) -> int:
    """Una línea por evento recibido, también si no se pudo procesar: `otro` con confianza 0 y sin órdenes."""
    print(f"[error] {evento.get('event_id')}: {motivo}", file=sys.stderr)
    decision = {
        "event_id": str(evento["event_id"]),
        "call_id": (evento.get("telephony") or {}).get("call_id"),
        "etiqueta": "otro",
        "motivo": motivo[:500],
        "confianza": 0.0,
        "ordenes": [],
    }
    try:
        _anadir_lineas(dir_salida / "decisiones.jsonl", [decision])
    except OSError:
        traceback.print_exc(file=sys.stderr)
    return 1
