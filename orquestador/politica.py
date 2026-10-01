"""Etiqueta → órdenes contra el CRM. Determinista: el LLM nunca decide órdenes ni calcula fechas.

Orden de aplicación (cada paso documentado en docs/decisiones.md):
1. cerrar_llamada, siempre la primera en un call.ended procesado por primera vez.
2. Las órdenes propias de la etiqueta (tabla de casos.md).
3. N3: agotados los intentos, las etiquetas sin contacto pasan al canal de respaldo.
4. N1: ningún WhatsApp a quien lo rechazó; si era el respaldo, se crea una tarea llamar_a_mano.
5. N4: segunda llamada cortada con el mismo lead → revisar_llamada.
6. N2: un lead que ya estaba de baja solo recibe órdenes internas.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from orquestador.calendario import a_madrid
from orquestador.clasificador import ClasificacionLLM
from orquestador.config import Config
from orquestador.dominio import (
    CORTADAS,
    SIN_CONTACTO,
    STATUS_POR_ETIQUETA,
    Clasificacion,
    ContextoLead,
    Orden,
    clave_orden,
    id_estable,
)

# Lo único que se permite con un lead que ya estaba de baja (N2): nada de esto le contacta.
PERMITIDAS_CON_BAJA = {"cerrar_llamada", "marcar_no_contactar", "cancelar_recordatorio"}
TAREAS_INTERNAS = {"revisar_llamada", "verificar_telefono"}


@dataclass
class _Borrador:
    """Orden aún sin clave: la clave se fija al final, cuando se sabe si la operación se repite."""

    operacion: str
    cuerpo: dict[str, Any]
    distintivo: str | None = None
    distintivo_obligatorio: bool = False
    etiquetas: set[str] = field(default_factory=set)  # marcas internas: "respaldo", "aviso"


def instante(texto: str) -> datetime:
    return a_madrid(texto)


def planificar_llamada(
    evento: dict,
    clasif: Clasificacion,
    llm: ClasificacionLLM | None,
    lead: ContextoLead,
    cfg: Config,
) -> list[Orden]:
    cal = cfg.calendario
    ref = instante(evento["occurred_at"])
    datos = _Datos(evento, cfg)
    etiqueta = clasif.etiqueta
    intento_actual = lead.intentos_previos + 1

    borradores = [_cerrar(datos, clasif)]

    if etiqueta == "visita_reservada":
        cita = evento["agent_outcome"]["appointment"]
        visita = _parsear_hora_local(cita.get("start_time"))
        if visita is not None:
            vence = max(cal.mas_horas(visita, -cfg.confirmar_visita_margen_horas), ref)
        else:  # el esquema permite una cita sin start_time: la tarea vence al plazo por defecto
            vence = cal.mas_dias(ref, cfg.vencimiento_por_defecto_dias)
        direccion = datos.lead.get("property_address") or "dirección no disponible en el CRM"
        cuando_visita = visita.isoformat() if visita else "(hora no disponible)"
        borradores.append(
            datos.tarea(
                "confirmar_visita_direccion",
                f"Confirmar la dirección de la visita de {datos.nombre}",
                f"Visita {cuando_visita} ({cita.get('appointment_id')}) al inmueble "
                f"{datos.lead.get('property_ref')}: {direccion}. Confirmar la dirección exacta al lead.",
                vence,
            )
        )
    elif etiqueta == "documentacion_enviada":
        borradores += [
            datos.recordatorio(
                "lead",
                {"canal": "whatsapp_lead", "plantilla": "recordatorio_documentacion"},
                cal.mas_horas(ref, cfg.documentacion_lead_horas),
            ),
            datos.recordatorio(
                "comercial",
                {"canal": "tarea_comercial", "tipo_tarea": "llamar_a_mano"},
                cal.mas_dias_habiles(ref, cfg.seguimiento_comercial_dias_habiles),
            ),
        ]
    elif etiqueta == "callback":
        borradores += _callback(datos, llm, ref, cfg)
    elif etiqueta in ("sin_respuesta", "buzon"):
        cuando = cal.primer_valido_desde(cal.mas_horas(ref, cfg.separacion_minima_horas))
        motivo = "sin respuesta" if etiqueta == "sin_respuesta" else "saltó el buzón de voz"
        nota = "no se llegó a hablar con el lead"
        borradores.append(datos.llamada(cuando, f"{motivo}, reintento tras la separación mínima", nota))
    elif etiqueta == "ocupado":
        cuando = cal.en_rango_preferente(
            cal.mas_minutos(ref, (cfg.ocupado_minutos_min + cfg.ocupado_minutos_max) / 2),
            cal.mas_minutos(ref, cfg.ocupado_minutos_min),
            cal.mas_minutos(ref, cfg.ocupado_minutos_max),
        )
        borradores.append(
            datos.llamada(cuando, "línea comunicando, reintento corto", "no se llegó a hablar con el lead")
        )
    elif etiqueta in CORTADAS:
        # Plazo específico de cortada (gana al general): lo antes posible dentro de la ventana.
        cuando = cal.primer_valido_desde(cal.mas_minutos(ref, cfg.cortada_minutos_min))
        motivo = (
            "visita acordada de palabra pero no reservada: volver a llamar (no se reserva, N5)"
            if etiqueta == "visita_sin_confirmar"
            else "la llamada se cortó a mitad de la cualificación"
        )
        borradores.append(datos.llamada(cuando, motivo, _nota_contexto(evento, llm)))
    elif etiqueta == "persona_equivocada":
        borradores.append(
            datos.tarea(
                "verificar_telefono",
                f"Verificar el teléfono de {datos.nombre}",
                f"Contestó otra persona: {clasif.motivo}",
                cal.mas_dias(ref, cfg.vencimiento_por_defecto_dias),
            )
        )
    elif etiqueta == "no_contactar":
        borradores.append(
            _Borrador(
                "marcar_no_contactar",
                {
                    "telefono": datos.telefono,
                    "contact_id": datos.contact_id,
                    "canal": "todos",
                    "motivo": clasif.motivo,
                    "origen": f"llamada {datos.call_id}",
                },
            )
        )
        # Decidido con el usuario: dejar vivos los recordatorios haría que le llegara un WhatsApp tras la baja.
        borradores += _cancelaciones(lead, ref, "el lead pidió la baja", solo_lead_responde=False)
    elif etiqueta == "rechazada":
        borradores += _respaldo_si_no_enviado(datos, lead)
    elif etiqueta == "documentacion_pendiente":
        email = llm.email if llm and llm.email else "pedir el email al lead"
        borradores.append(
            datos.tarea(
                "enviar_documentacion_email",
                f"Enviar la documentación por email a {datos.nombre}",
                f"Inmueble {datos.lead.get('property_ref')}. Email: {email}. Rechazó WhatsApp: no usar ese canal.",
                cal.mas_dias(ref, cfg.vencimiento_por_defecto_dias),
            )
        )
    elif etiqueta == "otro":
        borradores.append(datos.revisar(clasif.motivo, ref))
    # descartado: nada más que cerrar la llamada.

    # N3 · intentos agotados (solo etiquetas sin contacto): la llamada se sustituye por el canal de respaldo.
    if etiqueta in SIN_CONTACTO and intento_actual >= cfg.max_intentos:
        borradores = [b for b in borradores if b.operacion != "programar_llamada"]
        borradores += _respaldo_si_no_enviado(datos, lead)

    # N1 · WhatsApp rechazado. Si en esta llamada aceptó WhatsApp (documentacion_enviada), manda lo último que dijo.
    rechaza_ahora = etiqueta == "documentacion_pendiente" or bool(llm and llm.rechaza_whatsapp)
    whatsapp_bloqueado = (lead.rechaza_whatsapp and etiqueta != "documentacion_enviada") or rechaza_ahora
    if whatsapp_bloqueado:
        borradores = _sin_whatsapp(borradores, datos, ref)
    if rechaza_ahora:
        # Un recordatorio por WhatsApp programado antes del rechazo saldría igual: se cancela (N1).
        borradores += _cancelaciones(
            lead, ref, "el lead rechazó WhatsApp", solo_lead_responde=False, canal="whatsapp_lead"
        )

    # N4 · segunda llamada cortada con el mismo lead (cortada o visita_sin_confirmar).
    if etiqueta in CORTADAS and lead.cortadas_previas >= 1:
        borradores.append(
            datos.revisar(f"segunda llamada cortada con el mismo lead ({lead.cortadas_previas + 1}ª)", ref)
        )

    # N2 · el lead ya estaba de baja: solo órdenes internas.
    if lead.baja:
        borradores = [b for b in borradores if _permitida_con_baja(b)]

    return _asignar_claves(evento["idempotency_key"], borradores)


def planificar_mensaje(evento: dict, lead: ContextoLead, frase_baja: str | None = None) -> list[Orden]:
    """R7: el lead responde por WhatsApp → se cancela cada recordatorio pendiente que dependía de eso.

    Si en el mensaje pide la baja (decidido con el usuario): marcar_no_contactar y se cancelan TODOS sus
    recordatorios; la baja queda persistida y N2 bloquea lo saliente en eventos posteriores.
    """
    ref = instante(evento["occurred_at"])
    if frase_baja is None:
        borradores = _cancelaciones(lead, ref, "el lead respondió por WhatsApp", solo_lead_responde=True)
        return _asignar_claves(evento["idempotency_key"], borradores)
    marcar = _Borrador(
        "marcar_no_contactar",
        {
            "telefono": evento["lead"]["phone"],
            "contact_id": evento["lead"]["contact_id"],
            "canal": "todos",
            "motivo": f"pidió la baja por WhatsApp: «{frase_baja}»",
            "origen": f"whatsapp {evento['event_id']}",
        },
    )
    borradores = [marcar, *_cancelaciones(lead, ref, "el lead pidió la baja", solo_lead_responde=False)]
    return _asignar_claves(evento["idempotency_key"], borradores)


# ---------------------------------------------------------------------------------------------------------------


class _Datos:
    """Campos del evento que repiten casi todas las órdenes."""

    def __init__(self, evento: dict, cfg: Config):
        self.evento = evento
        self.cfg = cfg
        self.lead = evento["lead"]
        self.contact_id = self.lead["contact_id"]
        self.telefono = self.lead["phone"]
        self.nombre = self.lead.get("full_name") or "el lead"
        self.entry_id = evento["campaign"]["entry_id"]
        self.call_id = (evento.get("telephony") or {}).get("call_id")

    def llamada(self, cuando: datetime, motivo: str, nota: str) -> _Borrador:
        cuerpo = {
            "entry_id": self.entry_id,
            "telefono": self.telefono,
            "no_antes_de": cuando.isoformat(),
            "motivo": motivo,
            "nota_contexto": nota,
        }
        return _Borrador("programar_llamada", cuerpo)

    def tarea(self, tipo: str, titulo: str, detalle: str, vence: datetime) -> _Borrador:
        cuerpo = {
            "contact_id": self.contact_id,
            "call_id": self.call_id,
            "tipo": tipo,
            "titulo": titulo,
            "detalle": detalle,
            "vence_el": vence.isoformat(),
            "asignada_a": "comercial_asignado",
        }
        return _Borrador("crear_tarea", cuerpo, distintivo=tipo)

    def revisar(self, motivo: str, ref: datetime) -> _Borrador:
        vence = self.cfg.calendario.mas_dias(ref, self.cfg.vencimiento_por_defecto_dias)
        return self.tarea("revisar_llamada", f"Revisar la llamada {self.call_id}", motivo, vence)

    def recordatorio(self, distintivo: str, canal: dict, cuando: datetime) -> _Borrador:
        cuerpo = {
            "contact_id": self.contact_id,
            **canal,
            "cuando": cuando.isoformat(),
            "cancelar_si": "lead_responde",
        }
        return _Borrador("programar_recordatorio", cuerpo, distintivo=distintivo, distintivo_obligatorio=True)

    def whatsapp(self, plantilla: str, parametros: dict[str, str]) -> _Borrador:
        cuerpo = {
            "organization_id": self.evento["organization_id"],
            "telefono": self.telefono,
            "plantilla": plantilla,
            "parametros": {"nombre": self.nombre, **parametros},
            "idioma": self.lead.get("language") or "es",
        }
        return _Borrador("enviar_plantilla_whatsapp", cuerpo, distintivo=plantilla)


def _cerrar(datos: _Datos, clasif: Clasificacion) -> _Borrador:
    cuerpo = {
        "entry_id": datos.entry_id,
        "status": STATUS_POR_ETIQUETA[clasif.etiqueta],
        "etiqueta": clasif.etiqueta,
        "motivo": clasif.motivo,
        "confianza": clasif.confianza,
        "duration_seconds": int((datos.evento.get("telephony") or {}).get("duration_seconds") or 0),
    }
    return _Borrador("cerrar_llamada", cuerpo)


def _respaldo(datos: _Datos) -> _Borrador:
    """Canal de respaldo declarado en la configuración (N3). Hoy solo hay WhatsApp."""
    if datos.cfg.canal_respaldo == "whatsapp":
        borrador = datos.whatsapp("primer_toque_respaldo", {})
    else:
        ref = instante(datos.evento["occurred_at"])
        borrador = datos.tarea(
            "llamar_a_mano",
            f"Llamar a mano a {datos.nombre}",
            f"canal de respaldo «{datos.cfg.canal_respaldo}» sin automatizar",
            datos.cfg.calendario.mas_dias(ref, datos.cfg.vencimiento_por_defecto_dias),
        )
    borrador.etiquetas.add("respaldo")
    return borrador


def _respaldo_si_no_enviado(datos: _Datos, lead: ContextoLead) -> list[_Borrador]:
    """El canal de respaldo es un primer toque: se envía una vez por lead, no en cada intento fallido posterior."""
    return [] if lead.respaldo_enviado else [_respaldo(datos)]


def _callback(datos: _Datos, llm: ClasificacionLLM | None, ref: datetime, cfg: Config) -> list[_Borrador]:
    cal = cfg.calendario
    pedida = _parsear_hora_local(llm.callback_local if llm else None)
    nota = _nota_contexto(datos.evento, llm)
    if pedida is None or pedida <= ref:
        # Sin hora utilizable: se reintenta con la regla general y queda dicho en la nota.
        cuando = cal.primer_valido_desde(cal.mas_horas(ref, cfg.separacion_minima_horas))
        return [datos.llamada(cuando, "callback solicitado (sin hora concreta)", nota)]
    cuando = cal.primer_valido_desde(pedida)
    sin_hora = pedida.hour == 0 and pedida.minute == 0  # el prompt pide 00:00 cuando el lead da un día sin hora
    if cuando == pedida or (sin_hora and cuando.date() == pedida.date()):
        return [datos.llamada(cuando, "callback solicitado", nota)]
    # Caso 12: la hora pedida cae fuera de la ventana → primera franja válida y aviso al lead.
    nota = f"{nota}. Pidió {pedida.isoformat()}, fuera de la ventana de llamadas"
    aviso = datos.whatsapp(
        "aviso_cambio_hora",
        {"hora_pedida": pedida.strftime("%d/%m %H:%M"), "hora_propuesta": cuando.strftime("%d/%m %H:%M")},
    )
    aviso.etiquetas.add("aviso")
    return [
        datos.llamada(cuando, "callback solicitado fuera de la ventana: primera franja válida", nota),
        aviso,
    ]


def _parsear_hora_local(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return a_madrid(texto)
    except ValueError:
        return None


def _nota_contexto(evento: dict, llm: ClasificacionLLM | None) -> str:
    if llm and llm.nota_contexto:
        return llm.nota_contexto
    # Respaldo determinista: lo que el agente fue anotando (puede estar a medias).
    notas = (evento.get("agent_outcome") or {}).get("slots_snapshot") or {}
    partes = [f"{k}: {v}" for k, v in notas.items() if v not in (None, "", [], {})]
    return "; ".join(partes) if partes else "sin datos recogidos"


def _cancelaciones(
    lead: ContextoLead, ref: datetime, motivo: str, solo_lead_responde: bool, canal: str | None = None
) -> list[_Borrador]:
    borradores = []
    for rec in lead.recordatorios_pendientes:
        if solo_lead_responde and rec.cancelar_si != "lead_responde":
            continue
        if canal is not None and rec.canal != canal:
            continue
        if instante(rec.cuando) <= ref:  # ya se disparó: no hay nada que cancelar
            continue
        borradores.append(
            _Borrador(
                "cancelar_recordatorio",
                {"reminder_id": rec.reminder_id, "motivo": motivo},
                distintivo=rec.reminder_id,
                distintivo_obligatorio=True,
            )
        )
    return borradores


def _sin_whatsapp(borradores: list[_Borrador], datos: _Datos, ref: datetime) -> list[_Borrador]:
    resultado = []
    for b in borradores:
        es_whatsapp = b.operacion == "enviar_plantilla_whatsapp" or (
            b.operacion == "programar_recordatorio" and b.cuerpo.get("canal") == "whatsapp_lead"
        )
        if not es_whatsapp:
            resultado.append(b)
        elif "respaldo" in b.etiquetas:
            # Decidido con el usuario: el lead no se pierde, lo llama un comercial (N1 + N3).
            resultado.append(
                datos.tarea(
                    "llamar_a_mano",
                    f"Llamar a mano a {datos.nombre}",
                    "Toca el canal de respaldo (WhatsApp), pero el lead rechazó WhatsApp (N1).",
                    datos.cfg.calendario.mas_dias(ref, datos.cfg.vencimiento_por_defecto_dias),
                )
            )
    return resultado


def _permitida_con_baja(b: _Borrador) -> bool:
    if b.operacion in PERMITIDAS_CON_BAJA:
        return True
    return b.operacion == "crear_tarea" and b.cuerpo.get("tipo") in TAREAS_INTERNAS


def _asignar_claves(clave_evento: str, borradores: list[_Borrador]) -> list[Orden]:
    """`<clave>:<operacion>`, con distintivo si la operación se repite en el evento (o siempre, si se exige)."""
    repetidas = {b.operacion for b in borradores if sum(o.operacion == b.operacion for o in borradores) > 1}
    ordenes = []
    for b in borradores:
        distintivo = b.distintivo if (b.distintivo_obligatorio or b.operacion in repetidas) else None
        ordenes.append(Orden(b.operacion, clave_orden(clave_evento, b.operacion, distintivo), b.cuerpo))
    return ordenes


def reminder_id_de(orden: Orden) -> str:
    """El reminder_id que «devuelve» el CRM: estable, para poder cancelarlo desde otro proceso."""
    return id_estable("rem", orden.idempotency_key)
