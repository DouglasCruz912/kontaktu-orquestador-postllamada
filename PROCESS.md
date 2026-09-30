# Diario de proceso — reto 2 (LangGraph)

Diario vivo, en el mismo formato que el del primer reto. Sirve para la videollamada con Unai: qué le pedí a la IA, cómo lo verifiqué y dónde se equivocó.

Herramienta: **Claude Code** (Opus 5.5), en Windows 11.

---

## 1. Cómo me comuniqué con la IA

### Preparación (antes de abrir el zip; el reloj no había empezado)

| # | Qué le pedí | Qué hizo la IA | Qué decidí yo |
|---|---|---|---|
| 1 | Pegué el email de Miguel con el segundo reto | Lo clasificó como proyecto nuevo (proceso completo). Avisó de que **no iba a abrir el enlace de DocSend**, porque el acceso marca la hora de inicio. Propuso preparar antes todo lo que no depende del enunciado. | — |
| 2 | Revisar mi entorno | Dijo que no tenía Python ni `uv` ni claves de API | Le corregí: «revisa por terminal, sí tengo Python» (ver sección 3) |
| 3 | «¿uv qué es?» | Lo explicó (como `npm` para Python: entorno, dependencias con versiones fijadas en `uv.lock` y `uv run`) | Instalar `uv`; carpeta nueva en `C:\dev\kontaktu-reto2`, fuera de OneDrive; OpenAI como proveedor |
| 4 | — | Investigó en paralelo la documentación oficial (LangGraph 1.2, langchain-openai, OpenAI, uv) y **verificó sus afirmaciones contra la librería instalada** | — |
| 5 | — | Preparó y probó el harness de Python en una carpeta aparte, para meterlo en el 2º commit | — |

### Reto (desde que abrí el zip, a las 19:33)

| # | Qué le pedí | Qué hizo la IA | Qué decidí yo |
|---|---|---|---|
| 6 | «Dime si ya tienes contexto de la prueba» | Lo recuperó de su memoria persistente (reglas, reloj, entorno, harness) y comprobó que la carpeta del repo estaba vacía | Descomprimí el zip |
| 7 | «Revísalo y dame un resumen» | Leyó los 29 ficheros. Resumió el contrato, los 16 eventos con la etiqueta que espera en cada uno, los casos sin ejemplo, los detalles que hay que cuidar y dos dudas para Kontaktu. Vio que el visor carga un `resultado.json` del corrector, que puntúa etiquetas, órdenes y fallos críticos | — |
| 8 | «Si abro Claude desde la subcarpeta, ¿se pierde la sesión?» | Explicó que la sesión y la memoria van asociadas a la carpeta. Propuso subir el contenido del zip a la raíz en lugar de migrar la sesión (que no está soportado oficialmente) | Aceptado |

## 2. Skills, plugins y herramientas: cuáles y por qué

| Pieza | Para qué | Por qué |
|---|---|---|
| **superpowers: brainstorming** | Clasificar el reto y preparar sin tocar el enunciado | Separar lo que puedo hacer ya (entorno, harness) de lo que depende del zip |
| **Subagente de investigación** | Documentación oficial de LangGraph 1.2, OpenAI y uv | Mi conocimiento de LangGraph puede estar desfasado: la v1 cambió APIs |
| **Prueba desechable** | Instalar LangGraph y ejecutar un grafo mínimo fuera del proyecto | Que un problema de entorno no me coma tiempo del reto |
| **Hooks** (ruff tras cada edición, pytest al terminar) | Verificación automática | La IA no depende de acordarse de comprobar |
| **MCP `docs-langchain`** | Consultar la documentación oficial de LangChain/LangGraph desde Claude | Fuente oficial y actualizada, en lugar de la memoria del modelo |
| **context7** | Documentación de otras librerías (pydantic, pytest…) | Igual que en el primer reto |

## 3. Dónde se equivocó la IA y cómo lo detecté

1. **«No tienes Python instalado».** Solo había buscado `python` en el PATH de sus terminales. Python 3.12 de la Microsoft Store se ejecuta mediante alias de `WindowsApps`, que no estaban en ese PATH. **Lo detecté yo** porque sabía que lo tenía. La IA lo confirmó después mirando el registro de Windows (PEP 514).
2. **Acentos rotos en los mensajes de los hooks** («encontr�»). En Windows, Python escribe stderr en cp1252. **Detectado** al probar los hooks en un proyecto desechable. Arreglo: `sys.stderr.reconfigure(encoding="utf-8")`.
3. **Código metido entre dos imports.** Al añadir esa línea con `sed`, quedó entre dos imports. **Detectado** al pasarle ruff al propio harness.

### Afirmaciones de la investigación comprobadas contra la librería instalada (4 de 4 correctas)

- `add_node` acepta `retry_policy`, `error_handler` y `timeout`.
- `StateGraph(State, context_schema=Ctx)` + `Runtime[Ctx]` funciona.
- Valores por defecto de `RetryPolicy`: `max_attempts=3`, `initial_interval=0.5`, `backoff_factor=2.0`.
- Los modelos falsos de LangChain lanzan `NotImplementedError` con `with_structured_output`. **Decide el diseño:** el clasificador LLM entra como dependencia y los tests usan un doble propio.

## 4. Decisiones (desviaciones y criterio)

- **Decisión:** `uv` con Python 3.12 de la Store. **Por qué:** `uv.lock` da a Kontaktu exactamente las mismas versiones. **Coste si es un error:** se añade un `requirements.txt` exportado para quien no use uv.
- **Decisión:** proyecto en `C:\dev\kontaktu-reto2`, fuera de OneDrive. **Por qué:** `.venv` tiene miles de ficheros.
- **Decisión:** el harness se prepara antes del zip, en una carpeta aparte, y entra en el 2º commit. **Por qué:** el primer commit tiene que ser el zip intacto.
- **Decisión:** el contenido del zip va en la raíz del repo, no en la subcarpeta `reto-kontaktu/` que creó la extracción. **Por qué:** el enunciado ejecuta `python run.py eventos/...` desde donde está `eventos/`, y así Claude Code sigue en la misma carpeta, con su sesión y su memoria. **Cómo lo verifiqué:** md5 de los 29 ficheros antes y después de moverlos (idénticos), y `core.autocrlf false` en el repo para que Git no cambie los finales de línea.
- **Decisión:** `.gitignore` excluye `salida/` y las bases SQLite, pero no `.env.example`. **Por qué:** Kontaktu evalúa desde cero, sin estado previo, y la plantilla del zip tiene que seguir versionada.

## 5. Cronología

- 2026-09-30, 19:13: preparación terminada (entorno, investigación y harness probado). **El zip todavía no se ha abierto.**
- 2026-09-30, 19:33: **zip abierto y descomprimido. Empieza el reloj (tope: 4 h).**
- 2026-09-30, ~19:50: lectura del enunciado y resumen terminados. Primer commit (el zip intacto) y segundo (el harness).
