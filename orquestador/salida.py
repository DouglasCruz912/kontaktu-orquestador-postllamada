"""Validación contra los esquemas del reto y escritura transaccional de salida/*.jsonl.

Los esquemas se leen de esquemas/ (contrato del reto): decision.schema.json y el requestBody de cada
operación del OpenAPI. OpenAPI 3.1 usa JSON Schema 2020-12, así que se valida con jsonschema tal cual.
"""

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from orquestador.config import RAIZ
from orquestador.dominio import Clasificacion, Orden, Recordatorio
from orquestador.persistencia import Repositorio
from orquestador.politica import reminder_id_de

ESQUEMAS = RAIZ / "esquemas"


@cache
def _validador_decision() -> Draft202012Validator:
    return Draft202012Validator(json.loads((ESQUEMAS / "decision.schema.json").read_text(encoding="utf-8")))


@cache
def _validadores_ordenes() -> dict[str, Draft202012Validator]:
    openapi = yaml.safe_load((ESQUEMAS / "crm-openapi.yaml").read_text(encoding="utf-8"))
    validadores = {}
    for metodos in openapi["paths"].values():
        for operacion in metodos.values():
            esquema = operacion["requestBody"]["content"]["application/json"]["schema"]
            validadores[operacion["operationId"]] = Draft202012Validator(esquema)
    return validadores


def errores_orden(orden: Orden) -> list[str]:
    validador = _validadores_ordenes().get(orden.operacion)
    if validador is None:
        return [f"operación desconocida: {orden.operacion}"]
    return [e.message for e in validador.iter_errors(orden.cuerpo)]


def errores_decision(decision: dict) -> list[str]:
    return [e.message for e in _validador_decision().iter_errors(decision)]


@dataclass
class CambiosLead:
    rechaza_whatsapp: bool | None = None


def emitir(
    repo: Repositorio,
    dir_salida: Path,
    evento: dict,
    clasif: Clasificacion,
    ordenes: list[Orden],
    *,
    registrar_evento: bool,
    cambios_lead: CambiosLead | None = None,
) -> tuple[dict, list[str]]:
    """Escribe las órdenes nuevas y la decisión del evento, y persiste el estado. Todo en una transacción.

    Devuelve la decisión escrita y los avisos (órdenes omitidas por idempotencia o por no validar).
    """
    avisos: list[str] = []
    lineas_ordenes: list[dict] = []
    with repo.transaccion():
        for orden in ordenes:
            if repo.orden_emitida(orden.idempotency_key):  # R5: el mismo hecho no duplica órdenes
                avisos.append(f"orden ya emitida, se omite: {orden.idempotency_key}")
                continue
            errores = errores_orden(orden)
            if errores:  # nunca se escribe algo que el CRM rechazaría; queda constancia
                avisos.append(f"orden {orden.operacion} no válida, se omite: {errores}")
                continue
            repo.registrar_orden(
                orden.orden_id, evento["event_id"], orden.operacion, orden.idempotency_key, orden.cuerpo
            )
            _efectos_en_estado(repo, evento, orden)
            lineas_ordenes.append(
                {
                    "orden_id": orden.orden_id,
                    "event_id": evento["event_id"],
                    "operacion": orden.operacion,
                    "idempotency_key": orden.idempotency_key,
                    "cuerpo": orden.cuerpo,
                }
            )

        if registrar_evento:
            repo.registrar_evento(evento, clasif)
        if cambios_lead and cambios_lead.rechaza_whatsapp is not None:
            repo.marcar_lead(evento["lead"]["contact_id"], rechaza_whatsapp=cambios_lead.rechaza_whatsapp)

        decision = {
            "event_id": evento["event_id"],
            "call_id": (evento.get("telephony") or {}).get("call_id"),
            "etiqueta": clasif.etiqueta,
            "motivo": clasif.motivo,
            "confianza": clasif.confianza,
            "ordenes": [linea["orden_id"] for linea in lineas_ordenes],
        }
        errores = errores_decision(decision)
        if errores:
            raise ValueError(f"la decisión no cumple decision.schema.json: {errores}")

        # Dentro de la transacción: si la escritura falla, ROLLBACK y no queda nada a medias en el estado.
        _anadir_lineas(dir_salida / "ordenes.jsonl", lineas_ordenes)
        _anadir_lineas(dir_salida / "decisiones.jsonl", [decision])
    return decision, avisos


def _efectos_en_estado(repo: Repositorio, evento: dict, orden: Orden) -> None:
    """Se sigue «como si el CRM hubiera respondido lo que declara el OpenAPI»: el efecto se guarda para después."""
    contact_id = evento["lead"]["contact_id"]
    if orden.operacion == "programar_recordatorio":
        c = orden.cuerpo
        repo.crear_recordatorio(
            Recordatorio(reminder_id_de(orden), contact_id, c["canal"], c["cuando"], c["cancelar_si"])
        )
    elif orden.operacion == "cancelar_recordatorio":
        repo.cancelar_recordatorio(orden.cuerpo["reminder_id"])
    elif orden.operacion == "marcar_no_contactar":
        repo.marcar_lead(contact_id, baja=True)


def _anadir_lineas(ruta: Path, lineas: list[dict]) -> None:
    if not lineas:
        return
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8", newline="\n") as f:
        for linea in lineas:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
