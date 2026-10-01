# Decisiones e interpretaciones

Por qué el sistema hace lo que hace. Las marcadas **(usuario)** las tomó el candidato al ver las alternativas.
Las demás son interpretaciones de la especificación que contrastó un agente revisor independiente.

## Arquitectura

| Decisión | Por qué | Alternativa descartada |
|---|---|---|
| Grafo explícito, lo determinista primero **(usuario)** | Kontaktu evalúa con eventos que no conocemos: cada arista tiene que ser predecible y defendible. Telefonía, AMD y cita creada se resuelven sin LLM: más baratos y sin errores | Agente ReAct con herramientas del CRM: no determinista, y el LLM calcularía fechas |
| El LLM solo clasifica y extrae datos; las órdenes y fechas, en código | La política y las fechas son reglas exactas (R3, N1–N5); un modelo no las garantiza | — |
| Dependencias por `context_schema` / `Runtime[Contexto]` | El estado es lo que cambia durante el evento; el contexto es lo que se le presta al grafo (config, repo, clasificador). Los tests inyectan un doble | Pasarlas en el estado, o con variables globales |
| Sin checkpointer; estado del dominio en SQLite propio | La memoria entre procesos son hechos del dominio consultados por `contact_id` e `idempotency_key`. Un checkpointer guarda el estado de un hilo del grafo, para retomar una ejecución | `SqliteSaver` con `thread_id = contact_id`: mezcla las dos cosas y complica la idempotencia |
| `salida/estado.sqlite` dentro de `salida/` | Kontaktu evalúa «desde cero, sin estado ni salida»: borrar `salida/` lo resetea todo | Un `estado/` aparte que podría contaminar su ejecución |
| Solo `emitir` escribe, en una única transacción | Si cualquier nodo anterior falla, no queda nada a medias (R8) | — |
| `error_handler` en el nodo LLM, que devuelve `Command(goto="reglas_duras")` | Agotados los reintentos se sigue sin LLM (etiqueta `otro`). Verificado: sin `goto`, el grafo termina en el manejador | try/except dentro del nodo, que anularía la RetryPolicy |
| `RetryPolicy` solo para errores transitorios de OpenAI (conexión, timeout, 429, 5xx) | Un 400 o una salida imparseable no mejoran reintentando. `max_retries=0` en el cliente: un solo sitio decide los reintentos | Los reintentos por defecto del cliente y del grafo a la vez |
| `json_schema` strict + `include_raw=True` | El modelo no puede devolver etiquetas fuera del enum. Un parseo fallido llega como `parsed=None` → `ClasificacionInvalida` → `otro` | `function_calling` sin strict |
| Modelo `gpt-6-luna` con `reasoning_effort=none` | Tarea corta de clasificación y extracción en español. Verificado con llamadas reales: con 16/16 en el lote de ejemplo y 21/21 en los extra, unos 2,5 s por llamada. Configurable con `MODELO` / `RAZONAMIENTO` | Un modelo con razonamiento: el doble de latencia y la misma respuesta |

## Interpretaciones de la especificación

| Tema | Decisión |
|---|---|
| Intentos agotados (N3) **(usuario)** | Solo para etiquetas sin contacto (`sin_respuesta`, `ocupado`, `buzon`). Si el lead habló y pidió otra llamada, se le llama. El revisor opinaba aplicarlo a todas las de voz; se mantuvo la decisión |
| Respaldo con WhatsApp rechazado (N1) **(usuario)** | `crear_tarea llamar_a_mano`: el lead no se pierde y no se le escribe por WhatsApp |
| Baja con recordatorios pendientes **(usuario)** | Se cancelan: cancelar no contacta al lead, y dejarlos vivos haría que le llegara un WhatsApp tras la baja |
| Detección de la baja **(usuario)** | La detecta el LLM (`pide_baja`). La regex acotada solo actúa si el LLM no responde. **Cambiado tras la revisión final:** con «LLM **o** regex», la regex pisaba al modelo con falsos positivos («no me llaméis más hoy», un número equivocado que dice «no me volváis a llamar»). N2 se sigue cumpliendo aunque el LLM caiga (probado) |
| Baja pedida por WhatsApp **(usuario)** | En un `message.received`, la regex acotada sobre el texto (en los mensajes no hay LLM) registra la baja, emite `marcar_no_contactar` con canal `todos` y cancela todos los recordatorios. La etiqueta sigue siendo `no_aplica` |
| Rechazo de WhatsApp con recordatorios programados | Se cancelan los recordatorios por WhatsApp pendientes (no el del comercial): si no, saldrían igual y romperían N1 |
| Canal de respaldo repetido | Se envía una vez por lead (`respaldo_enviado`). Un cuarto 480 o un segundo 603 solo cierran la llamada |
| Callback para un día sin hora | El prompt pide 00:00 y el código usa la primera hora válida de ese día. Solo hay `aviso_cambio_hora` si cambia el día (p. ej. domingo → lunes) |
| Referencia temporal del prompt | `telephony.ended_at`: «mañana» se resuelve desde el fin real de la conversación, también en una reentrega cuyo original se perdió |
| Callback antes de 2 h **(usuario)** | Manda la hora pedida, ajustada solo a la ventana. La separación mínima es para reintentos no pedidos |
| `no_contactar` y `cerrar_llamada` | Sí se cierra, con `dnc`: se pide en todo `call.ended`, y «ninguna otra orden» se refiere a contactar al lead |
| Ocupado 30–90 min | El punto medio (+60) reproduce el ejemplo resuelto. Si cae fuera, el punto válido más cercano dentro del rango; si no hay, la siguiente apertura |
| Cortada / visita sin confirmar | +30 min «lo antes posible»; si cae fuera de la ventana, la siguiente apertura (la ventana manda sobre el «mismo día») |
| Callback fuera de ventana (caso 12) | Primer instante válido igual o posterior a la hora pedida, más `aviso_cambio_hora` |
| «A las seis» | La lectura dentro de la ventana (18:00), como en el §4.2 del enunciado. Si el lead la precisa («esta noche a las diez»), se respeta y aplica el caso 12 |
| Días hábiles | Se cuentan desde el día siguiente y se conserva la hora: el martes a las 16:42 da el viernes a las 16:42; el sábado no cuenta |
| Horas frente a días | «N horas» son horas reales (se suma en UTC); «N días» conservan la hora local. Así el cambio de hora del 25 de octubre no desplaza plazos |
| `confirmar_visita_direccion` | Vence en visita − 2 h; si eso ya pasó, en el instante del evento |
| Llamada contestada y transcripción sin el lead | `otro` + `revisar_llamada`: no puede ser `sin_respuesta` (exige 408/480) ni `cortada` |
| N2 en eventos posteriores | Con un lead de baja solo se emiten `cerrar_llamada`, `marcar_no_contactar`, `cancelar_recordatorio` y las tareas internas `revisar_llamada` / `verificar_telefono` |
| N4 | Con la segunda llamada cortada o más (sumando `cortada` y `visita_sin_confirmar` por `contact_id`), se añade `revisar_llamada` |
| Reentrega | Se detecta por la `idempotency_key` guardada, no por `delivery_attempt` (el original pudo perderse). Repite etiqueta, motivo y confianza, con `ordenes: []` |
| Otra organización | Va primero: no se lee ni se escribe nada de nuestro estado. No cuenta como intento |
| Recordatorio pendiente | No cancelado y con `cuando` posterior al mensaje. Uno que ya se disparó no se cancela |
| `orden_id` / `reminder_id` | `"ord_"` / `"rem_"` + sha1(idempotency_key)[:8]: reproducen exactamente el ejemplo resuelto y son estables entre procesos |
| `idempotency_key` | `<clave del evento>:<operación>`, con distintivo solo cuando la operación se repite (`:lead`, `:comercial`, `:<reminder_id>`, `:<tipo>`) |
| Instante de referencia | `occurred_at`, como dice el enunciado (en la reentrega difiere de `ended_at`, pero no se calcula nada) |
| Evento inválido | Validación tolerante: si están los campos que se usan, se procesa y se avisa. Si no, decisión `otro` con confianza 0 y salida 1 |

## Revisión contra la documentación oficial

Un agente de solo lectura contrastó el código con la documentación de LangGraph y langchain-openai (MCP `docs-langchain`)
y con el código fuente instalado.

**Confirmado:**
- `context_schema` + `Runtime` es el patrón de la v1.
- El `error_handler` con `(state, error: NodeError) -> Command(update, goto)` es el ejemplo oficial.
- `retry_on` acepta una tupla.
- Aristas condicionales cuando solo se enruta, y `Command` cuando además se actualiza el estado.
- `max_retries=0`, porque por defecto el cliente reintenta 6 veces.
- El esquema strict es válido.
- `isolation_level=None` con transacciones manuales.

**Corregido, con un test por cada punto:**
- **A:** órdenes huérfanas si fallaba la escritura. Ahora se truncan los JSONL al tamaño previo y se hace ROLLBACK.
- **B:** un ROLLBACK que podía tapar el error. Ahora solo se ejecuta si `in_transaction`.
- **C:** `format: date-time` no se validaba. Ahora hay un checker propio que exige offset.
- **D:** `occurred_at` sin zona. Ahora se interpreta en Madrid, nunca en la zona del sistema.
- **E:** un `type` desconocido. Ahora va a `no_aplica`, sin `cerrar_llamada`.
- **F:** fallos al abrir SQLite o leer los prompts. Ahora quedan dentro del `try` y dejan decisión de respaldo.
- **El docstring de `ClasificacionLLM`** viajaba a OpenAI como `description` del esquema; ahora es un comentario.

**Nota:** `draw_mermaid()` no dibuja la arista `error_handler → reglas_duras`, porque LangGraph 1.2.12 registra el
manejador sin destinos. Existe en ejecución (probado), y el README la dibuja a mano.
