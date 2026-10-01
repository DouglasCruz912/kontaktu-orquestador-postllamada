"""Clasificador de conversaciones con un modelo de OpenAI y salida estructurada.

- Los prompts viven en prompts/ (versionados), nunca incrustados aquí.
- `json_schema` strict: el modelo solo puede devolver el esquema de ClasificacionLLM (etiquetas del enum cerrado).
- `include_raw=True`: un fallo de parseo llega como `parsed=None` en vez de excepción; aquí se convierte en
  ClasificacionInvalida, que el grafo envía al error_handler del nodo (→ etiqueta `otro`).
- `max_retries=0` en el cliente: los reintentos los gestiona la RetryPolicy del nodo de LangGraph, en un solo sitio.
"""

import json
import os
from datetime import datetime
from string import Template

from langchain_openai import ChatOpenAI

from orquestador.calendario import DIAS_SEMANA, TZ
from orquestador.clasificador import ClasificacionLLM
from orquestador.config import RAIZ

PROMPTS = RAIZ / "prompts"
MODELO_POR_DEFECTO = "gpt-6-luna"
RAZONAMIENTO_POR_DEFECTO = "none"


class ClasificacionInvalida(ValueError):
    """El modelo respondió, pero no con el esquema esperado."""


class ClasificadorOpenAI:
    def __init__(self, modelo: str, razonamiento: str | None = None):
        self.modelo = modelo
        self.razonamiento = razonamiento
        self._sistema = (PROMPTS / "clasificador_sistema.md").read_text(encoding="utf-8")
        self._usuario = Template((PROMPTS / "clasificador_usuario.md").read_text(encoding="utf-8"))
        self._cadena = None  # perezosa: sin conversación que leer, no hace falta clave ni cliente

    @classmethod
    def desde_entorno(cls) -> "ClasificadorOpenAI":
        modelo = os.environ.get("MODELO") or MODELO_POR_DEFECTO
        # reasoning_effort="none" solo con el modelo por defecto (verificado: misma respuesta, ~2x más rápido).
        # Con otro MODELO no se envía, porque no todos los modelos aceptan el parámetro.
        por_defecto = RAZONAMIENTO_POR_DEFECTO if modelo == MODELO_POR_DEFECTO else None
        return cls(modelo=modelo, razonamiento=os.environ.get("RAZONAMIENTO") or por_defecto)

    def _llm(self):
        if self._cadena is None:
            extra = {"reasoning_effort": self.razonamiento} if self.razonamiento else {}
            modelo = ChatOpenAI(model=self.modelo, timeout=60, max_retries=0, **extra)
            self._cadena = modelo.with_structured_output(
                ClasificacionLLM, method="json_schema", strict=True, include_raw=True
            )
        return self._cadena

    def mensajes(self, evento: dict) -> list[tuple[str, str]]:
        return [("system", self._sistema), ("human", self._usuario.substitute(datos_usuario(evento)))]

    def clasificar(self, evento: dict) -> ClasificacionLLM:
        respuesta = self._llm().invoke(self.mensajes(evento))
        if respuesta.get("parsed") is None:
            raise ClasificacionInvalida(str(respuesta.get("parsing_error") or "respuesta sin parsear"))
        return respuesta["parsed"]


def datos_usuario(evento: dict) -> dict[str, str]:
    tel = evento.get("telephony") or {}
    amd = tel.get("amd") or {}
    lead = evento.get("lead") or {}
    salida = evento.get("agent_outcome") or {}
    ref = datetime.fromisoformat(evento["occurred_at"]).astimezone(TZ)
    turnos = evento.get("transcript") or []
    transcripcion = "\n".join(
        f"[{t.get('time_in_call_secs', '?')}s] {_quien(t)}: {t.get('message', '')}" for t in turnos
    )
    return {
        "referencia": ref.strftime("%Y-%m-%dT%H:%M"),
        "dia_semana": DIAS_SEMANA[ref.weekday()].replace("miercoles", "miércoles").replace("sabado", "sábado"),
        "nombre": lead.get("full_name") or "desconocido",
        "inmueble": f"{lead.get('property_ref') or '?'} ({lead.get('property_address') or 'dirección no disponible'})",
        "idioma": lead.get("language") or "es",
        "sip": f"{tel.get('sip_status_code')} {tel.get('sip_status') or ''}".strip(),
        "desconexion": str(tel.get("disconnect_reason")),
        "colgo": str(tel.get("hung_up_by") or "no se sabe"),
        "duracion": str(tel.get("duration_seconds")),
        "amd": f"{amd.get('result')} ({amd.get('source')})",
        "cita": json.dumps(salida.get("appointment"), ensure_ascii=False) if salida.get("appointment") else "ninguna",
        "notas": json.dumps(salida.get("slots_snapshot") or {}, ensure_ascii=False),
        "transcripcion": transcripcion or "(vacía)",
    }


def _quien(turno: dict) -> str:
    return "agente" if turno.get("role") == "agent" else "lead"
