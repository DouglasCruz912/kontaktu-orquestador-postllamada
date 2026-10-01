"""Cálculo de fechas de la campaña (R3).

Todo se calcula en Europe/Madrid con zoneinfo: nunca se escribe un offset fijo, porque el 25 de octubre
cambia la hora (+02:00 → +01:00). En Windows la base de datos IANA viene del paquete `tzdata`.

Criterio de aritmética, elegido para que el cambio de hora no desplace los plazos:
- «N horas» son horas reales (se suma en UTC): 48 h naturales son 48 h de reloj físico.
- «N días» conservan la hora local: una tarea a «2 días» vence a la misma hora en el calendario.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")

# Claves de campana.yaml en el orden de date.weekday() (lunes = 0).
DIAS_SEMANA = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


@dataclass(frozen=True)
class Calendario:
    # weekday → (apertura, cierre), ambos inclusive; None si ese día no se llama.
    franjas: dict[int, tuple[time, time] | None]
    dias_habiles: frozenset[int]
    tz: ZoneInfo = TZ

    def local(self, instante: datetime) -> datetime:
        return instante.astimezone(self.tz)

    def _franja(self, dia: date) -> tuple[datetime, datetime] | None:
        franja = self.franjas.get(dia.weekday())
        if franja is None:
            return None
        apertura, cierre = franja
        return (
            datetime.combine(dia, apertura, tzinfo=self.tz),
            datetime.combine(dia, cierre, tzinfo=self.tz),
        )

    def en_ventana(self, instante: datetime) -> bool:
        local = self.local(instante)
        franja = self._franja(local.date())
        return franja is not None and franja[0] <= local <= franja[1]

    def primer_valido_desde(self, instante: datetime) -> datetime:
        """Primer instante >= `instante` dentro de la ventana de llamadas."""
        local = self.local(instante)
        for desplazamiento in range(8):  # una semana entera basta para encontrar una franja
            dia = local.date() + timedelta(days=desplazamiento)
            franja = self._franja(dia)
            if franja is None:
                continue
            apertura, cierre = franja
            if desplazamiento == 0:
                if local <= cierre:
                    return max(local, apertura)
                continue
            return apertura
        raise ValueError("la configuración no tiene ninguna franja de llamadas")

    def en_rango_preferente(self, objetivo: datetime, minimo: datetime, maximo: datetime) -> datetime:
        """El instante válido más cercano a `objetivo` dentro de [minimo, maximo].

        Si ningún punto del rango cae en ventana, devuelve la siguiente apertura: la ventana es una
        restricción dura y el rango (p. ej. ocupado 30–90 min) es una preferencia.
        """
        inicio = self.primer_valido_desde(minimo)
        if inicio > self.local(maximo):
            return inicio
        _, cierre = self._franja(inicio.date())
        fin = min(self.local(maximo), cierre)
        return min(max(self.local(objetivo), inicio), fin)

    def mas_horas(self, instante: datetime, horas: float) -> datetime:
        return (instante.astimezone(UTC) + timedelta(hours=horas)).astimezone(self.tz)

    def mas_minutos(self, instante: datetime, minutos: float) -> datetime:
        return self.mas_horas(instante, minutos / 60)

    def mas_dias(self, instante: datetime, dias: int) -> datetime:
        local = self.local(instante)
        return datetime.combine(local.date() + timedelta(days=dias), local.time(), tzinfo=self.tz)

    def mas_dias_habiles(self, instante: datetime, dias: int) -> datetime:
        """Cuenta `dias` días hábiles posteriores al del instante y conserva la hora local."""
        local = self.local(instante)
        dia, contados = local.date(), 0
        while contados < dias:
            dia += timedelta(days=1)
            if dia.weekday() in self.dias_habiles:
                contados += 1
        return datetime.combine(dia, local.time(), tzinfo=self.tz)


def a_madrid(texto: str) -> datetime:
    """ISO 8601 → instante en Europe/Madrid. Sin offset se interpreta como hora de Madrid, nunca la del sistema
    (astimezone() sobre un datetime naive usaría la zona de la máquina que ejecute el reto)."""
    valor = datetime.fromisoformat(texto)
    return valor.replace(tzinfo=TZ) if valor.tzinfo is None else valor.astimezone(TZ)


def calendario_desde_config(ventana: dict[str, list[str]], dias_habiles: list[str]) -> Calendario:
    franjas: dict[int, tuple[time, time] | None] = {}
    for indice, nombre in enumerate(DIAS_SEMANA):
        valores = ventana.get(nombre) or []
        franjas[indice] = (time.fromisoformat(valores[0]), time.fromisoformat(valores[1])) if valores else None
    habiles = frozenset(DIAS_SEMANA.index(nombre) for nombre in dias_habiles)
    return Calendario(franjas=franjas, dias_habiles=habiles)
