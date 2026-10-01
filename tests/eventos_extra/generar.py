"""Genera eventos extra para lo que el lote de ejemplo no cubre: casos ⚠ de casos.md, pares que se parecen,
reglas N1–N4 con estado entre eventos y fechas en sábado o domingo.

Cada evento se deriva de uno real de eventos/ (no se modifican: se copian). `esperado.json` guarda, por evento,
la etiqueta y las órdenes esperadas y la respuesta que daría el LLM (la usa el doble en los tests).

    uv run python tests/eventos_extra/generar.py
"""

import copy
import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent
EVENTOS = AQUI.parent.parent / "eventos"
BASE_SIN_RESPUESTA = json.loads((EVENTOS / "01-call-ended-nuria.json").read_text(encoding="utf-8"))
BASE_CONVERSACION = json.loads((EVENTOS / "03-call-ended-elena.json").read_text(encoding="utf-8"))
BASE_MENSAJE = json.loads((EVENTOS / "14-message-received-marcos.json").read_text(encoding="utf-8"))


def evento(n: int, lead: int, cuando: str, base: dict, telefonia: dict | None = None, turnos=None, **extra) -> dict:
    e = copy.deepcopy(base)
    clave = f"x-{n:02d}"
    e.update(event_id=f"evx_{n:02d}", occurred_at=cuando, idempotency_key=clave)
    e["campaign"]["entry_id"] = f"ce_x{lead}"
    e["lead"].update(contact_id=f"c_x{lead}", phone=f"+3460000{lead:04d}", full_name=f"Lead {lead}")
    if "telephony" in e:
        e["telephony"].update(call_id=clave, ended_at=cuando, **(telefonia or {}))
    if turnos is not None:
        e["transcript"] = [
            {"role": "agent" if i % 2 == 0 else "user", "message": m, "time_in_call_secs": 3 + 6 * i}
            for i, m in enumerate(turnos)
        ]
    e.update(extra)
    return e


SALUDO = "Hola, muy buenas. Soy Marta, la asistente virtual de Ribera Inmobiliaria. Te llamo por tu consulta."
HUMANO = {
    "sip_status_code": 200,
    "sip_status": "OK",
    "disconnect_reason": "CLIENT_INITIATED",
    "hung_up_by": "callee",
    "answered_at": "2026-09-15T10:00:00+02:00",
    "duration_seconds": 40,
    "amd": {"result": "human", "greeting_transcript": "¿Sí?", "detected_at_secs": 1.5, "source": "livekit_amd"},
}


def llm(etiqueta: str, **campos) -> dict:
    return {
        "etiqueta": etiqueta,
        "motivo": etiqueta,
        "confianza": 0.9,
        "pide_baja": False,
        "rechaza_whatsapp": False,
        "callback_local": None,
        "nota_contexto": None,
        "email": None,
        **campos,
    }


CASOS = [
    # Caso 11 ⚠ rechazada: 603 antes de descolgar → respaldo, nunca otra llamada.
    (evento(1, 401, "2026-09-17T11:20:00+02:00", BASE_SIN_RESPUESTA,
            {"sip_status_code": 603, "sip_status": "Decline D21", "disconnect_reason": "USER_REJECTED"}),
     "rechazada", ["cerrar_llamada", "enviar_plantilla_whatsapp"], None, {}),
    # Caso 12 ⚠ callback fuera de ventana: «esta noche a las diez» → miércoles 10:00 + aviso_cambio_hora.
    (evento(2, 402, "2026-09-15T13:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Uf, ahora mismo no puedo. ¿Me llamas esta noche a las diez?", "Claro, lo anoto."]),
     "callback", ["cerrar_llamada", "programar_llamada", "enviar_plantilla_whatsapp"],
     llm("callback", callback_local="2026-09-15T22:00"), {1: {"no_antes_de": "2026-09-16T10:00:00+02:00"}}),
    # Caso 15 ⚠ descartado: ya alquiló y cuelga seco → solo cerrar (no es cortada ni baja).
    (evento(3, 403, "2026-09-17T12:05:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "No, gracias, ya alquilé un piso la semana pasada. Ya no busco."]),
     "descartado", ["cerrar_llamada"], llm("descartado"), {}),
    # otro: contestó un IVR.
    (evento(4, 404, "2026-09-16T11:00:00+02:00", BASE_SIN_RESPUESTA,
            {**HUMANO, "amd": {"result": "machine-ivr", "greeting_transcript": "Pulse uno", "source": "livekit_amd"}}),
     "otro", ["cerrar_llamada", "crear_tarea"], None, {1: {"tipo": "revisar_llamada"}}),
    # otro: fallo de trunk 5xx.
    (evento(5, 405, "2026-09-16T11:05:00+02:00", BASE_SIN_RESPUESTA,
            {"sip_status_code": 503, "sip_status": "Service Unavailable", "disconnect_reason": "SIP_TRUNK_FAILURE"}),
     "otro", ["cerrar_llamada", "crear_tarea"], None, {1: {"tipo": "revisar_llamada"}}),
    # N4: dos cortadas con el mismo lead → la segunda añade revisar_llamada.
    (evento(6, 406, "2026-09-15T12:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Sí, sigo buscando. En Pozuelo, de compra.", "Perfecto. ¿Y qué presupuesto…",
             "Pues unos tresci…"]),
     "cortada", ["cerrar_llamada", "programar_llamada"], llm("cortada"),
     {1: {"no_antes_de": "2026-09-15T12:30:00+02:00"}}),
    (evento(7, 406, "2026-09-15T15:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Sí, perdona, antes se cortó. Te decía que el presupuesto es de…"]),
     "cortada", ["cerrar_llamada", "programar_llamada", "crear_tarea"], llm("cortada"),
     {2: {"tipo": "revisar_llamada"}}),
    # N3: tercer sin_respuesta del mismo lead → canal de respaldo en lugar de otra llamada.
    (evento(8, 407, "2026-09-15T10:00:00+02:00", BASE_SIN_RESPUESTA), "sin_respuesta",
     ["cerrar_llamada", "programar_llamada"], None, {}),
    (evento(9, 407, "2026-09-15T12:30:00+02:00", BASE_SIN_RESPUESTA), "sin_respuesta",
     ["cerrar_llamada", "programar_llamada"], None, {}),
    (evento(10, 407, "2026-09-15T15:00:00+02:00", BASE_SIN_RESPUESTA), "sin_respuesta",
     ["cerrar_llamada", "enviar_plantilla_whatsapp"], None, {1: {"plantilla": "primer_toque_respaldo"}}),
    # Ocupado el sábado a las 13:45: ningún punto de 30–90 min cae en ventana → lunes 10:00.
    (evento(11, 408, "2026-09-19T13:45:00+02:00", BASE_SIN_RESPUESTA,
            {"sip_status_code": 486, "sip_status": "Busy Here", "disconnect_reason": "USER_REJECTED"}),
     "ocupado", ["cerrar_llamada", "programar_llamada"], None, {1: {"no_antes_de": "2026-09-21T10:00:00+02:00"}}),
    # 10 contra 15: «ya encontré piso y no me llaméis más» es baja, no descarte.
    (evento(12, 409, "2026-09-16T12:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Ya encontré piso, así que no me llaméis más, gracias."]),
     "no_contactar", ["cerrar_llamada", "marcar_no_contactar"], llm("no_contactar", pide_baja=True), {}),
    # Un tercero da la hora: es callback («a las ocho» → 20:00, dentro de la ventana inclusive).
    (evento(13, 410, "2026-09-15T17:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "No, Javier no está, está trabajando. Llámale a las ocho, que ya habrá vuelto."]),
     "callback", ["cerrar_llamada", "programar_llamada"], llm("callback", callback_local="2026-09-15T20:00"),
     {1: {"no_antes_de": "2026-09-15T20:00:00+02:00"}}),
    # 3 contra 7: «ahora no puedo» sin pedir otra llamada y cuelga → cortada.
    (evento(14, 411, "2026-09-16T10:30:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Ahora no puedo, estoy conduciendo."]),
     "cortada", ["cerrar_llamada", "programar_llamada"], llm("cortada"), {}),
    # N1 + N3: rechazó WhatsApp y luego se agotan los intentos → tarea llamar_a_mano, ningún WhatsApp.
    (evento(15, 412, "2026-09-15T10:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Quiero la nota simple.", "Te la mando por WhatsApp.", "No, por WhatsApp no, al correo: a@b.es"]),
     "documentacion_pendiente", ["cerrar_llamada", "crear_tarea"],
     llm("documentacion_pendiente", rechaza_whatsapp=True, email="a@b.es"), {}),
    (evento(16, 412, "2026-09-15T13:00:00+02:00", BASE_SIN_RESPUESTA), "sin_respuesta",
     ["cerrar_llamada", "programar_llamada"], None, {}),
    (evento(17, 412, "2026-09-15T16:00:00+02:00", BASE_SIN_RESPUESTA), "sin_respuesta",
     ["cerrar_llamada", "crear_tarea"], None, {1: {"tipo": "llamar_a_mano"}}),
    # Baja con recordatorios pendientes → se cancelan (decidido con el usuario).
    (evento(18, 413, "2026-09-15T10:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Mándame la documentación por WhatsApp.", "Hecho, te la acabo de enviar.", "Gracias."]),
     "documentacion_enviada", ["cerrar_llamada", "programar_recordatorio", "programar_recordatorio"],
     llm("documentacion_enviada"), {}),
    (evento(19, 413, "2026-09-16T11:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Mira, dadme de baja, no quiero saber nada más."]),
     "no_contactar", ["cerrar_llamada", "marcar_no_contactar", "cancelar_recordatorio", "cancelar_recordatorio"],
     llm("no_contactar", pide_baja=True), {}),
    # WhatsApp de un lead sin recordatorios pendientes → no_aplica, ninguna orden.
    (evento(20, 414, "2026-09-16T12:00:00+02:00", BASE_MENSAJE, message={"channel": "whatsapp", "text": "Hola"}),
     "no_aplica", [], None, {}),
    # Callback para el domingo (sin ventana) → lunes 10:00 + aviso.
    (evento(21, 415, "2026-09-18T11:00:00+02:00", BASE_CONVERSACION, HUMANO,
            [SALUDO, "Esta semana imposible. Llámame el domingo a las doce."]),
     "callback", ["cerrar_llamada", "programar_llamada", "enviar_plantilla_whatsapp"],
     llm("callback", callback_local="2026-09-20T12:00"), {1: {"no_antes_de": "2026-09-21T10:00:00+02:00"}}),
]  # fmt: skip


def main() -> None:
    esperado = {}
    nombres = []
    for e, etiqueta, operaciones, respuesta_llm, campos in CASOS:
        nombre = f"{e['event_id']}.json"
        (AQUI / nombre).write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        nombres.append(nombre)
        esperado[e["event_id"]] = {"etiqueta": etiqueta, "operaciones": operaciones, "llm": respuesta_llm,
                                   "campos": {str(k): v for k, v in campos.items()}}  # fmt: skip
    (AQUI / "orden.txt").write_text("\n".join(nombres) + "\n", encoding="utf-8")
    (AQUI / "esperado.json").write_text(json.dumps(esperado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(nombres)} eventos generados")


if __name__ == "__main__":
    main()
