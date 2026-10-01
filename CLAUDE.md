# Reto 2 Kontaktu: clasificador de llamadas con LangGraph

Sistema en Python + LangGraph que recibe de uno en uno los eventos de fin de llamada (transcripción + señalización de telefonía), clasifica cómo fue la llamada, decide la próxima acción y emite órdenes contra el CRM. El enunciado, el catálogo de casos, los esquemas y los eventos de ejemplo vienen del zip de Kontaktu.

## Reglas de entrega (no negociables)

- **El primer commit es el contenido del zip sin tocar.** Los ficheros del zip (enunciado, esquemas, eventos) son el contrato: no se editan nunca. Si hay que adaptar algo, se hace en código propio.
- **Los prompts que usa el código viven versionados en `prompts/`**, nunca incrustados en el código.
- **La salida cumple SIEMPRE el esquema del enunciado**, también con eventos que no hemos visto: Kontaktu lo ejecutará contra otro conjunto de eventos.

## Comandos

- `uv sync`: crea `.venv` e instala exactamente lo de `uv.lock`
- `uv run pytest`: tests (sin red: el LLM se sustituye por un doble)
- `uv run ruff check .` · `uv run ruff format .`

## LangGraph 1.2: lo verificado (no fiarse de la memoria del modelo)

- Imports: `from langgraph.graph import StateGraph, START, END` · `from langgraph.runtime import Runtime` · `from langgraph.types import RetryPolicy, Command`.
- Dependencias en tiempo de ejecución: `StateGraph(State, context_schema=Ctx)` y el nodo recibe `runtime: Runtime[Ctx]`; se pasan con `graph.invoke(inp, context=Ctx(...))`.
- `add_node(..., retry_policy=RetryPolicy(max_attempts=3), error_handler=..., timeout=...)`. `timeout` solo funciona en nodos **async**.
- `invoke` devuelve un **dict**, no el modelo Pydantic: la salida final se valida explícitamente.
- Los modelos falsos de LangChain **no** soportan `with_structured_output` (lanzan `NotImplementedError`). Por eso el clasificador LLM entra por `context` detrás de una interfaz, y los tests usan un doble propio.
- OpenAI con salida estructurada `method="json_schema", strict=True`: todos los campos obligatorios, sin valores por defecto ni restricciones de `Field`; lo opcional es `X | None`. Con `include_raw=True`, un *refusal* da `parsed=None`; pero por Chat Completions el SDK usa `parse()` y un `ValidationError` o un corte por longitud o por filtro **se lanzan** (corregido tras la revisión contra el código fuente).
- Documentación oficial actualizada: MCP `docs-langchain` (en `.mcp.json`) o https://docs.langchain.com/llms.txt.

## Principios de diseño

- **Determinista primero, LLM después.** Lo que la señalización de telefonía resuelve sola (no contesta, buzón, número erróneo…) no pasa por el LLM.
- **Salida siempre válida.** El último paso valida contra el esquema; si algo falla (el LLM, el parseo, un evento raro), hay un camino de respaldo determinista que produce una orden válida y deja constancia del motivo.
- **Cada decisión tiene un porqué escrito.** En la entrevista preguntarán a bajo nivel por la arquitectura y el código: comentarios que expliquen el porqué, un grafo explícito y `docs/decisiones.md`.

## Cómo trabajar

- TDD: primero el test con un evento de ejemplo real y verlo fallar.
- Antes de decir «hecho»: `uv run ruff check . && uv run pytest`, y ejecutar el sistema contra todos los eventos de ejemplo validando cada salida contra el esquema.
- Registra en `PROCESS.md` los prompts clave, cada desviación del plan y **cada error de la IA junto con cómo se detectó**.
- La clave de OpenAI se lee de `OPENAI_API_KEY`. Nunca se escribe en el repo ni se muestra en la terminal.
