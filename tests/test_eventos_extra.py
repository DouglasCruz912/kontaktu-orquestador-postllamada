"""Casos sin ejemplo en eventos/ (⚠ de casos.md, pares que se parecen, N1–N4 con estado, fines de semana).

El doble responde lo que guarda esperado.json; el mismo lote se ejecuta con el LLM real en scripts/ejecutar_lote.py.
"""

import json
from pathlib import Path

import pytest

from orquestador.aplicacion import procesar
from orquestador.clasificador import ClasificacionLLM
from orquestador.dominio import Orden
from orquestador.salida import errores_decision, errores_orden

EXTRA = Path(__file__).resolve().parent / "eventos_extra"
ESPERADO = json.loads((EXTRA / "esperado.json").read_text(encoding="utf-8"))


class DobleDesdeEsperado:
    def clasificar(self, evento: dict) -> ClasificacionLLM:
        return ClasificacionLLM(**ESPERADO[evento["event_id"]]["llm"])


@pytest.fixture(scope="module")
def salida(tmp_path_factory):
    directorio = tmp_path_factory.mktemp("extra")
    nombres = (EXTRA / "orden.txt").read_text(encoding="utf-8").split()
    codigos = [procesar(EXTRA / n, directorio, DobleDesdeEsperado()) for n in nombres]
    leer = lambda f: [json.loads(x) for x in (directorio / f).read_text("utf-8").splitlines()]  # noqa: E731
    return {"codigos": codigos, "decisiones": leer("decisiones.jsonl"), "ordenes": leer("ordenes.jsonl")}


def test_todos_procesados_y_validos(salida):
    assert set(salida["codigos"]) == {0}
    assert len(salida["decisiones"]) == len(ESPERADO)
    assert all(errores_decision(d) == [] for d in salida["decisiones"])
    for o in salida["ordenes"]:
        assert errores_orden(Orden(o["operacion"], o["idempotency_key"], o["cuerpo"])) == [], o


@pytest.mark.parametrize("event_id", list(ESPERADO))
def test_caso(salida, event_id):
    esperado = ESPERADO[event_id]
    decision = next(d for d in salida["decisiones"] if d["event_id"] == event_id)
    ordenes = [o for o in salida["ordenes"] if o["event_id"] == event_id]
    assert decision["etiqueta"] == esperado["etiqueta"]
    assert [o["operacion"] for o in ordenes] == esperado["operaciones"]
    for indice, campos in esperado["campos"].items():
        for campo, valor in campos.items():
            assert ordenes[int(indice)]["cuerpo"][campo] == valor, (event_id, campo)


def test_n1_ningun_whatsapp_al_lead_que_lo_rechazo(salida):
    ordenes = [o for o in salida["ordenes"] if o["cuerpo"].get("telefono") == "+34600000412"]
    assert ordenes, "el lead 412 debería tener órdenes"
    assert all(o["operacion"] != "enviar_plantilla_whatsapp" for o in ordenes)
