"""Contrato del clasificador de conversaciones: lo que el LLM devuelve y la interfaz que lo produce.

El grafo depende de esta interfaz, no de OpenAI: en los tests se inyecta un doble por `context`
(los modelos falsos de LangChain no soportan with_structured_output).
"""

from typing import Literal, Protocol

from pydantic import BaseModel

# Solo las etiquetas que exigen leer la conversación. Las de telefonía (ocupado, buzon…) y visita_reservada
# (que sale de que exista la cita) no se le ofrecen al modelo: así no puede devolverlas por error.
EtiquetaConversacion = Literal[
    "callback",
    "cortada",
    "visita_sin_confirmar",
    "persona_equivocada",
    "no_contactar",
    "documentacion_enviada",
    "documentacion_pendiente",
    "descartado",
    "otro",
]


# Salida estructurada. Con json_schema strict todo campo es obligatorio; lo opcional es `X | None`.
# Sin docstring a propósito: pydantic lo enviaría a OpenAI como `description` del esquema (sería parte del prompt,
# y los prompts viven en prompts/).
class ClasificacionLLM(BaseModel):
    etiqueta: EtiquetaConversacion
    motivo: str
    confianza: float
    pide_baja: bool
    rechaza_whatsapp: bool
    callback_local: str | None
    nota_contexto: str | None
    email: str | None


class Clasificador(Protocol):
    def clasificar(self, evento: dict) -> ClasificacionLLM: ...
