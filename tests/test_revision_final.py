"""Regresiones de la revisión final de bugs: un test por hallazgo, con el escenario que lo reproducía."""

import copy
import json

import pytest

from orquestador.aplicacion import procesar
from orquestador.clasificador import ClasificacionLLM
from orquestador.clasificador_openai import datos_usuario
from orquestador.reglas import es_baja
from tests.utiles import cargar_evento

BASE_480 = cargar_evento("01-call-ended-nuria.json")
BASE_CONVERSACION = cargar_evento("03-call-ended-elena.json")
BASE_MENSAJE = cargar_evento("14-message-received-marcos.json")


def llm(etiqueta, **campos) -> ClasificacionLLM:
    base = dict(motivo=etiqueta, confianza=0.9, pide_baja=False, rechaza_whatsapp=False)
    base |= dict(callback_local=None, nota_contexto=None, email=None)
    return ClasificacionLLM(etiqueta=etiqueta, **{**base, **campos})


class Guion:
    """Clasificador doble que responde por event_id."""

    def __init__(self, respuestas: dict[str, ClasificacionLLM]):
        self.respuestas = respuestas

    def clasificar(self, evento):
        return self.respuestas[evento["event_id"]]


def evento(base, n, cuando, contact="c_900", turnos=None, **extra):
    e = copy.deepcopy(base)
    e.update(event_id=f"evr_{n}", idempotency_key=f"r-{n}", occurred_at=cuando)
    e["lead"].update(contact_id=contact, phone="+34600000900")
    if "telephony" in e:
        e["telephony"].update(call_id=f"r-{n}", ended_at=cuando)
    if turnos is not None:
        e["transcript"] = [{"role": "user", "message": t, "time_in_call_secs": 5 + i} for i, t in enumerate(turnos)]
    e.update(extra)
    return e


def ejecutar(tmp_path, eventos, guion=None):
    salida = tmp_path / "salida"
    for e in eventos:
        ruta = tmp_path / f"{e['event_id']}.json"
        ruta.write_text(json.dumps(e, ensure_ascii=False), encoding="utf-8")
        assert procesar(ruta, salida, guion or Guion({})) == 0
    leer = lambda f: [json.loads(x) for x in (salida / f).read_text("utf-8").splitlines()]  # noqa: E731
    ordenes = leer("ordenes.jsonl") if (salida / "ordenes.jsonl").exists() else []
    return leer("decisiones.jsonl"), ordenes


def ops(ordenes, event_id):
    return [o["operacion"] for o in ordenes if o["event_id"] == event_id]


# 1 · CRÍTICO: la regex de baja daba falsos positivos y pisaba al LLM.
@pytest.mark.parametrize(
    "frase",
    [
        "No me llaméis más hoy, que estoy liado. Mañana a las seis sí.",
        "no me llaméis más por la mañana",
        "No quiero que me escribáis, mejor mándamelo al email",
        "no me llamasteis más",
        "no quiero darme de baja",
        "no me escribas más por WhatsApp",
        "No me llames más tarde, que estoy ocupado",
        "no quiero que me llaméis ahora",
    ],
)
def test_regex_no_confunde_estas_frases_con_una_baja(frase):
    assert not es_baja(frase)


@pytest.mark.parametrize(
    "frase",
    [
        "Mira, prefiero que no me llaméis más. Dadme de baja, por favor.",
        "Ya encontré piso, así que no me llaméis más, gracias.",
        "no me volváis a llamar",
        "Quiero darme de baja",
        "borradme de la lista",
        "no quiero que me llaméis.",
    ],
)
def test_regex_si_reconoce_bajas_claras(frase):
    assert es_baja(frase)


def test_la_regex_no_pisa_al_llm_cuando_este_responde(tmp_path):
    """Número equivocado que dice «no me volváis a llamar»: el LLM dice persona_equivocada y eso manda (9 vs 10)."""
    e = evento(BASE_CONVERSACION, 1, "2026-09-15T11:00:00+02:00",
               turnos=["Aquí no vive ninguna Elena. Se ha equivocado, no me volváis a llamar."])  # fmt: skip
    decisiones, ordenes = ejecutar(tmp_path, [e], Guion({"evr_1": llm("persona_equivocada")}))
    assert decisiones[0]["etiqueta"] == "persona_equivocada"
    assert "marcar_no_contactar" not in ops(ordenes, "evr_1")


# 2 · N1: el rechazo de WhatsApp cancela el recordatorio por WhatsApp ya programado (no el del comercial).
def test_rechazo_de_whatsapp_cancela_el_recordatorio_programado(tmp_path):
    enviada = evento(BASE_CONVERSACION, 2, "2026-09-15T16:42:00+02:00", turnos=["mándamelo por WhatsApp"])
    pendiente = evento(BASE_CONVERSACION, 3, "2026-09-16T10:30:00+02:00", turnos=["por WhatsApp no, al correo"])
    guion = Guion(
        {"evr_2": llm("documentacion_enviada"), "evr_3": llm("documentacion_pendiente", rechaza_whatsapp=True)}
    )
    _, ordenes = ejecutar(tmp_path, [enviada, pendiente], guion)
    assert ops(ordenes, "evr_3") == ["cerrar_llamada", "crear_tarea", "cancelar_recordatorio"]
    del_lead = next(o for o in ordenes if o["idempotency_key"] == "r-2:programar_recordatorio:lead")
    cancelada = next(o for o in ordenes if o["operacion"] == "cancelar_recordatorio")
    assert cancelada["idempotency_key"].endswith(cancelada["cuerpo"]["reminder_id"])
    assert del_lead["cuerpo"]["canal"] == "whatsapp_lead"


# 3 · Baja pedida por WhatsApp: se registra, se marca en el CRM y bloquea lo saliente después (N2).
def test_baja_por_whatsapp_bloquea_las_llamadas_siguientes(tmp_path):
    mensaje = evento(BASE_MENSAJE, 4, "2026-09-16T09:30:00+02:00",
                     message={"channel": "whatsapp", "text": "No me llaméis más, dadme de baja."})  # fmt: skip
    llamada = evento(BASE_480, 5, "2026-09-16T12:00:00+02:00")
    decisiones, ordenes = ejecutar(tmp_path, [mensaje, llamada])
    assert decisiones[0]["etiqueta"] == "no_aplica"
    assert ops(ordenes, "evr_4") == ["marcar_no_contactar"]
    assert ops(ordenes, "evr_5") == ["cerrar_llamada"]  # nada de programar_llamada


# 4 · Una cita sin start_time (el esquema lo permite) ya no tumba el evento.
def test_cita_sin_hora_no_tumba_el_evento(tmp_path):
    e = evento(BASE_CONVERSACION, 6, "2026-09-15T16:10:00+02:00", turnos=["Sí, el jueves me va bien."])
    e["agent_outcome"] = {"appointment": {"appointment_id": "apt_1", "start_time": None}, "slots_snapshot": {}}
    decisiones, ordenes = ejecutar(tmp_path, [e], Guion({"evr_6": llm("visita_sin_confirmar")}))
    assert decisiones[0]["etiqueta"] == "visita_reservada"
    tarea = next(o for o in ordenes if o["operacion"] == "crear_tarea")
    assert tarea["cuerpo"]["vence_el"] == "2026-09-17T16:10:00+02:00"


# 5 · El canal de respaldo se envía una sola vez por lead.
def test_respaldo_solo_una_vez(tmp_path):
    horas = ["10:00", "12:30", "15:00", "17:30"]
    eventos = [evento(BASE_480, 10 + i, f"2026-09-15T{h}:00+02:00") for i, h in enumerate(horas)]
    _, ordenes = ejecutar(tmp_path, eventos)
    assert ops(ordenes, "evr_12") == ["cerrar_llamada", "enviar_plantilla_whatsapp"]
    assert ops(ordenes, "evr_13") == ["cerrar_llamada"]


# 6 · El motivo de una baja marcada solo con pide_baja ya no dice «None».
def test_motivo_de_baja_sin_frase(tmp_path):
    e = evento(BASE_CONVERSACION, 7, "2026-09-15T11:00:00+02:00", turnos=["Prefiero que me borréis."])
    respuesta = llm("descartado", motivo="no quiere que le contacten", pide_baja=True)
    decisiones, _ = ejecutar(tmp_path, [e], Guion({"evr_7": respuesta}))
    assert decisiones[0]["etiqueta"] == "no_contactar"
    assert "None" not in decisiones[0]["motivo"]


# 7 · La referencia del prompt es el fin real de la llamada, no la hora de una reentrega.
def test_referencia_del_prompt_es_ended_at():
    reentrega = cargar_evento("15-call-ended-javier-reentrega.json")
    assert datos_usuario(reentrega)["referencia"] == "2026-09-15T17:05"


# 8 · Un callback para un día sin hora no dispara un aviso de cambio de hora falso.
@pytest.mark.parametrize(
    "pedida, esperada, con_aviso",
    [
        ("2026-09-17T00:00", "2026-09-17T10:00:00+02:00", False),  # jueves sin hora → 10:00 sin aviso
        ("2026-09-20T00:00", "2026-09-21T10:00:00+02:00", True),  # domingo → lunes: aquí sí cambia el día
    ],
)
def test_callback_dia_sin_hora(tmp_path, pedida, esperada, con_aviso):
    e = evento(BASE_CONVERSACION, 8, "2026-09-15T11:00:00+02:00", turnos=["Llámame el jueves."])
    _, ordenes = ejecutar(tmp_path, [e], Guion({"evr_8": llm("callback", callback_local=pedida)}))
    llamada = next(o for o in ordenes if o["operacion"] == "programar_llamada")
    assert llamada["cuerpo"]["no_antes_de"] == esperada
    assert ("enviar_plantilla_whatsapp" in ops(ordenes, "evr_8")) is con_aviso
