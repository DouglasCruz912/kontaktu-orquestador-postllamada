"""Carga de config/campana.yaml. El fichero es contrato del reto: se lee, nunca se modifica."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from orquestador.calendario import Calendario, calendario_desde_config

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = RAIZ / "config" / "campana.yaml"


@dataclass(frozen=True)
class Config:
    organization_id: str
    calendario: Calendario
    max_intentos: int
    separacion_minima_horas: float
    ocupado_minutos_min: float
    ocupado_minutos_max: float
    cortada_minutos_min: float
    cortada_horas_max: float
    canal_respaldo: str
    documentacion_lead_horas: float
    seguimiento_comercial_dias_habiles: int
    confirmar_visita_margen_horas: float
    vencimiento_por_defecto_dias: int


def cargar_config(ruta: Path = RUTA_CONFIG) -> Config:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    reintentos = datos["reintentos"]
    return Config(
        organization_id=datos["campana"]["organization_id"],
        calendario=calendario_desde_config(datos["ventana_llamadas"], datos["dias_habiles"]),
        max_intentos=int(reintentos["max_intentos"]),
        separacion_minima_horas=reintentos["separacion_minima_horas"],
        ocupado_minutos_min=reintentos["ocupado_minutos_min"],
        ocupado_minutos_max=reintentos["ocupado_minutos_max"],
        cortada_minutos_min=reintentos["cortada_minutos_min"],
        cortada_horas_max=reintentos["cortada_horas_max"],
        canal_respaldo=datos["canal_respaldo"],
        documentacion_lead_horas=datos["recordatorios"]["documentacion_lead_horas"],
        seguimiento_comercial_dias_habiles=int(datos["recordatorios"]["seguimiento_comercial_dias_habiles"]),
        confirmar_visita_margen_horas=datos["tareas"]["confirmar_visita_margen_horas"],
        vencimiento_por_defecto_dias=int(datos["tareas"]["vencimiento_por_defecto_dias"]),
    )
