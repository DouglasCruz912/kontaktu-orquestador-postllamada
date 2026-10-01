"""Tipos del dominio y catálogos cerrados de casos.md. Ningún valor de estos catálogos se inventa."""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

Etiqueta = Literal[
    "visita_reservada",
    "documentacion_enviada",
    "callback",
    "sin_respuesta",
    "ocupado",
    "buzon",
    "cortada",
    "visita_sin_confirmar",
    "persona_equivocada",
    "no_contactar",
    "rechazada",
    "documentacion_pendiente",
    "descartado",
    "otro",
    "no_aplica",
]

# Estado de cola que lleva cerrar_llamada (tabla de casos.md: «fijado por la etiqueta»).
STATUS_POR_ETIQUETA: dict[str, str] = {
    "visita_reservada": "successful",
    "documentacion_enviada": "completed",
    "documentacion_pendiente": "completed",
    "callback": "callback_requested",
    "sin_respuesta": "no_answer",
    "ocupado": "no_answer",
    "buzon": "no_answer",
    "cortada": "needs_review",
    "visita_sin_confirmar": "needs_review",
    "otro": "needs_review",
    "persona_equivocada": "failed",
    "no_contactar": "dnc",
    "rechazada": "refused",
    "descartado": "skipped",
}

# Etiquetas «sin contacto»: las únicas a las que se aplica el agotamiento de intentos
# (N3, decidido con el usuario: si el lead habló y pidió otra llamada, se le llama).
SIN_CONTACTO = frozenset({"sin_respuesta", "ocupado", "buzon"})
# Llamadas cortadas a efectos de N4.
CORTADAS = frozenset({"cortada", "visita_sin_confirmar"})


@dataclass(frozen=True)
class Clasificacion:
    etiqueta: str
    motivo: str
    confianza: float
    origen: Literal["senalizacion", "llm", "regla", "respaldo", "reentrega", "no_aplica"]


@dataclass(frozen=True)
class Recordatorio:
    reminder_id: str
    contact_id: str
    canal: str
    cuando: str
    cancelar_si: str


@dataclass(frozen=True)
class ContextoLead:
    """Lo que se sabe del lead por eventos anteriores (persistido en SQLite, R4)."""

    intentos_previos: int = 0
    cortadas_previas: int = 0
    baja: bool = False
    rechaza_whatsapp: bool = False
    recordatorios_pendientes: tuple[Recordatorio, ...] = ()


@dataclass(frozen=True)
class Orden:
    """Una petición al CRM. `idempotency_key` identifica el hecho: el mismo evento produce la misma clave."""

    operacion: str
    idempotency_key: str
    cuerpo: dict[str, Any] = field(default_factory=dict)

    @property
    def orden_id(self) -> str:
        return id_estable("ord", self.idempotency_key)


def id_estable(prefijo: str, clave: str) -> str:
    """Id único entre ejecuciones y reproducible: reproduce exactamente los orden_id del ejemplo resuelto."""
    return f"{prefijo}_{hashlib.sha1(clave.encode('utf-8')).hexdigest()[:8]}"


def clave_orden(clave_evento: str, operacion: str, distintivo: str | None = None) -> str:
    return f"{clave_evento}:{operacion}" + (f":{distintivo}" if distintivo else "")
