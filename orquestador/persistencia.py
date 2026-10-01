"""Estado entre procesos (R4, R5) en un SQLite local dentro de salida/.

Va dentro de salida/ a propósito: Kontaktu evalúa «desde cero, sin estado ni salida», y borrar esa carpeta
debe resetearlo todo. No es un checkpointer de LangGraph: aquí se guarda el dominio (intentos, recordatorios,
bajas, claves ya emitidas) consultado por contact_id e idempotency_key, no el estado de un hilo del grafo.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from orquestador.dominio import CORTADAS, Clasificacion, ContextoLead, Recordatorio

ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos_procesados (
    idempotency_key TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    contact_id      TEXT NOT NULL,
    etiqueta        TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    confianza       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    contact_id       TEXT PRIMARY KEY,
    baja             INTEGER NOT NULL DEFAULT 0,
    rechaza_whatsapp INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recordatorios (
    reminder_id TEXT PRIMARY KEY,
    contact_id  TEXT NOT NULL,
    canal       TEXT NOT NULL,
    cuando      TEXT NOT NULL,
    cancelar_si TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'pendiente'
);
CREATE TABLE IF NOT EXISTS ordenes (
    idempotency_key TEXT PRIMARY KEY,
    orden_id        TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    operacion       TEXT NOT NULL,
    cuerpo          TEXT NOT NULL
);
"""


class Repositorio:
    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: las transacciones las abro y cierro yo, explícitas, en `transaccion()`.
        self.conn = sqlite3.connect(ruta, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(ESQUEMA)

    def cerrar(self) -> None:
        self.conn.close()

    @contextmanager
    def transaccion(self):
        """Todo o nada: si escribir la salida falla, no queda registrado nada del evento."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # --- lecturas ------------------------------------------------------------------------------------------------

    def evento_previo(self, idempotency_key: str) -> Clasificacion | None:
        """Si el hecho ya se procesó (reentrega, R5), su clasificación original."""
        fila = self.conn.execute(
            "SELECT etiqueta, motivo, confianza FROM eventos_procesados WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if fila is None:
            return None
        return Clasificacion(fila["etiqueta"], fila["motivo"], fila["confianza"], "reentrega")

    def contexto_lead(self, contact_id: str) -> ContextoLead:
        intentos = self.conn.execute(
            "SELECT COUNT(*) FROM eventos_procesados WHERE contact_id = ? AND tipo = 'call.ended'", (contact_id,)
        ).fetchone()[0]
        marcas = ",".join("?" * len(CORTADAS))
        cortadas = self.conn.execute(
            f"SELECT COUNT(*) FROM eventos_procesados WHERE contact_id = ? AND etiqueta IN ({marcas})",
            (contact_id, *sorted(CORTADAS)),
        ).fetchone()[0]
        lead = self.conn.execute(
            "SELECT baja, rechaza_whatsapp FROM leads WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        pendientes = tuple(
            Recordatorio(f["reminder_id"], f["contact_id"], f["canal"], f["cuando"], f["cancelar_si"])
            for f in self.conn.execute(
                "SELECT * FROM recordatorios WHERE contact_id = ? AND estado = 'pendiente' ORDER BY rowid",
                (contact_id,),
            )
        )
        return ContextoLead(
            intentos_previos=intentos,
            cortadas_previas=cortadas,
            baja=bool(lead and lead["baja"]),
            rechaza_whatsapp=bool(lead and lead["rechaza_whatsapp"]),
            recordatorios_pendientes=pendientes,
        )

    def orden_emitida(self, idempotency_key: str) -> bool:
        fila = self.conn.execute("SELECT 1 FROM ordenes WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return fila is not None

    # --- escrituras (siempre dentro de `transaccion()`) ----------------------------------------------------------

    def registrar_evento(self, evento: dict, clasif: Clasificacion) -> None:
        self.conn.execute(
            "INSERT INTO eventos_procesados VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                evento["idempotency_key"],
                evento["event_id"],
                evento["type"],
                evento["lead"]["contact_id"],
                clasif.etiqueta,
                clasif.motivo,
                clasif.confianza,
            ),
        )

    def registrar_orden(self, orden_id: str, event_id: str, operacion: str, clave: str, cuerpo: dict) -> None:
        self.conn.execute(
            "INSERT INTO ordenes VALUES (?, ?, ?, ?, ?)",
            (clave, orden_id, event_id, operacion, json.dumps(cuerpo, ensure_ascii=False)),
        )

    def marcar_lead(self, contact_id: str, *, baja: bool | None = None, rechaza_whatsapp: bool | None = None) -> None:
        self.conn.execute("INSERT OR IGNORE INTO leads (contact_id) VALUES (?)", (contact_id,))
        if baja is not None:
            self.conn.execute("UPDATE leads SET baja = ? WHERE contact_id = ?", (int(baja), contact_id))
        if rechaza_whatsapp is not None:
            self.conn.execute(
                "UPDATE leads SET rechaza_whatsapp = ? WHERE contact_id = ?", (int(rechaza_whatsapp), contact_id)
            )

    def crear_recordatorio(self, rec: Recordatorio) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO recordatorios (reminder_id, contact_id, canal, cuando, cancelar_si) "
            "VALUES (?, ?, ?, ?, ?)",
            (rec.reminder_id, rec.contact_id, rec.canal, rec.cuando, rec.cancelar_si),
        )

    def cancelar_recordatorio(self, reminder_id: str) -> None:
        self.conn.execute("UPDATE recordatorios SET estado = 'cancelado' WHERE reminder_id = ?", (reminder_id,))
