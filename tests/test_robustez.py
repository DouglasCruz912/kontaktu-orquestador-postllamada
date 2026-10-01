"""R8: un fallo procesando un evento no impide procesar los siguientes, y siempre queda una línea de decisión."""

import json
import os
import subprocess
import sys

import httpx
import openai

from orquestador.aplicacion import procesar
from orquestador.clasificador_openai import ClasificacionInvalida
from tests.utiles import EVENTOS, RAIZ


class DobleQueFalla:
    def __init__(self, error: Exception):
        self.error = error
        self.llamadas = 0

    def clasificar(self, evento: dict):
        self.llamadas += 1
        raise self.error


def _decisiones(directorio):
    return [json.loads(x) for x in (directorio / "decisiones.jsonl").read_text("utf-8").splitlines()]


def _ordenes(directorio):
    return [json.loads(x) for x in (directorio / "ordenes.jsonl").read_text("utf-8").splitlines()]


def test_salida_imparseable_del_llm_acaba_en_otro_con_revision(tmp_path):
    doble = DobleQueFalla(ClasificacionInvalida("json roto"))
    assert procesar(EVENTOS / "03-call-ended-elena.json", tmp_path, doble) == 0
    assert doble.llamadas == 1  # un ValueError no se reintenta: no mejoraría
    assert _decisiones(tmp_path)[0]["etiqueta"] == "otro"
    assert [o["operacion"] for o in _ordenes(tmp_path)] == ["cerrar_llamada", "crear_tarea"]
    assert _ordenes(tmp_path)[1]["cuerpo"]["tipo"] == "revisar_llamada"


def test_caida_de_red_se_reintenta_y_luego_hay_respaldo(tmp_path):
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    doble = DobleQueFalla(error)
    assert procesar(EVENTOS / "03-call-ended-elena.json", tmp_path, doble) == 0
    assert doble.llamadas == 3  # RetryPolicy(max_attempts=3)
    assert _decisiones(tmp_path)[0]["etiqueta"] == "otro"


def test_la_baja_se_detecta_aunque_falle_el_llm(tmp_path):
    """La red de seguridad (regex) no depende del modelo: N2 se cumple aunque el LLM caiga."""
    doble = DobleQueFalla(ClasificacionInvalida("json roto"))
    assert procesar(EVENTOS / "06-call-ended-pedro.json", tmp_path, doble) == 0
    assert _decisiones(tmp_path)[0]["etiqueta"] == "no_contactar"
    assert [o["operacion"] for o in _ordenes(tmp_path)] == ["cerrar_llamada", "marcar_no_contactar"]


def test_json_ilegible_sale_con_2_sin_escribir(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{ no es json", encoding="utf-8")
    assert procesar(roto, tmp_path / "salida", DobleQueFalla(AssertionError())) == 2
    assert not (tmp_path / "salida" / "decisiones.jsonl").exists()


def test_evento_sin_campos_minimos_deja_decision_de_respaldo_y_sale_con_1(tmp_path):
    incompleto = tmp_path / "incompleto.json"
    incompleto.write_text(json.dumps({"event_id": "evt_roto", "type": "call.ended"}), encoding="utf-8")
    assert procesar(incompleto, tmp_path / "salida", DobleQueFalla(AssertionError())) == 1
    decision = _decisiones(tmp_path / "salida")[0]
    assert decision["event_id"] == "evt_roto" and decision["etiqueta"] == "otro" and decision["ordenes"] == []


def test_tras_un_fallo_el_siguiente_evento_se_procesa(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{", encoding="utf-8")
    procesar(roto, tmp_path / "salida", None)
    assert procesar(EVENTOS / "01-call-ended-nuria.json", tmp_path / "salida", DobleQueFalla(AssertionError())) == 0


def test_run_py_como_proceso(tmp_path):
    entorno = {**os.environ, "SALIDA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8", "OPENAI_API_KEY": ""}
    proceso = subprocess.run(
        [sys.executable, str(RAIZ / "run.py"), str(EVENTOS / "02-call-ended-tomas.json")],
        env=entorno,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proceso.returncode == 0, proceso.stderr
    assert "evt_02: ocupado" in proceso.stdout
    assert len(_ordenes(tmp_path)) == 2
    sin_argumentos = subprocess.run([sys.executable, str(RAIZ / "run.py")], capture_output=True, env=entorno)
    assert sin_argumentos.returncode == 2


# --- correcciones de la revisión de buenas prácticas (PROCESS.md) -------------------------------------------------


def test_si_falla_la_escritura_no_quedan_ordenes_huerfanas(tmp_path, monkeypatch):
    """Riesgo A: si la decisión no llega a escribirse, se truncan las órdenes ya escritas y se deshace el estado."""
    from orquestador import salida as modulo_salida

    original = modulo_salida._anadir_lineas

    def falla_en_decisiones(ruta, lineas):
        if ruta.name == "decisiones.jsonl":
            raise OSError("disco lleno")
        original(ruta, lineas)

    monkeypatch.setattr(modulo_salida, "_anadir_lineas", falla_en_decisiones)
    doble = DobleQueFalla(AssertionError())
    assert procesar(EVENTOS / "02-call-ended-tomas.json", tmp_path, doble) == 1
    assert (tmp_path / "ordenes.jsonl").read_text("utf-8") == ""
    monkeypatch.setattr(modulo_salida, "_anadir_lineas", original)
    # El estado se deshizo: al reprocesar no es una reentrega y las órdenes se emiten una sola vez.
    assert procesar(EVENTOS / "02-call-ended-tomas.json", tmp_path, doble) == 0
    assert len(_ordenes(tmp_path)) == 2


def test_fecha_sin_offset_no_valida_contra_el_openapi():
    """Riesgo C: el OpenAPI pide offset explícito en no_antes_de."""
    from orquestador.dominio import Orden
    from orquestador.salida import errores_orden

    cuerpo = {"entry_id": "e", "telefono": "t", "motivo": "m", "no_antes_de": "2026-09-15T11:31:00"}
    assert errores_orden(Orden("programar_llamada", "k", cuerpo))
    assert errores_orden(Orden("programar_llamada", "k", {**cuerpo, "no_antes_de": "mañana"}))
    assert not errores_orden(Orden("programar_llamada", "k", {**cuerpo, "no_antes_de": "2026-09-15T11:31:00+02:00"}))


def test_occurred_at_sin_offset_se_interpreta_en_madrid():
    """Riesgo D: nunca en la zona horaria de la máquina que lo ejecute."""
    from orquestador.calendario import a_madrid

    assert a_madrid("2026-09-15T10:31:00").isoformat() == "2026-09-15T10:31:00+02:00"
    assert a_madrid("2026-09-15T08:31:00+00:00").isoformat() == "2026-09-15T10:31:00+02:00"


def test_tipo_de_evento_desconocido_no_cierra_ninguna_llamada(tmp_path):
    """Riesgo E: un `type` fuera del contrato es no_aplica, sin cerrar_llamada."""
    evento = json.loads((EVENTOS / "01-call-ended-nuria.json").read_text("utf-8"))
    evento["type"] = "call.started"
    ruta = tmp_path / "raro.json"
    ruta.write_text(json.dumps(evento), encoding="utf-8")
    assert procesar(ruta, tmp_path / "salida", DobleQueFalla(AssertionError())) == 0
    assert _decisiones(tmp_path / "salida")[0]["etiqueta"] == "no_aplica"
    assert not (tmp_path / "salida" / "ordenes.jsonl").exists()
