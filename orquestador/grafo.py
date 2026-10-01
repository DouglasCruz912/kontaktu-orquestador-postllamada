"""El grafo LangGraph del orquestador: un evento entra, una decisión y sus órdenes salen.

    START → cargar_contexto ─┬─ otra_org ──► no_aplica ─────────────┐
                             ├─ reentrega ─► repetir_decision ──────┤
                             ├─ mensaje ───► cancelar_recordatorios ┤
                             └─ llamada ───► clasificar_senalizacion│
                                   ├─ resuelta ─────────────────────┼─► planificar_ordenes ─► emitir ─► END
                                   └─ conversación ─► clasificar_llm ─► reglas_duras ─┘

Decisiones de diseño (más en docs/decisiones.md):
- Lo determinista va primero: el LLM solo lee conversaciones con una persona.
- Las dependencias (config, repo SQLite, clasificador) entran por `context`, no por el estado: el estado es lo que
  cambia durante el evento; el contexto es lo que se le presta al grafo. Así los tests inyectan un clasificador doble.
- Sin checkpointer: cada evento es un proceso nuevo y la memoria entre eventos es del dominio (persistencia.py).
- Solo `emitir` escribe (una transacción): si cualquier nodo anterior falla, no queda nada a medias (R8).
"""

import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypedDict

import openai
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, RetryPolicy

from orquestador.clasificador import ClasificacionLLM, Clasificador
from orquestador.config import Config
from orquestador.dominio import Clasificacion, ContextoLead, Orden
from orquestador.persistencia import Repositorio
from orquestador.politica import planificar_llamada, planificar_mensaje
from orquestador.reglas import aplicar_reglas
from orquestador.salida import CambiosLead, emitir
from orquestador.senalizacion import clasificar_por_senalizacion

Ruta = Literal["otra_org", "reentrega", "mensaje", "llamada"]


class Estado(TypedDict, total=False):
    evento: dict
    ruta: Ruta
    lead: ContextoLead
    clasificacion: Clasificacion
    llm: ClasificacionLLM | None
    error_llm: str | None
    ordenes: list[Orden]
    registrar_evento: bool
    cambios_lead: CambiosLead | None
    decision: dict
    # Reductor: cada nodo añade sus incidencias sin pisar las de los anteriores.
    avisos: Annotated[list[str], operator.add]


@dataclass(frozen=True)
class Contexto:
    config: Config
    repo: Repositorio
    clasificador: Clasificador
    dir_salida: Path


# --- nodos ---------------------------------------------------------------------------------------------------------


def cargar_contexto(state: Estado, runtime: Runtime[Contexto]) -> dict:
    evento, ctx = state["evento"], runtime.context
    # R6 va primero: de otra organización no se lee ni se escribe nada de nuestro estado.
    if evento["organization_id"] != ctx.config.organization_id:
        return {"ruta": "otra_org"}
    # R5: la idempotency_key identifica el hecho; delivery_attempt no basta (el original pudo perderse).
    previa = ctx.repo.evento_previo(evento["idempotency_key"])
    if previa is not None:
        return {"ruta": "reentrega", "clasificacion": previa}
    ruta: Ruta = "mensaje" if evento["type"] == "message.received" else "llamada"
    return {"ruta": ruta, "lead": ctx.repo.contexto_lead(evento["lead"]["contact_id"])}


def no_aplica(state: Estado) -> dict:
    org = state["evento"]["organization_id"]
    clasif = Clasificacion("no_aplica", f"evento de otra organización ({org}): no se procesa", 1.0, "no_aplica")
    return {"clasificacion": clasif, "ordenes": [], "registrar_evento": False}


def repetir_decision(state: Estado) -> dict:
    previa = state["clasificacion"]
    intento = state["evento"].get("delivery_attempt")
    motivo = f"reentrega (delivery_attempt {intento}) de un hecho ya procesado: {previa.motivo}"
    clasif = Clasificacion(previa.etiqueta, motivo, previa.confianza, "reentrega")
    return {"clasificacion": clasif, "ordenes": [], "registrar_evento": False}


def cancelar_recordatorios(state: Estado) -> dict:
    ordenes = planificar_mensaje(state["evento"], state["lead"])
    motivo = f"el lead respondió por WhatsApp: se cancelan {len(ordenes)} recordatorios pendientes"
    clasif = Clasificacion("no_aplica", motivo, 1.0, "no_aplica")
    return {"clasificacion": clasif, "ordenes": ordenes, "registrar_evento": True}


def clasificar_senalizacion(state: Estado) -> dict:
    clasif = clasificar_por_senalizacion(state["evento"])
    return {"clasificacion": clasif} if clasif else {}


def clasificar_llm(state: Estado, runtime: Runtime[Contexto]) -> dict:
    return {"llm": runtime.context.clasificador.clasificar(state["evento"]), "error_llm": None}


def respaldo_llm(state: Estado, error: NodeError) -> Command:
    """error_handler de clasificar_llm: agotados los reintentos, se sigue sin LLM (acabará en `otro`).

    Hay que devolver Command(goto=...): sin goto, el grafo termina en el manejador.
    """
    detalle = f"{type(error.error).__name__}: {error.error}"
    return Command(
        update={"llm": None, "error_llm": detalle, "avisos": [f"fallo del clasificador LLM ({detalle})"]},
        goto="reglas_duras",
    )


def reglas_duras(state: Estado) -> dict:
    return {"clasificacion": aplicar_reglas(state["evento"], state.get("llm"), state.get("error_llm"))}


def planificar_ordenes(state: Estado, runtime: Runtime[Contexto]) -> dict:
    clasif, llm = state["clasificacion"], state.get("llm")
    ordenes = planificar_llamada(state["evento"], clasif, llm, state["lead"], runtime.context.config)
    rechaza = None
    if clasif.etiqueta == "documentacion_pendiente" or (llm and llm.rechaza_whatsapp):
        rechaza = True
    elif clasif.etiqueta == "documentacion_enviada":
        rechaza = False  # aceptó WhatsApp en esta llamada: manda lo último que dijo
    return {"ordenes": ordenes, "registrar_evento": True, "cambios_lead": CambiosLead(rechaza_whatsapp=rechaza)}


def emitir_salida(state: Estado, runtime: Runtime[Contexto]) -> dict:
    ctx = runtime.context
    decision, avisos = emitir(
        ctx.repo,
        ctx.dir_salida,
        state["evento"],
        state["clasificacion"],
        state.get("ordenes", []),
        registrar_evento=state.get("registrar_evento", False),
        cambios_lead=state.get("cambios_lead"),
    )
    return {"decision": decision, "avisos": avisos}


# --- aristas condicionales -----------------------------------------------------------------------------------------


def por_ruta(state: Estado) -> Ruta:
    return state["ruta"]


def tras_senalizacion(state: Estado) -> Literal["resuelta", "conversacion"]:
    return "resuelta" if state.get("clasificacion") else "conversacion"


# Reintentos solo para fallos transitorios de la API. Un 400 o una salida imparseable no mejoran reintentando.
REINTENTO_LLM = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    retry_on=(openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError),
)


def construir_grafo():
    g = StateGraph(Estado, context_schema=Contexto)
    g.add_node("cargar_contexto", cargar_contexto)
    g.add_node("no_aplica", no_aplica)
    g.add_node("repetir_decision", repetir_decision)
    g.add_node("cancelar_recordatorios", cancelar_recordatorios)
    g.add_node("clasificar_senalizacion", clasificar_senalizacion)
    g.add_node("clasificar_llm", clasificar_llm, retry_policy=REINTENTO_LLM, error_handler=respaldo_llm)
    g.add_node("reglas_duras", reglas_duras)
    g.add_node("planificar_ordenes", planificar_ordenes)
    g.add_node("emitir", emitir_salida)

    g.add_edge(START, "cargar_contexto")
    g.add_conditional_edges(
        "cargar_contexto",
        por_ruta,
        {
            "otra_org": "no_aplica",
            "reentrega": "repetir_decision",
            "mensaje": "cancelar_recordatorios",
            "llamada": "clasificar_senalizacion",
        },
    )
    g.add_conditional_edges(
        "clasificar_senalizacion",
        tras_senalizacion,
        {"resuelta": "planificar_ordenes", "conversacion": "clasificar_llm"},
    )
    g.add_edge("clasificar_llm", "reglas_duras")
    g.add_edge("reglas_duras", "planificar_ordenes")
    for nodo in ("no_aplica", "repetir_decision", "cancelar_recordatorios", "planificar_ordenes"):
        g.add_edge(nodo, "emitir")
    g.add_edge("emitir", END)
    return g.compile()
