"""Casos 4, 5, 6, 11 y 13 (y `otro` técnico) salen de la señalización, sin LLM."""

import copy

import pytest

from orquestador.senalizacion import clasificar_por_senalizacion
from tests.utiles import cargar_evento


@pytest.mark.parametrize(
    "fichero, etiqueta",
    [
        ("01-call-ended-nuria.json", "sin_respuesta"),
        ("02-call-ended-tomas.json", "ocupado"),
        ("10-call-ended-sonia.json", "buzon"),  # heuristic_regex con saludo ambiguo: caso 13
        ("12-call-ended-nuria.json", "buzon"),
    ],
)
def test_eventos_resueltos_por_senalizacion(fichero, etiqueta):
    assert clasificar_por_senalizacion(cargar_evento(fichero)).etiqueta == etiqueta


@pytest.mark.parametrize(
    "fichero",
    [
        "03-call-ended-elena.json",
        "06-call-ended-pedro.json",
        "07-call-ended-laura.json",
        "11-call-ended-carla.json",
    ],
)
def test_conversaciones_van_al_llm(fichero):
    assert clasificar_por_senalizacion(cargar_evento(fichero)) is None


def _variante(cambios_telefonia: dict, transcript=None):
    evento = copy.deepcopy(cargar_evento("01-call-ended-nuria.json"))
    evento["telephony"].update(cambios_telefonia)
    if transcript is not None:
        evento["transcript"] = transcript
    return evento


def test_603_es_rechazada_aunque_livekit_diga_user_rejected():
    evento = _variante({"sip_status_code": 603, "sip_status": "Decline D21", "disconnect_reason": "USER_REJECTED"})
    assert clasificar_por_senalizacion(evento).etiqueta == "rechazada"


@pytest.mark.parametrize("codigo", [500, 503, 502])
def test_5xx_es_otro(codigo):
    evento = _variante({"sip_status_code": codigo, "disconnect_reason": "SIP_TRUNK_FAILURE"})
    assert clasificar_por_senalizacion(evento).etiqueta == "otro"


def test_ivr_es_otro():
    evento = _variante(
        {
            "sip_status_code": 200,
            "answered_at": "2026-09-15T10:11:40+02:00",
            "amd": {"result": "machine-ivr", "source": "livekit_amd", "greeting_transcript": "Pulse 1"},
        }
    )
    assert clasificar_por_senalizacion(evento).etiqueta == "otro"


def test_humano_sin_transcripcion_es_otro():
    evento = _variante(
        {"sip_status_code": 200, "amd": {"result": "human", "source": "livekit_amd"}},
        transcript=[],
    )
    assert clasificar_por_senalizacion(evento).etiqueta == "otro"


def test_uncertain_con_conversacion_se_trata_como_persona():
    evento = _variante(
        {"sip_status_code": 200, "amd": {"result": "uncertain", "source": "livekit_amd"}},
        transcript=[{"role": "user", "message": "¿Sí?", "time_in_call_secs": 2}],
    )
    assert clasificar_por_senalizacion(evento) is None
