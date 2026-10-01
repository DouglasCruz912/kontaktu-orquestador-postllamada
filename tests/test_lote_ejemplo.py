"""Integración: los 16 eventos de ejemplo en el orden de orden.txt, con un clasificador doble.

El doble devuelve lo que se espera que diga el LLM para cada conversación; así se prueba todo lo demás
(señalización, reglas duras, política, fechas, persistencia entre procesos e idempotencia) de forma determinista.
La tabla dorada la calcularon por separado la IA principal y un agente revisor, y coincidieron (PROCESS.md).
"""

import json

import pytest

from orquestador.aplicacion import procesar
from orquestador.clasificador import ClasificacionLLM
from orquestador.salida import errores_decision, errores_orden
from tests.utiles import EVENTOS, eventos_en_orden


def llm(etiqueta, motivo="", **extra) -> ClasificacionLLM:
    base = dict(
        etiqueta=etiqueta,
        motivo=motivo or etiqueta,
        confianza=0.9,
        pide_baja=False,
        rechaza_whatsapp=False,
        callback_local=None,
        nota_contexto=None,
        email=None,
    )
    return ClasificacionLLM(**{**base, **extra})


RESPUESTAS_LLM = {
    "evt_03": llm("persona_equivocada", "contesta otra persona y no conoce a Elena"),
    "evt_04": llm("cortada", nota_contexto="alquiler; Majadahonda o Las Rozas; presupuesto ~1.200 €/mes a medias"),
    "evt_06": llm("no_contactar", "pide que no le llamen más y la baja", pide_baja=True),
    # El LLM se equivoca a propósito: la cita existe, así que la regla dura manda (visita_reservada).
    "evt_07": llm("visita_sin_confirmar"),
    "evt_08": llm("documentacion_enviada"),
    "evt_09": llm("callback", "pide que le llamen mañana a las 18:00", callback_local="2026-09-16T18:00"),
    "evt_11": llm("visita_sin_confirmar", nota_contexto="visita acordada de palabra el jueves 17 a las 17:00"),
    "evt_13": llm("documentacion_pendiente", rechaza_whatsapp=True, email="ivan.recalde@example.com"),
}


class ClasificadorDoble:
    def __init__(self):
        self.llamadas: list[str] = []

    def clasificar(self, evento: dict) -> ClasificacionLLM:
        self.llamadas.append(evento["event_id"])
        return RESPUESTAS_LLM[evento["event_id"]]


# (etiqueta, [(operacion, idempotency_key, {campo: valor esperado})])
DORADA = {
    "evt_01": ("sin_respuesta", [("cerrar_llamada", "lk-out-0301:cerrar_llamada", {"status": "no_answer"}),
                                 ("programar_llamada", "lk-out-0301:programar_llamada",
                                  {"no_antes_de": "2026-09-15T12:12:00+02:00"})]),
    "evt_02": ("ocupado", [("cerrar_llamada", "lk-out-0306:cerrar_llamada", {"status": "no_answer"}),
                           ("programar_llamada", "lk-out-0306:programar_llamada",
                            {"no_antes_de": "2026-09-15T11:31:00+02:00"})]),
    "evt_03": ("persona_equivocada", [("cerrar_llamada", "lk-out-0307:cerrar_llamada", {"status": "failed"}),
                                      ("crear_tarea", "lk-out-0307:crear_tarea",
                                       {"tipo": "verificar_telefono", "vence_el": "2026-09-17T11:04:00+02:00"})]),
    "evt_04": ("cortada", [("cerrar_llamada", "lk-out-0305:cerrar_llamada", {"status": "needs_review"}),
                           ("programar_llamada", "lk-out-0305:programar_llamada",
                            {"no_antes_de": "2026-09-15T12:17:00+02:00"})]),
    "evt_05": ("sin_respuesta", [("cerrar_llamada", "lk-out-0302:cerrar_llamada", {"status": "no_answer"}),
                                 ("programar_llamada", "lk-out-0302:programar_llamada",
                                  {"no_antes_de": "2026-09-15T14:20:00+02:00"})]),
    "evt_06": ("no_contactar", [("cerrar_llamada", "lk-out-0308:cerrar_llamada", {"status": "dnc"}),
                                ("marcar_no_contactar", "lk-out-0308:marcar_no_contactar", {"canal": "todos"})]),
    "evt_07": ("visita_reservada", [("cerrar_llamada", "lk-out-0309:cerrar_llamada", {"status": "successful"}),
                                    ("crear_tarea", "lk-out-0309:crear_tarea",
                                     {"tipo": "confirmar_visita_direccion",
                                      "vence_el": "2026-09-17T09:00:00+02:00"})]),
    "evt_08": ("documentacion_enviada", [
        ("cerrar_llamada", "lk-out-0310:cerrar_llamada", {"status": "completed"}),
        ("programar_recordatorio", "lk-out-0310:programar_recordatorio:lead",
         {"canal": "whatsapp_lead", "plantilla": "recordatorio_documentacion",
          "cuando": "2026-09-17T16:42:00+02:00", "cancelar_si": "lead_responde"}),
        ("programar_recordatorio", "lk-out-0310:programar_recordatorio:comercial",
         {"canal": "tarea_comercial", "tipo_tarea": "llamar_a_mano",
          "cuando": "2026-09-18T16:42:00+02:00", "cancelar_si": "lead_responde"}),
    ]),
    "evt_09": ("callback", [("cerrar_llamada", "lk-out-0311:cerrar_llamada", {"status": "callback_requested"}),
                            ("programar_llamada", "lk-out-0311:programar_llamada",
                             {"no_antes_de": "2026-09-16T18:00:00+02:00"})]),
    "evt_10": ("buzon", [("cerrar_llamada", "lk-out-0312:cerrar_llamada", {"status": "no_answer"}),
                         ("programar_llamada", "lk-out-0312:programar_llamada",
                          {"no_antes_de": "2026-09-15T19:30:00+02:00"})]),
    "evt_11": ("visita_sin_confirmar", [("cerrar_llamada", "lk-out-0315:cerrar_llamada", {"status": "needs_review"}),
                                        ("programar_llamada", "lk-out-0315:programar_llamada",
                                         {"no_antes_de": "2026-09-15T18:20:00+02:00"})]),
    "evt_12": ("buzon", [("cerrar_llamada", "lk-out-0313:cerrar_llamada", {"status": "no_answer"}),
                         ("enviar_plantilla_whatsapp", "lk-out-0313:enviar_plantilla_whatsapp",
                          {"plantilla": "primer_toque_respaldo", "telefono": "+34600000103"})]),
    "evt_13": ("documentacion_pendiente", [
        ("cerrar_llamada", "lk-out-0314:cerrar_llamada", {"status": "completed"}),
        ("crear_tarea", "lk-out-0314:crear_tarea",
         {"tipo": "enviar_documentacion_email", "vence_el": "2026-09-17T18:40:00+02:00"}),
    ]),
    "evt_14": ("no_aplica", [("cancelar_recordatorio", None, {}), ("cancelar_recordatorio", None, {})]),
    "evt_15": ("callback", []),
    "evt_16": ("no_aplica", []),
}  # fmt: skip


@pytest.fixture(scope="module")
def salida(tmp_path_factory):
    """Procesa el lote completo una vez: cada `procesar` abre su propio repo, como un proceso nuevo."""
    directorio = tmp_path_factory.mktemp("salida")
    doble = ClasificadorDoble()
    codigos = [procesar(EVENTOS / nombre, directorio, doble) for nombre in eventos_en_orden()]
    decisiones = [json.loads(linea) for linea in (directorio / "decisiones.jsonl").read_text("utf-8").splitlines()]
    ordenes = [json.loads(linea) for linea in (directorio / "ordenes.jsonl").read_text("utf-8").splitlines()]
    return {"codigos": codigos, "decisiones": decisiones, "ordenes": ordenes, "doble": doble}


def test_todos_los_eventos_terminan_bien(salida):
    assert salida["codigos"] == [0] * 16
    assert len(salida["decisiones"]) == 16


def test_el_llm_solo_se_usa_en_conversaciones(salida):
    # Ni telefonía resuelta, ni reentrega (evt_15), ni otra organización (evt_16), ni WhatsApp entrante.
    assert salida["doble"].llamadas == ["evt_03", "evt_04", "evt_06", "evt_07", "evt_08", "evt_09", "evt_11", "evt_13"]


def test_salida_cumple_los_esquemas(salida):
    for decision in salida["decisiones"]:
        assert errores_decision(decision) == [], decision
    from orquestador.dominio import Orden

    for linea in salida["ordenes"]:
        assert errores_orden(Orden(linea["operacion"], linea["idempotency_key"], linea["cuerpo"])) == [], linea


@pytest.mark.parametrize("event_id", list(DORADA))
def test_tabla_dorada(salida, event_id):
    etiqueta, esperadas = DORADA[event_id]
    decision = next(d for d in salida["decisiones"] if d["event_id"] == event_id)
    ordenes = [o for o in salida["ordenes"] if o["event_id"] == event_id]
    assert decision["etiqueta"] == etiqueta
    assert [o["operacion"] for o in ordenes] == [op for op, _, _ in esperadas]
    assert decision["ordenes"] == [o["orden_id"] for o in ordenes]
    for orden, (_, clave, campos) in zip(ordenes, esperadas, strict=True):
        if clave:
            assert orden["idempotency_key"] == clave
        for campo, valor in campos.items():
            assert orden["cuerpo"][campo] == valor, (event_id, campo)


def test_ejemplo_resuelto_identico(salida):
    """El evento 02 debe producir exactamente las órdenes del ejemplo resuelto (ids incluidos)."""
    from tests.utiles import RAIZ

    esperadas = [
        json.loads(linea) for linea in (RAIZ / "ejemplo-resuelto/salida/ordenes.jsonl").read_text("utf-8").splitlines()
    ]
    obtenidas = [o for o in salida["ordenes"] if o["event_id"] == "evt_02"]
    assert [o["orden_id"] for o in obtenidas] == [o["orden_id"] for o in esperadas]
    assert obtenidas[1]["cuerpo"] == esperadas[1]["cuerpo"]


def test_mensaje_cancela_los_recordatorios_creados_en_otro_proceso(salida):
    creados = [o for o in salida["ordenes"] if o["operacion"] == "programar_recordatorio"]
    cancelados = [o for o in salida["ordenes"] if o["operacion"] == "cancelar_recordatorio"]
    from orquestador.dominio import Orden
    from orquestador.politica import reminder_id_de

    ids_creados = {reminder_id_de(Orden(o["operacion"], o["idempotency_key"], o["cuerpo"])) for o in creados}
    assert {o["cuerpo"]["reminder_id"] for o in cancelados} == ids_creados


def test_ninguna_orden_duplicada(salida):
    claves = [o["idempotency_key"] for o in salida["ordenes"]]
    assert len(claves) == len(set(claves)) == 29
