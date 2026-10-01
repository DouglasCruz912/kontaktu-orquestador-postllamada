# Reto 2 Kontaktu: orquestador post-llamada con LangGraph. Spec y plan

## Contexto

Es el segundo y último reto técnico de Kontaktu. `python run.py <evento.json>` procesa **un evento por proceso** y
añade líneas a `salida/decisiones.jsonl` (una por evento recibido) y a `salida/ordenes.jsonl` (una por cada petición al
CRM). Kontaktu lo ejecuta desde cero contra un **segundo lote que no tenemos**, con casos sin ejemplo y variantes de
otras horas y días. El corrector puntúa etiquetas, órdenes y «críticos fallados». Después hay una videollamada de
45 min con revisión exhaustiva del código LangGraph.
El reloj empezó a las 19:33 con un tope de 4 h, así que el cierre duro es a las 23:33. El repo tiene `41349bc` (el zip intacto) y `26ed9dd` (el harness).

**Éxito:**
- La salida es siempre válida contra los esquemas.
- La etiqueta es correcta.
- Las órdenes llevan los parámetros correctos.
- No hay ningún fallo crítico: N1 (WhatsApp rechazado), N2 (baja), R5 (idempotencia), R6 (otra organización).
- El código se puede defender línea a línea.

## Arquitectura (enfoque A, aprobado): grafo explícito con lo determinista primero

```
START → cargar_contexto → enrutar ─┬─ otra_org ─────────► no_aplica ──────────────┐
                                   ├─ reentrega ────────► repetir_decision ───────┤
                                   ├─ message.received ─► cancelar_recordatorios ─┤
                                   └─ call.ended ─► clasificar_senalizacion       │
                                        ├─ resuelta ───────────────► planificar_ordenes
                                        └─ conversación ► clasificar_llm ► reglas_duras ► planificar_ordenes
                                                                 planificar_ordenes ► emitir ► END
                                                     (no_aplica / repetir / cancelar ► emitir)
```

- **Estado del grafo** (`TypedDict`): `evento`, `ruta`, `lead`, `clasificacion`, `extraccion`, `ordenes`, `decision`
  y `avisos: Annotated[list[str], operator.add]`. Este último usa un reductor para acumular incidencias sin que unos nodos pisen a otros.
- **Contexto de ejecución** (`context_schema`, `Runtime[Ctx]`), con lo que el grafo no posee: `config`, `repo`
  (SQLite), `clasificador` (una interfaz; en los tests es un doble) y `ruta_salida`.
- **Enrutado con `add_conditional_edges`**, para que el diagrama mermaid generado sirva en el README y en la entrevista.
- **Sin checkpointer.** La memoria entre procesos es del dominio (intentos, recordatorios, bajas), no la de un hilo del grafo.
  Alternativa descartada: `SqliteSaver` con `thread_id` = `contact_id`.
- **Escrituras solo en `emitir`, en una única transacción.** Si algo falla antes, no queda rastro, lo que da R8.

## Componentes (paquete `orquestador/`)

| Módulo | Qué hace |
|---|---|
| `config.py` | Carga `config/campana.yaml` en una dataclass |
| `calendario.py` | Horario de llamadas (inclusivo; sábado de 10 a 14; domingo cerrado), `siguiente_apertura`, `ajustar_a_ventana`, +N días hábiles y naturales. Usa `zoneinfo` con Europe/Madrid (con el paquete `tzdata` en Windows; cambio de hora el 25 de octubre) |
| `senalizacion.py` | Clasificación determinista: 486 → ocupado · 603 → rechazada · 408/480 → sin_respuesta · 5xx → otro · 200 con machine-vm o machine-unavailable → buzon · machine-ivr → otro · 200 con humano, uncertain o not_run y sin turnos del lead → otro · resto → conversación (LLM) |
| `clasificador_llm.py` | Interfaz `Clasificador` e implementación OpenAI (`with_structured_output`, `json_schema` strict, `include_raw`). El prompt sale de `prompts/clasificador.md` |
| `reglas.py` | Reglas duras posteriores al LLM: la baja gana a todo (el LLM marca `pide_baja` **o** lo detecta una regex acotada); si hay cita creada → visita_reservada; una hora de callback que falta o no es válida se resuelve sin LLM |
| `politica.py` | Tabla de etiqueta → órdenes, más los filtros N1–N4 (ver abajo) |
| `persistencia.py` | Repositorio SQLite en `salida/estado.sqlite` (dentro de `salida/`, para que borrarla lo resetee todo) |
| `salida.py` | Valida contra `decision.schema.json` y los `requestBody` del OpenAPI (jsonschema), aplica la idempotencia y añade las líneas a los JSONL |
| `grafo.py` | Construye el `StateGraph` y define los nodos |
| `run.py` (raíz) | CLI: carga `.env` (python-dotenv), construye el contexto, invoca el grafo y devuelve el código de salida |

**Salida estructurada del LLM** (`ClasificacionLLM`, todos los campos obligatorios y lo opcional como `X | None`):
`etiqueta` (callback, cortada, visita_sin_confirmar, persona_equivocada, no_contactar, documentacion_enviada,
documentacion_pendiente, descartado u otro), `motivo`, `confianza`, `pide_baja`, `rechaza_whatsapp`,
`callback_local` («YYYY-MM-DDTHH:MM» en Madrid, con la fecha y el día de la semana de referencia en el prompt), `nota_contexto` y `email`.
El prompt incluye el catálogo conversacional y los «pares que se parecen» de `casos.md`.
El modelo se lee de `MODELO` en `.env`; el valor por defecto se verifica con una llamada real y se justifica en el README.
El nodo LLM lleva `RetryPolicy` para errores transitorios. Si se agotan los reintentos o el parseo da `None`, la etiqueta es `otro`, con confianza baja y el motivo de respaldo.

## Política de órdenes

En todo `call.ended` de nuestra organización que se procesa por primera vez, la primera orden es `cerrar_llamada`
{entry_id, status según la tabla de `casos.md`, etiqueta, motivo, confianza, duration_seconds}.
`idempotency_key` = `<clave del evento>:<operación>[:lead|:comercial|:<reminder_id>]`.
`orden_id` = `"ord_" + sha1(clave)[:8]`, lo que reproduce exactamente el ejemplo. `reminder_id` = `"rem_" + sha1(clave)[:8]`.
El instante de referencia es `occurred_at`, calculado en Europe/Madrid.

| Etiqueta | Órdenes además de cerrar |
|---|---|
| visita_reservada | `crear_tarea confirmar_visita_direccion`, que vence en visita − 2 h (o en ref si eso ya pasó). El detalle lleva property_ref y la dirección, o «no disponible» |
| documentacion_enviada | 2 `programar_recordatorio`: whatsapp_lead con `recordatorio_documentacion` a ref + 48 h, y tarea_comercial `llamar_a_mano` a ref + 3 días hábiles (manteniendo la hora). Los dos con `cancelar_si: lead_responde`. Se persisten |
| callback | `programar_llamada` a la hora pedida (sin separación mínima). Si cae fuera de ventana: primer instante válido igual o posterior a esa hora, más `enviar_plantilla_whatsapp aviso_cambio_hora` |
| sin_respuesta / buzon | `programar_llamada` a ref + 2 h, ajustada a la ventana |
| ocupado | `programar_llamada` a ref + 60 min. Si cae fuera, el punto válido más cercano dentro de [+30, +90]; si no hay ninguno, la siguiente apertura |
| cortada / visita_sin_confirmar | `programar_llamada` a ref + 30 min, ajustada a la ventana, con `nota_contexto` (en visita_sin_confirmar, la visita verbal). No se reserva nada (N5) |
| persona_equivocada | `crear_tarea verificar_telefono` a ref + 2 días. Sin reintento y sin baja |
| no_contactar | `marcar_no_contactar` con canal `todos`, más `cancelar_recordatorio` de los pendientes del lead (decidido con el usuario) |
| rechazada | Canal de respaldo: `enviar_plantilla_whatsapp primer_toque_respaldo`. Nunca otra llamada |
| documentacion_pendiente | `crear_tarea enviar_documentacion_email` (con el email en el detalle) a ref + 2 días. Se persiste `rechaza_whatsapp` |
| descartado | Nada más |
| otro | `crear_tarea revisar_llamada` con el motivo (N4) |

**Filtros que se aplican después, en este orden:**
- **N3** (decidido con el usuario): solo en sin_respuesta, ocupado y buzon. Si `intentos >= max_intentos` (contando el actual), `programar_llamada` se sustituye por el respaldo.
- **N1:** si el lead rechazó WhatsApp, se quita todo WhatsApp y el respaldo pasa a `crear_tarea llamar_a_mano` (decidido con el usuario).
- **N4:** con 2 o más cortada o visita_sin_confirmar del mismo lead, se añade `revisar_llamada`.
- **N2:** si el lead ya estaba de baja, solo se emiten `cerrar_llamada`, `cancelar_recordatorio` y `revisar_llamada`.

**Eventos que no son casos:**
- Otra organización → `no_aplica`, 0 órdenes y no se persiste nada.
- Reentrega (la `idempotency_key` ya está guardada) → repite la etiqueta, el motivo y la confianza guardados, con `ordenes: []`.
- `message.received` → `no_aplica`, más `cancelar_recordatorio` por cada recordatorio pendiente del lead (no cancelado y con `cuando` posterior al mensaje).

**Intentos y cortadas** se cuentan sobre la tabla de eventos procesados (los `call.ended` propios por `contact_id`).
Las reentregas y los eventos de otra organización no cuentan.

## Persistencia (SQLite, una transacción por evento en `emitir`)

Tablas:
- `eventos_procesados` (clave PK, event_id, tipo, contact_id, etiqueta, motivo, confianza)
- `leads` (contact_id PK, baja, rechaza_whatsapp)
- `recordatorios` (reminder_id PK, contact_id, canal, cuando, cancelar_si, estado)
- `ordenes` (idempotency_key PK, orden_id, event_id, operacion)

En `emitir`, dentro de la transacción: se insertan las órdenes, saltando las claves ya existentes (R5), se escriben
los JSONL y se hace COMMIT. Si falla la escritura, ROLLBACK.

## Errores y códigos de salida

- Fallo del LLM → respaldo `otro` (exit 0).
- Excepción inesperada → stderr, línea de decisión de respaldo si se conoce el `event_id`, y exit 1.
- JSON ilegible o sin `event_id` → exit 2.
- Si el evento no valida contra el esquema, se avisa y se procesa en modo tolerante cuando están los campos mínimos.
- Cada proceso es independiente, así que un fallo no bloquea el siguiente (R8).

## Estructura y dependencias

`run.py`, `orquestador/`, `prompts/clasificador.md`, `tests/` (con `tests/eventos_extra/` para los casos ⚠ y las variantes),
`scripts/ejecutar_lote.py` (limpia `salida/`, ejecuta `orden.txt` como procesos separados, valida y compara con la tabla dorada),
`docs/decisiones.md`, `README.md`, `requirements.txt` exportado de `uv.lock`.
Dependencias: langgraph, langchain-openai, pydantic, pyyaml, jsonschema, python-dotenv y tzdata. De desarrollo: pytest y ruff.

## Verificación

1. **Unitarias:** calendario (20:00 y 10:00 inclusive, sábado 14:00, domingo, viernes → lunes, cambio de hora), señalización y política por etiqueta.
2. **Integración con clasificador doble:** los 16 eventos en orden, reabriendo el repo en cada uno como si fuera otro proceso. Se comparan con la
   **tabla dorada** (16 decisiones y 29 órdenes, verificada de forma independiente por un agente) y se validan todas las líneas contra los esquemas.
3. **Eventos extra:** 603, callback fuera de ventana, descartado, machine-ivr, 5xx, segunda cortada, 4.º intento sin respuesta, evento en sábado o domingo
   y lead con WhatsApp rechazado y luego agotado.
4. **LLM real:** `scripts/ejecutar_lote.py` con el modelo real, ejecutado 2 veces para ver si es estable, más transcripciones parafraseadas de los pares confusos.
5. Revisión manual en `visor/index.html`. `uv run ruff check . && uv run pytest` antes de cada «hecho».

## Calendario (cierre duro a las 23:33)

| Hora | Tarea |
|---|---|
| ~20:25 | Spec en `docs/` y commit |
| ~21:00 | Proyecto uv, config y calendario, con tests |
| ~21:40 | Señalización, política y persistencia, con tests |
| ~22:15 | Grafo, run.py y emitir; integración con el doble sobre los 16 eventos |
| ~22:45 | Clasificador OpenAI y prompt; lote real |
| ~23:15 | Eventos extra, README, `docs/decisiones.md` y PROCESS.md |
| ~23:30 | Verificación final y push |

**Recorte si no da tiempo** (de lo último a lo primero): eventos extra parafraseados → segunda ejecución del lote real →
regex de baja (el LLM sigue marcando `pide_baja`).

## Después de aprobar

1. Guardar esta spec en `docs/superpowers/specs/2026-09-30-orquestador-design.md` y hacer commit.
2. Por el límite de tiempo, las tareas de este plan hacen de plan de implementación. Se registra como desviación en PROCESS.md.
3. Implementar con TDD, en línea, explicando cada pieza para que el usuario pueda defenderla.
4. Antes de la integración con el LLM real, el usuario tiene que dejar `OPENAI_API_KEY` disponible: reiniciar la terminal o crear `.env`.
