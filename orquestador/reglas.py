"""Reglas duras que se aplican sobre la salida del LLM. Son deterministas y mandan sobre el modelo.

1. Una baja manda sobre cualquier otra etiqueta, la diga cuando la diga (casos.md, «10 contra todo»).
   La detecta el LLM (`pide_baja`). Si el LLM no responde, una expresión acotada sobre lo que dijo el lead
   hace de red de seguridad: N2 se cumple aunque OpenAI caiga.
2. Si el agente creó la cita (`agent_outcome.appointment`), la etiqueta es visita_reservada (caso 1).
3. Sin salida del LLM (error, parseo fallido), la etiqueta es `otro`: un humano revisa (N4).
"""

import re

from orquestador.clasificador import ClasificacionLLM
from orquestador.dominio import Clasificacion

# Solo frases inequívocas, en imperativo o subjuntivo («no me llaméis más»), nunca en pasado ni negadas.
# «No me llames más tarde / hoy / por la mañana» NO es una baja: de ahí los lookahead.
_LIMITE = r"(?!\s+(?:tarde|hoy|ahora|luego|por\s+(?:la|el|las)|esta|este|en\s+este|a\s+estas|de\s+momento))"
PATRONES_BAJA = [
    r"\bno (?:me|nos) (?:llam|contact|molest)(?:[ée]is|[ée]s|[ée]n|e)\b (?:m[aá]s|nunca)\b" + _LIMITE,
    r"\bno (?:me |nos )?(?:vuelv|volv)(?:[áa]is|as|an|a) a (?:llamar|contactar)\b" + _LIMITE,
    # «no quiero que me llaméis» solo cuenta si la frase termina ahí (o con «más»/«nunca»).
    r"\bno quiero que (?:me|nos) (?:llam|contact)(?:[ée]is|[ée]s|[ée]n|e)\b(?:\s+(?:m[aá]s|nunca))?\s*(?:[.,;!?]|$)",
    r"(?<!no quiero )(?<!no quiero que me )\b(?:dadme|d[ée]me|dame|denme|darme|dadnos) de baja\b",
    r"\b(?:b[oó]rr|quit|elimin)(?:adme|ame|enme|adnos|ar) (?:mi n[uú]mero|mis datos|de (?:la|vuestra|tu|su) lista)",
]
_REGEX_BAJA = re.compile("|".join(PATRONES_BAJA), re.IGNORECASE)


def es_baja(texto: str) -> bool:
    return bool(_REGEX_BAJA.search(texto or ""))


def frase_de_baja(evento: dict) -> str | None:
    """Devuelve la frase del lead que pide la baja, o None. Solo se miran los turnos del lead."""
    for turno in evento.get("transcript") or []:
        if turno.get("role") == "user" and es_baja(turno.get("message", "")):
            return turno["message"]
    return None


def aplicar_reglas(evento: dict, llm: ClasificacionLLM | None, error_llm: str | None) -> Clasificacion:
    if llm and (llm.pide_baja or llm.etiqueta == "no_contactar"):
        motivo = llm.motivo if llm.etiqueta == "no_contactar" else f"el lead pide la baja ({llm.motivo})"
        return Clasificacion("no_contactar", motivo, _acotar(llm.confianza), "llm")
    # Red de seguridad SOLO si el LLM no respondió (decidido con el usuario tras la revisión final):
    # aplicada siempre, pisaba al modelo con falsos positivos («no me llaméis más hoy», un número equivocado…).
    frase = frase_de_baja(evento) if llm is None else None
    if frase:
        return Clasificacion("no_contactar", f"el lead pide la baja: «{frase}»", 0.85, "regla")

    cita = (evento.get("agent_outcome") or {}).get("appointment")
    if cita:
        motivo = f"el agente creó la cita {cita.get('appointment_id')} para {cita.get('start_time')}"
        return Clasificacion("visita_reservada", motivo, 0.97, "regla")

    if llm is None:
        return Clasificacion("otro", f"no se pudo clasificar la conversación ({error_llm})", 0.2, "respaldo")
    return Clasificacion(llm.etiqueta, llm.motivo, _acotar(llm.confianza), "llm")


def _acotar(valor: float) -> float:
    return round(min(1.0, max(0.0, float(valor))), 2)
