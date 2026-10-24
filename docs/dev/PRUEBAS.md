# Pruebas

Cómo está organizada la suite, qué fixtures y falsos hay, cómo escribir una prueba nueva y qué
protege cada una de las **pruebas guardianas**: las que no verifican una función sino una promesa del
proyecto (que el prompt no cambia, que ningún canal enseña resultados antes de tiempo, que nada
abre una ventana ni toca la red). Si una guardiana falla, casi nunca hay que arreglar la prueba: hay
que entender qué promesa se rompió.

## Qué hay

Al 2026-10-24, `python -m pytest --collect-only` reúne **unas 2.400 pruebas** en 129 archivos de
`tests/`:

| Carpeta | Pruebas | Archivos | Qué es |
|---|---:|---:|---|
| `tests/unit/` | 2.326 | 119 | pruebas de una pieza o de pocas, con una base en memoria o temporal y el exterior simulado; incluye las guardianas y las de los scripts |
| `tests/e2e/` | 51 | 8 | **de punta a punta**: el sistema real (scheduler, generador con las estrategias reales, sombras, etiquetador, brazo, riesgo, monitor, Telegram, push y la aplicación FastAPI) con **solo el exterior simulado**: Anthropic, Bitunix REST y WebSocket, y Telegram |
| `tests/integration/` | 4 | 2 | **manuales**, contra la red real de Bitunix; se omiten por defecto (ver abajo) |
| `tests/support/` | — | — | ayudantes compartidos, no son pruebas |

La cifra crece con el código: no la tomes como contrato. La suite completa tarda unos 11 o 12
minutos en el equipo de desarrollo; mientras desarrollas corre solo lo que tocas (comandos en
[`ENTORNO_DESARROLLO.md`](ENTORNO_DESARROLLO.md#correr-las-pruebas)).

**Las pruebas no necesitan credenciales ni red.** Ninguna llama a la API de Anthropic ni a Telegram
ni a Bitunix. Las excepciones son las dos pruebas manuales de `tests/integration/`, que exigen
`MINERVA_RUN_LIVE_TESTS=1`:

- `test_bitunix_live.py`: endpoints **públicos** de Bitunix (sin credenciales).
- `test_start_stop_bot_scripts.py`: corre `start_bot.ps1` y `stop_bot.ps1` con el `lifespan` real, solo en
  Windows y **siempre contra el puerto 8001 y una copia temporal de la base**; es lento. No lo corras sin
  querer: lanza el bot.

## Cómo se organizan las pruebas unitarias

Los archivos se nombran por lo que prueban: `test_<módulo>.py` (`test_risk_engine.py`,
`test_stop_engine.py`), `test_<paquete>_<tema>.py` (`test_llm_prompts.py`) o `test_<script>_script.py`
(`test_placebo_check_script.py`). Las pruebas de las fases 4 y 5 se agrupan por fase
(`test_phase5_read_api.py`, `test_phase5_push.py`). Para saber qué prueba cubre un archivo, mira la línea
**Pruebas que lo importan o citan** del bloque de hechos de su guía en
[`modulos/`](modulos/trading.md): la calcula el código.

**Las convenciones del código de las pruebas:**

- El nombre de la prueba es una frase en inglés que dice lo que se garantiza
  (`test_a_hook_that_raises_does_not_affect_the_labeling`); el docstring del módulo y los comentarios, en
  español, explican **por qué** existe la prueba y qué incidente o decisión la originó.
- `asyncio_mode = "auto"`: una `async def test_...` corre sin decorador (varias pruebas antiguas llevan
  `@pytest.mark.asyncio` de más; es inofensivo).
- Los falsos se definen **junto a la prueba que los usa** (`FakeRestClient`, `FakeRest`, `FakeWs`,
  `FakeStrategy`...), salvo los que se reutilizan, que viven en un módulo propio.

## Fixtures y falsos

### Los fixtures compartidos (`tests/conftest.py`)

| Fixture | Para qué sirve |
|---|---|
| `db` | una `Database` **en memoria** (`:memory:`) ya conectada, con el esquema completo; se cierra sola. Es el punto de partida de casi toda prueba que toca la base |
| `reveal_open` | simula "gate abierto y registro oficial del veredicto" en los comandos de Telegram (parchea `app.telegram.commands.is_outcome_reveal_allowed`); sin él, balance, equity, PnL y PF de la cuenta paper están ocultos, que es el estado real hoy |
| `real_db_copy` | una `Database` sobre una **copia** de la base real (`settings.database_path`) en una ruta temporal, con un `assert` explícito de que no es la ruta real. Úsala en vez de apuntar un `Database` a la base de trabajo. Si la base no existe, la copia queda vacía |
| `_the_real_task_scheduler_is_never_reachable` (de sesión, automático) | deja `MINERVA_SCHTASKS_EXE` apuntando a una **trampa** durante toda la sesión: si alguna prueba intenta usar el Programador de tareas real de Windows, la sesión falla al terminar con la llamada |

### La configuración en las pruebas

Crea la configuración con **`Settings(_env_file=None, ...)`**, pasando los valores por su alias (el nombre
de la variable de entorno), nunca leyendo el `.env`:

```python
def make_settings(**overrides) -> Settings:
    defaults = {"LLM_LIVE_LABELING_ENABLED": True, "LLM_DAILY_BUDGET_USD": 1.0}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]
```

`_env_file=None` hace la prueba independiente del `.env` de quien la corre (que además no debe leerse).
Casi cada archivo define su propio `make_settings`. Lo que sí lee el `.env` es el `settings` global
(`app.config.settings`), por eso una prueba que lo parchea (`monkeypatch.setattr(main.settings, ...)`) debe
apagar explícitamente lo que no quiere (ver `tests/unit/lifespan_fakes.py`: apaga el push de Telegram "aunque
ahí estuviera encendido").

### Los falsos que se reutilizan

| Falso | Dónde | Qué simula |
|---|---|---|
| `FakeLlmClient` | `app/llm/client.py` | el cliente del LLM: cada llamada consume la siguiente respuesta programada (`LlmRawResponse` o una excepción) y registra la petición en `calls`; sin respuestas, falla. Está en el código de la aplicación (es una zona protegida: [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md#etiquetador-y-medición)) |
| `FakeRest`, `FakeWs`, `FakeCoinGecko`, `FakeUniverse` y `patch_lifespan` | `tests/unit/lifespan_fakes.py` | los dobles de red para correr `app.main.lifespan` **real** en proceso, con una base temporal y los interruptores apagados |
| `World`, `ScriptedRest`, `ScriptedLlm`, `FakeTelegramPush` | `tests/e2e/world.py` | el arnés de punta a punta (ver abajo) |
| `FakeDocker`, `FakeTasks`, `FakeNativeBot`, `MigrationWorld`, `make_native_db` | `tests/support/fakes.py` y `fake_docker.py` | un `docker` falso, un Programador de tareas falso y un bot nativo falso para las pruebas de la mudanza a Docker |
| `httpx.MockTransport` | en cada prueba | las respuestas HTTP de Bitunix, Telegram y las fuentes de noticias, sin red |
| `scheduler_tripwire`, `window_probe`, `winproc.NO_WINDOW`, `sourcecheck` | `tests/support/` | la trampa del Programador de tareas, la detección de ventanas de consola, el indicador `CREATE_NO_WINDOW` y la lectura de código PowerShell y de cadenas incrustadas |

### El arnés de punta a punta (`tests/e2e/`)

`World.start(...)` arma el sistema real sobre una base de archivo temporal y deja que la prueba lo maneje con
`seed_market(...)` (siembra el universo, los contratos y las velas), `cycle()` (un ciclo del scheduler **y
todo lo que dispara**: la sombra, la etiqueta y el brazo, esperando a que terminen), `sync()` (un ciclo
periódico del monitor: carga las posiciones abiertas, marcas y equity), `tick(símbolo, precio)` (un tick del
WebSocket), `trades()`, `shadows()` y `arm_rows()` (consultas a la base), y las consultas de la API y de
Telegram. Lo que simula es solo el exterior:

- **Anthropic:** un cliente programable que decide por símbolo (`ScriptedLlm`).
- **Bitunix REST:** precios fijos; las velas se **siembran en la caché**.
- **El WebSocket:** los ticks se inyectan.
- **Telegram:** un cliente que registra los mensajes.

Dos detalles que importan: el `.env` real **nunca** se lee (`Settings(_env_file=None)`) y todas las pruebas de
`tests/e2e/` llevan un candado de red (`no_external_network`, un fixture automático) que hace fallar
cualquier resolución de un nombre que no sea de este mismo equipo. El reloj es el real: las velas se siembran
de modo que la última cierre "hace unos segundos".

## Cómo escribir una prueba nueva

1. **Decide la carpeta.** Una pieza o pocas, con el exterior simulado: `tests/unit/`. La cadena entera: un
   escenario en `tests/e2e/`. Contra la red real: `tests/integration/`, marcada `@pytest.mark.slow` y con
   el `skipif` de `MINERVA_RUN_LIVE_TESTS`.
2. **Una base sin tocar `data/`:** usa el fixture `db` (en memoria) o `tmp_path` para una base de archivo.
   Para datos reales, `real_db_copy`.
3. **Sin red y sin gasto:** el LLM con `FakeLlmClient`, Bitunix y las demás con un falso o
   `httpx.MockTransport`. Si pruebas algo que podría llamar a la red por descuido, hazlo fallar fuerte
   (un transporte que lanza) en vez de confiar en que no ocurra.
4. **La configuración con `Settings(_env_file=None, ...)`**, los valores por alias.
5. **El tiempo bajo control:** muchos objetos reciben un reloj (`clock=`) o un `now`; pásalo en vez de depender
   de la hora real, y usa `asyncio.Event`/`wait_until` en lugar de `sleep` largos.
6. **Las tareas de fuego y olvido** (etiquetador, brazo): espera a que terminen antes de afirmar
   (`await _wait_for_tasks(labeler)` en las unitarias; `World.cycle()` ya las espera en las de punta a punta).
   Una prueba que afirma antes de tiempo es la que falla "a veces".
7. **Procesos hijos:** `creationflags=NO_WINDOW` (de `tests.support.winproc`) en todo
   `subprocess.run/Popen`; una guardiana lo exige.
8. **Cierra lo que abres:** una `Database` de archivo abierta cuelga el proceso al salir en Windows; usa los
   fixtures, que la cierran.
9. **Nada de secretos reales:** si necesitas una contraseña o un token, inventa uno que se declare falso. La
   guardiana de higiene falla ante un valor con forma de credencial.

**Lo que toda función de riesgo o de simulación necesita** (SL, TP, trailing, liquidación, comisiones,
funding): pruebas con **casos límite**, no solo el camino feliz. Una lista para partir:

- el precio exactamente **en** el umbral (`<=` contra `<`) en LONG y en SHORT;
- el hueco de apertura (un gap que salta el umbral) y el **orden** cuando SL y liquidación se cruzan en la
  misma vela;
- cantidades en el mínimo del contrato, o por debajo, y redondeo a la precisión;
- funding positivo y negativo, y un evento que cae justo en el límite de la ventana;
- datos ausentes (`None`) y obsoletos;
- la escala de los parámetros `*_pct` (fracción 0-1 o porcentaje 0-100);
- que **repetir** la operación (un reinicio, un segundo ciclo) no duplica ni cambia nada.

**Un ejemplo** de la forma habitual: un repositorio contra la base en memoria.

```python
import pytest
from app.persistence.repositories import system_state_repo


@pytest.mark.asyncio
async def test_a_state_key_is_overwritten_not_duplicated(db):
    await system_state_repo.set_state(db, "clave", "1")
    await system_state_repo.set_state(db, "clave", "2")

    assert await system_state_repo.get_state(db, "clave") == "2"
```

**Cuando cambias el comportamiento de algo de la medición** (zona protegida) escribe, además, la
**prueba de invarianza**: el mismo escenario con y sin tu cambio deja `llm_logs`, `shadow_trades`, el
estado de la medición y `measurement_keys` idénticos. El modelo es
`tests/unit/test_paper_execution_isolation.py::test_the_measurement_is_identical_with_the_arm_on_and_off`.

**La regla del humo de punta a punta** (CLAUDE.md): antes de dar por terminada una tarea que toque el
pipeline de datos o la persistencia, corre un smoke test de punta a punta con un subconjunto pequeño; que
las pruebas unitarias pasen no es suficiente. Las de `tests/e2e/` cubren la cadena con el exterior
simulado; para el pipeline real, corre el bot de desarrollo sobre una copia
([`ENTORNO_DESARROLLO.md`](ENTORNO_DESARROLLO.md#correr-el-bot-de-desarrollo-en-el-puerto-8001)).

## Las pruebas guardianas

Cada una protege una promesa. Están agrupadas por la promesa, no por archivo.

### El prompt congelado

| Prueba | Qué protege |
|---|---|
| `tests/unit/test_prompt_v5_prepared.py` | los **bytes** de `app/llm/prompts.py` y `app/llm/market_features.py` (con los saltos de línea normalizados a LF), el hash del `SYSTEM_PROMPT`, `PROMPT_VERSION`, `FEATURES_VERSION`, el formato exacto de `build_user_message` y que `FROZEN_PROMPT_SHA256` es el que congeló el autor. Además prueba que el prompt v5 preparado es distinto, deriva del v4 sin modificarlo y que nada lo importa. Si falla, **alguien cambió lo que ve el modelo** |
| `tests/unit/test_llm_prompts.py`, `tests/unit/test_llm_market_features.py` | las features y el mensaje: unidades, signos del funding, cada campo `null` explícito |
| `tests/unit/test_shadow_live_labeler.py` | el hash se comprueba cada ciclo, sin descartes silenciosos; el retraso máximo; los fallos dejan `SIN_LLM`; el cierre limpio |

### Los resultados ocultos

| Prueba | Qué protege |
|---|---|
| `tests/unit/test_outcome_reveal.py` | la condición única para mostrar resultados: gate abierto **y** registro oficial del veredicto; el registro se lee en cada petición y falla cerrado (archivo ausente, vacío, enorme, sin la línea `Veredicto:` o con el nombre del placebo) |
| `tests/unit/test_serializers.py` | la **lista blanca** de `shadow_trade_to_public_dict`: un campo nuevo no sale hasta que se agrega a mano |
| `tests/unit/test_paper_account_results_hidden.py` | `GET /trades` y `GET /positions` ocultan los resultados de la cuenta paper y siguen siendo una lista blanca |
| `tests/unit/test_telegram_commands.py`, `tests/unit/test_telegram_metrics.py`, `tests/unit/test_phase5_push.py`, `tests/unit/test_phase5_read_api.py` | los canales de lectura (comandos de Telegram, avisos push y API del dashboard) con la puerta cerrada y abierta |
| `tests/e2e/test_no_leaks.py` (con `tests/e2e/leaks.py`) | el **barrido** de punta a punta: tras cerrar operaciones por TP, SL, trailing y liquidación y una racha con post-mortem, recorre la API, las rutas heredadas, `/health`, Telegram, el push y el resumen diario buscando claves prohibidas, valores numéricos reales de resultados y etiquetas de texto. Su prueba de sensibilidad **abre la puerta y exige que sí encuentre cosas**: un barrido que no encuentra nada no prueba nada |
| `frontend/src/*.test.tsx` (Vitest) | el dashboard no muestra resultados aunque el JSON los trajera |

### La medición y el veredicto

`tests/unit/test_ai_value.py`, `test_ai_value_placebo.py`, `test_ai_value_report.py` y sus pruebas de
script fijan el criterio de decisión (conglomerados, bootstrap, placebo, las tres salidas, el punto de
análisis único y las guardas del gate); `test_measurement_status_script.py`, `test_measurement_uptime.py`
y `test_measurement_identity_script.py`, el avance y la identidad de la medición sin mostrar resultados.

### Solo paper

`tests/unit/test_paper_execution_isolation.py` comprueba por AST que **ningún módulo del bot importa
`bitunix_backend`** (la ejecución real) y que `app/main.py` construye un `PaperBackend` y nunca uno real;
también que el brazo ejecutado, si falla o se cuelga, no afecta al etiquetado ni al ciclo del scheduler y
que la medición es idéntica con el brazo encendido y apagado.

### El aislamiento de las piezas opcionales

`test_news_runtime_isolation.py`, `test_memory_isolation.py`, `test_telegram_runtime.py`,
`test_phase5_push.py` (y la prueba de aislamiento del brazo): cada pieza opcional está **apagada por
defecto**, construirla no lanza, corre en su **propia tarea**, un fallo suyo o un cuelgue no frena al
scheduler, al etiquetado ni al monitor, y su paquete no importa el núcleo de la medición ni la API de
Anthropic. Una pieza opcional nueva trae su propia prueba de aislamiento
([`RECETAS.md`](RECETAS.md)).

### Sin red

`tests/e2e/conftest.py::no_external_network` hace fallar la resolución de cualquier nombre que no sea de este
mismo equipo; `tests/e2e/test_isolation.py` comprueba que el candado bloquea cada servicio con el que habla
el bot y que un escenario completo no toca ninguno ni gasta nada. En las pruebas unitarias, la ausencia
de red depende de los falsos de cada prueba.

### Sin ventanas de consola y sin el Programador de tareas

| Prueba | Qué protege |
|---|---|
| `tests/unit/test_no_console_windows.py` | por AST, **todo** `subprocess.run/Popen/...` y `asyncio.create_subprocess_exec` de `app/`, `scripts/` y `tests/` lleva `creationflags=NO_WINDOW` (con casos que deben fallar); nada usa `DETACHED_PROCESS`, `CREATE_NEW_CONSOLE` ni `os.system`. El motivo está medido: el `python.exe` de un venv de uv es un trampolín de consola |
| `tests/unit/test_no_real_task_scheduler.py` | ninguna prueba toca el Programador de tareas **real** de Windows (`MinervaStartBot`, `MinervaHealthAlert`, `MinervaDailySummary`, `MinervaBackup`): la única vía es `$env:MINERVA_SCHTASKS_EXE`, que en las pruebas apunta siempre a un falso o a una trampa |

### La base real no se toca

`tests/unit/test_real_db_copy_safety.py` (el fixture `real_db_copy` jamás devuelve la ruta real y escribir en
la copia no cambia el original), `test_docker_mode_guards.py` (con la marca de la mudanza, las herramientas
que usarían la base nativa vieja por defecto se niegan) y `test_phase4_base.py` (la migración sobre una copia
de la base real no cambia nada más).

### Docker y configuración

`test_docker_setup.py` (puerto solo en loopback, `.env` fuera de la imagen, usuario sin privilegios, versiones
exactas, paridad de los interruptores del compose con `start_bot.ps1`), `test_docker_support.py`,
`test_docker_db_script.py` y `test_docker_migration_scripts.py`, y `test_env_example.py`:
`.env.example` tiene **todas** las variables de `Settings` (ni una más ni una menos), las credenciales
vacías, y copiarlo tal cual da exactamente los valores por defecto del código.

### La documentación

| Prueba | Qué protege |
|---|---|
| `tests/unit/test_public_docs.py` | la documentación pública: enlaces y anclas que resuelven, rutas del repositorio citadas que existen, variables de `.env.example` descritas, scripts y opciones citados que existen, tablas bien formadas, 22 rutas de la API, ningún dato personal |
| `tests/unit/test_final_docs.py` | el informe final y la guía de la mudanza: sin marcadores sin rellenar |
| `tests/unit/test_schema_doc.py` | `docs/ESQUEMA_BD.md` coincide con el DDL del código |
| `tests/unit/test_docstrings.py` | el formato de los docstrings de `app/` y `scripts/` |
| `tests/unit/test_build_dev_docs.py`, `tests/unit/test_dev_docs_facts.py` | el generador de la referencia, de los hechos derivados y del grafo |
| `tests/unit/test_dev_docs.py` | los documentos de `docs/dev/`: completos, con enlaces y rutas que resuelven y la referencia generada al día |
| `tests/unit/test_check_measurement_safety.py` | el script de zonas protegidas y que su lista coincide con los archivos y las variables reales y con [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md) |

### Higiene del repositorio

`tests/unit/test_repo_hygiene.py` recorre los archivos que git versionaría y falla ante un archivo que no debe
versionarse (credenciales, bases de datos, logs, claves, certificados), un patrón de credencial real (claves de
Anthropic, tokens de Telegram, hashes bcrypt, claves privadas...) o una asignación de un valor largo a una
variable de credencial; **nunca imprime el valor**, solo la regla, el archivo y la línea. También comprueba
que `.gitignore` cubre lo que no debe subirse y no oculta lo que sí.

## Lo que no hacen las pruebas

- No prueban contra servicios reales: los modos de fallo de Bitunix, CoinGecko, GDELT y Anthropic están
  simulados con lo que se leyó de su documentación, no observados.
- No prueban el contenedor ni la imagen (no hay demonio de Docker en la suite): el build y el arranque reales
  se verificaron a mano, según [`../INFORME_FINAL.md`](../INFORME_FINAL.md).
- No hay integración continua: nadie las corre por ti.
- Una prueba verde no demuestra que el cambio sea seguro para la medición: eso lo dice
  `python scripts/check_measurement_safety.py` y [`ZONAS_PROTEGIDAS.md`](ZONAS_PROTEGIDAS.md).
