"""Clasificación determinista a partir de la telefonía (casos 4, 5, 6, 11, 13 y `otro` técnico).

Lo que la señalización resuelve sola no pasa por el LLM: es más barato, más rápido y no se equivoca.
Devuelve None cuando hubo una conversación con una persona y hay que leer la transcripción.
"""

from orquestador.dominio import Clasificacion

BUZON = frozenset({"machine-vm", "machine-unavailable"})


def clasificar_por_senalizacion(evento: dict) -> Clasificacion | None:
    tel = evento.get("telephony") or {}
    sip = tel.get("sip_status_code")
    texto_sip = f"{sip} {tel.get('sip_status', '')}".strip()
    amd = tel.get("amd") or {}

    # LiveKit expone el 486 como USER_REJECTED, pero no es un rechazo del lead: manda el código SIP.
    if sip == 486:
        return Clasificacion("ocupado", f"{texto_sip}: la línea comunica", 0.97, "senalizacion")
    if sip == 603:
        return Clasificacion("rechazada", f"{texto_sip}: rechazo activo antes de descolgar", 0.95, "senalizacion")
    if sip in (408, 480):
        return Clasificacion("sin_respuesta", f"{texto_sip}: nadie descolgó", 0.97, "senalizacion")
    if isinstance(sip, int) and 500 <= sip <= 599:
        return Clasificacion("otro", f"{texto_sip}: fallo de trunk antes de conectar", 0.9, "senalizacion")
    if sip != 200:
        return Clasificacion("otro", f"código SIP no contemplado ({texto_sip})", 0.6, "senalizacion")

    # 200 OK: un buzón también contesta con 200; la única señal de máquina está en amd.
    resultado, fuente = amd.get("result"), amd.get("source")
    if resultado in BUZON:
        # Caso 13: con heuristic_regex la detección es menos fiable, pero la etiqueta es la misma;
        # lo que decide la acción es el recuento de intentos, no la certeza.
        confianza = 0.75 if fuente == "heuristic_regex" else 0.93
        return Clasificacion("buzon", f"contestador detectado ({resultado}, {fuente})", confianza, "senalizacion")
    if resultado == "machine-ivr":
        return Clasificacion("otro", "contestó un IVR, no una persona", 0.85, "senalizacion")

    # human, uncertain (se trata como persona), not_run o ausente: hay que leer lo que se dijo.
    turnos_lead = [t for t in evento.get("transcript") or [] if t.get("role") == "user" and t.get("message")]
    if not turnos_lead:
        return Clasificacion("otro", "descolgaron pero el lead no llegó a hablar", 0.6, "senalizacion")
    return None
