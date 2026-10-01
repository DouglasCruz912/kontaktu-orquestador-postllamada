"""Reglas duras que se aplican sobre la salida del LLM. Son deterministas y mandan sobre el modelo.

1. Una baja manda sobre cualquier otra etiqueta, la diga cuando la diga (casos.md, «10 contra todo»).
   Se detecta si el LLM la marca O si una expresión acotada la encuentra en lo que dijo el lead:
   es el fallo más grave (N2), así que se pone una red de seguridad que no depende del modelo.
2. Si el agente creó la cita (`agent_outcome.appointment`), la etiqueta es visita_reservada (caso 1).
3. Sin salida del LLM (error, parseo fallido), la etiqueta es `otro`: un humano revisa (N4).
"""

import re

from orquestador.clasificador import ClasificacionLLM
from orquestador.dominio import Clasificacion

# Solo frases inequívocas. «No me llames más tarde» NO es una baja: de ahí el lookahead.
PATRONES_BAJA = [
    r"\bno (?:me|nos) (?:llam|contact|molest|escrib)\w*+ (?:m[aá]s|nunca)\b(?! tarde)",
    r"\bno (?:me |nos )?(?:vuelv|volv)\w* a (?:llamar|contactar|escribir)",
    # «no quiero que me llaméis» solo cuenta si la frase termina ahí (o con «más»/«nunca»):
    # «no quiero que me llaméis ahora» no es una baja.
    # \w*+ es posesivo: no retrocede para esquivar la condición.
    r"\bno quiero que (?:me|nos) (?:llam|contact|escrib)\w*+(?:\s+(?:m[aá]s|nunca))?\s*(?:[.,;!?]|$)",
    r"\b(?:dadme|d[ée]me|dame|denme|darme|darnos|dadnos) de baja\b",
    r"\b(?:quiero|quisiera) (?:darme|que me deis|que me des) de baja\b",
    r"\b(?:borr|quit|elimin)\w* (?:mi n[uú]mero|mis datos|de (?:la|vuestra|tu|su) lista)",
]
_REGEX_BAJA = re.compile("|".join(PATRONES_BAJA), re.IGNORECASE)


def frase_de_baja(evento: dict) -> str | None:
    """Devuelve la frase del lead que pide la baja, o None. Solo se miran los turnos del lead."""
    for turno in evento.get("transcript") or []:
        if turno.get("role") == "user" and _REGEX_BAJA.search(turno.get("message", "")):
            return turno["message"]
    return None


def aplicar_reglas(evento: dict, llm: ClasificacionLLM | None, error_llm: str | None) -> Clasificacion:
    frase = frase_de_baja(evento)
    if (llm and (llm.pide_baja or llm.etiqueta == "no_contactar")) or frase:
        motivo = llm.motivo if llm and llm.etiqueta == "no_contactar" else f"el lead pide la baja: «{frase}»"
        confianza = max(llm.confianza if llm else 0.0, 0.9 if frase else 0.0)
        return Clasificacion("no_contactar", motivo, _acotar(confianza), "regla" if frase else "llm")

    cita = (evento.get("agent_outcome") or {}).get("appointment")
    if cita:
        motivo = f"el agente creó la cita {cita.get('appointment_id')} para {cita.get('start_time')}"
        return Clasificacion("visita_reservada", motivo, 0.97, "regla")

    if llm is None:
        return Clasificacion("otro", f"no se pudo clasificar la conversación ({error_llm})", 0.2, "respaldo")
    return Clasificacion(llm.etiqueta, llm.motivo, _acotar(llm.confianza), "llm")


def _acotar(valor: float) -> float:
    return round(min(1.0, max(0.0, float(valor))), 2)
