"""Reglas de fechas (R3): ventana de llamadas inclusiva, días hábiles y plazos de campana.yaml."""

from datetime import datetime, timedelta

import pytest

from orquestador.calendario import TZ
from orquestador.config import cargar_config


@pytest.fixture(scope="module")
def cal():
    return cargar_config().calendario


def madrid(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=TZ)


# 2026-09-15 es martes; 19 sábado; 20 domingo; 21 lunes.


@pytest.mark.parametrize(
    "instante, dentro",
    [
        ("2026-09-15T10:00", True),  # extremo inicial inclusive
        ("2026-09-15T20:00", True),  # extremo final inclusive
        ("2026-09-15T09:59", False),
        ("2026-09-15T20:01", False),
        ("2026-09-19T14:00", True),  # sábado hasta las 14:00
        ("2026-09-19T14:01", False),
        ("2026-09-20T12:00", False),  # domingo sin ventana
    ],
)
def test_en_ventana(cal, instante, dentro):
    assert cal.en_ventana(madrid(instante)) is dentro


@pytest.mark.parametrize(
    "desde, esperado",
    [
        ("2026-09-15T12:12", "2026-09-15T12:12"),  # ya dentro: no se mueve
        ("2026-09-15T08:30", "2026-09-15T10:00"),  # antes de abrir: abre ese día
        ("2026-09-15T21:00", "2026-09-16T10:00"),  # tras cerrar: día siguiente
        ("2026-09-18T20:30", "2026-09-19T10:00"),  # viernes noche → sábado
        ("2026-09-19T15:00", "2026-09-21T10:00"),  # sábado tarde → lunes (domingo cerrado)
        ("2026-09-20T11:00", "2026-09-21T10:00"),  # domingo → lunes
    ],
)
def test_primer_valido_desde(cal, desde, esperado):
    assert cal.primer_valido_desde(madrid(desde)) == madrid(esperado)


@pytest.mark.parametrize(
    "ref, esperado",
    [
        ("2026-09-15T10:31", "2026-09-15T11:31"),  # ejemplo resuelto: punto medio de 30–90
        ("2026-09-15T19:15", "2026-09-15T20:00"),  # medio fuera; 20:00 sigue dentro del rango
        ("2026-09-19T13:45", "2026-09-21T10:00"),  # sábado: ningún punto del rango vale → siguiente apertura
        ("2026-09-15T08:50", "2026-09-15T10:00"),  # medio 09:50 fuera; 10:00 dentro de [09:20, 10:20]
    ],
)
def test_en_rango_preferente(cal, ref, esperado):
    r = madrid(ref)
    resultado = cal.en_rango_preferente(r + timedelta(minutes=60), r + timedelta(minutes=30), r + timedelta(minutes=90))
    assert resultado == madrid(esperado)


@pytest.mark.parametrize(
    "ref, esperado",
    [
        ("2026-09-15T16:42", "2026-09-18T16:42"),  # martes → viernes
        ("2026-09-18T09:00", "2026-09-23T09:00"),  # viernes → miércoles
        ("2026-09-19T12:00", "2026-09-23T12:00"),  # sábado no es hábil: lun, mar, mié
    ],
)
def test_mas_dias_habiles(cal, ref, esperado):
    assert cal.mas_dias_habiles(madrid(ref), 3) == madrid(esperado)


def test_horas_naturales_son_absolutas_en_cambio_de_hora(cal):
    # El 25-10-2026 a las 03:00 se vuelve a las 02:00: 48 h reales desde el viernes 23 a las 12:00
    # caen el domingo 25 a las 11:00 de reloj, con offset +01:00.
    resultado = cal.mas_horas(madrid("2026-10-23T12:00"), 48)
    assert resultado.isoformat() == "2026-10-25T11:00:00+01:00"


def test_dias_naturales_conservan_la_hora_local(cal):
    assert cal.mas_dias(madrid("2026-09-15T11:04"), 2) == madrid("2026-09-17T11:04")
    assert cal.mas_dias(madrid("2026-10-24T12:00"), 2).isoformat() == "2026-10-26T12:00:00+01:00"


def test_config_basica():
    cfg = cargar_config()
    assert cfg.organization_id == "org_demo_a"
    assert cfg.max_intentos == 3
    assert cfg.canal_respaldo == "whatsapp"
