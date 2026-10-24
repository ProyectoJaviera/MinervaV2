# Guía de `scripts/`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/scripts.html`](../api/scripts.html)

## Qué hace la carpeta

Las herramientas de línea de comandos: arrancar y detener el bot, respaldos y restauración, informes de la medición, el procedimiento del
veredicto, la mudanza a Docker, el backtest de la Fase 2 y las utilidades de desarrollo. **No forman parte del bot en ejecución** (salvo
`docker_support.py` y los scripts de respaldo, que viajan en la imagen). La guía de uso para quien opera el bot está en
[USO](../../USO.md#referencia-de-scripts); esta es para quien modifica el código.

## Reglas comunes (léelas antes de tocar un script)

- **Sobre una copia, nunca sobre la base de trabajo.** Todo script que lee la base real acepta `--db` y comprueba con un `assert` que la ruta
  **no** es `settings.database_path`. Los informes abren la copia con `mode=ro` (solo lectura) y no corren migraciones.
- **Ensayar con datos reales antes de entregar** (regla de CLAUDE.md): un script nuevo se corre contra una copia temporal de la base real; los
  tests con datos sintéticos no bastan (un fallo real de `scripts/analyze_risk.py` motivó la regla).
- **Medición ciega:** con la puerta cerrada los informes imprimen solo conteos y se niegan a mostrar resultados (`shadow_report.py`,
  `ai_value_report.py`, `lessons_report.py`, `real_money_report.py`). `--allow-synthetic-for-tests`, `--frozen-hash` y `--docs-dir` existen **solo**
  para pruebas con datos sintéticos; la bandera se rechaza con cualquier copia de la base real.
- **Códigos de salida documentados** en el docstring de cada script (0 ok, 2 uso, 3 rechazado o inexistente…); las pruebas los fijan.
- **`os.chdir` a la raíz del repositorio** antes de importar `app` (`telegram_alert.py`, `telegram_daily.py`, `resume_drawdown_stop.py`,
  `measurement_identity.py`): el Programador de tareas arranca en `System32` y `Settings()` busca `.env` en el directorio de trabajo.
- **Sin ventanas de consola:** todo `subprocess` lleva `creationflags=NO_WINDOW` y los `Start-Process` de PowerShell, `-WindowStyle Hidden`; lo vigila
  `tests/unit/test_no_console_windows.py`.
- **Ningún secreto** en la salida: tokens y contraseñas nunca se imprimen ni se escriben en logs.
- Todo script nuevo debe aparecer en [USO](../../USO.md#referencia-de-scripts) (lo exige `tests/unit/test_public_docs.py`) y cumplir el guardián de docstrings.

## Zona protegida

**Ningún script modifica la medición por sí mismo**, pero algunos son **parte del procedimiento**: el orden *copia → placebo → informe una sola vez* y la regla de
no crear a mano el registro oficial del veredicto. Ver [Gate, placebo y veredicto](../ZONAS_PROTEGIDAS.md#gate-placebo-y-veredicto). Los scripts de operación
(respaldos, avisos, mudanza) se pueden modificar sin afectar la medición, probando siempre con el proyecto `-p minerva-docs`, `--no-build` y un `.env` falso (nunca
`docker compose up/down/build/stop/restart` del proyecto `minerva`).

## Scripts por familia

| Familia | Scripts |
|---|---|
| Arranque y parada (Windows nativo) | `start_bot.ps1`, `stop_bot.ps1` |
| Mudanza a Docker | `migrate_to_docker.ps1`, `rollback_from_docker.ps1`, `docker_common.ps1`, `docker_support.py`, `docker_db.py`, `db_fingerprint.py` |
| Respaldos | `backup_db.py`, `restore_db.py` |
| Salud y avisos | `check_health.py`, `telegram_alert.py`, `telegram_daily.py` |
| Medición (solo conteos) | `measurement_status.py`, `measurement_identity.py`, `paper_execution_status.py`, `news_status.py` |
| Informes con puerta | `shadow_report.py`, `lessons_report.py`, `real_money_report.py` |
| Procedimiento del veredicto | `placebo_check.py`, `ai_value_report.py` |
| LLM (gastan API con `--real --confirm-real`) | `llm_smoke.py`, `llm_pilot.py` |
| Backtest de la Fase 2 | `download_history.py`, `run_backtest.py`, `analyze_risk.py`, `compare_fase2_runs.py`, `repair_ohlcv_gaps.py` |
| Control | `resume_drawdown_stop.py`, `hash_password.py` |
| Desarrollo | `run_dev_server.py`, `export_schema_doc.py`, `export_docker_requirements.py`, `build_dev_docs.py`, `dev_docs_facts.py`, `check_measurement_safety.py` |

## Archivos de PowerShell (sin hechos derivados)

### `scripts/start_bot.ps1`

**Responsabilidad.** Arranca el bot real nativo de un solo comando, sin consola visible. Fija **siempre** las mismas variables, sin importar lo que diga el `.env`:
`AUTO_OPEN_WITHOUT_LLM=false`, `LOG_LEVEL=INFO` y, **solo con `-Port 8000`**, `LLM_LIVE_LABELING_ENABLED`, `TELEGRAM_COMMANDS_ENABLED`, `PAPER_EXECUTION_ENABLED`,
`NEWS_COLLECTION_ENABLED` y `TELEGRAM_PUSH_ENABLED` en `true`; `LESSONS_MEMORY_ENABLED=false` siempre.

**Invariantes — no romper.**
- **Rehúsa** arrancar si algo ya escucha en `-Port` (dos procesos sobre la misma base SQLite) y si existe `data/MIGRADO_A_DOCKER.txt` (la base nativa sería una copia
  vieja y bifurcaría la medición).
- Los parámetros `-Port`, `-DatabasePath` y `-LogDir` existen para probar contra el puerto 8001 y una base temporal; un puerto distinto de 8000 apaga LLM, Telegram,
  brazo, noticias y avisos.

**Trampas conocidas.** `pythonw.exe` de un venv de uv es un trampolín **de consola**: se envuelve en `cmd.exe /c "... >> archivo 2>&1"` oculto con `-WindowStyle Hidden`.

### `scripts/stop_bot.ps1`

**Responsabilidad.** Detiene **solo** el proceso que escucha en `-Port` y su cadena de lanzadores (cmd.exe → `pythonw.exe` del `.venv` de este repositorio → proceso real).

**Invariantes — no romper.** Antes de tocar cualquier padre verifica con `Get-CimInstance Win32_Process` que existe, que empezó antes que su hijo y que su ejecutable o línea de comandos son los
de este repositorio y este puerto; un padre que no pasa se deja intacto. Al terminar confirma que no queda vivo ninguno de la cadena (mismo PID y misma hora de creación) y que el puerto
quedó libre (si no, sale con código 1).

**Trampas conocidas.** Es un corte **duro** (`Stop-Process -Force`): Windows no da una señal cooperativa para un proceso sin consola. SQLite en WAL lo tolera (a lo sumo se pierde la última
escritura). **Nunca** lo uses contra el bot real para probarlo: usa `-Port 8001`.

### `scripts/migrate_to_docker.ps1` y `scripts/rollback_from_docker.ps1`

**Responsabilidad.** Mudar el bot real de nativo a Docker (y volver), conservando la base y verificando que el volumen tiene las mismas filas en todas las tablas. Cada paso se registra en
`logs\mudanza_docker.log`; si algo falla desde la parada del bot se **deshace todo en orden inverso**.

**Invariantes — no romper.**
- Se niegan a correr fuera de los minutos :10–:40 de una hora UTC que no sea de cierre de vela de 4 h (00, 04, 08, 12, 16, 20): ahí las filas de la medición ya no están en vuelo.
- Tras la mudanza la base viva está en un volumen de Docker; la marca `data/MIGRADO_A_DOCKER.txt` protege la copia vieja.
- Con `-DryRun` solo hacen comprobaciones sin efectos; los parámetros de ensayo (`-NativePort`, `-DockerPort`, `-ProjectName`, `-EnvFile`…) existen para no tocar el bot real.

**Trampas conocidas.** Solo ASCII y compatibles con Windows PowerShell 5.1 (sin `??`, ternario ni `&&`): lo vigilan las pruebas.

### `scripts/docker_common.ps1`

**Responsabilidad.** Funciones compartidas por la mudanza y el retorno (registro, comprobaciones, ventana segura, huellas de la base, tareas programadas). **No se ejecuta solo**: se carga
con `. (Join-Path $PSScriptRoot "docker_common.ps1")`.

**Invariantes — no romper.** No llamar `ProjectName` a una variable de ámbito de script: sería la misma que el parámetro `-ProjectName` de quien lo carga. Las variables de entorno
`MINERVA_DOCKER_EXE`, `MINERVA_SCHTASKS_EXE`, `MINERVA_STOP_BOT_SCRIPT`, `MINERVA_START_BOT_SCRIPT` y `MINERVA_ASSUME_NOW_UTC` **solo existen para las pruebas**.

**Trampas conocidas.** Ninguna conocida.

## Archivos de Python

### `scripts/ai_value_report.py`

**Responsabilidad.** El informe del veredicto del valor de la IA sobre una **copia** (solo lectura). Se niega mientras el gate esté cerrado o el N efectivo limpio sea < `MIN_EFFECTIVE_N`.

**Funciones y clases clave.**
- `main()` — construye y muestra el informe; con `--out` lo guarda **sin sobrescribir** y rechaza si ya existe **un** registro oficial de cualquier fecha (solo puede haber uno).

**Invariantes — no romper.** Códigos de salida: 0 veredicto impreso; 3 rechazado (gate cerrado o punto no alcanzado); 4 bandera de pruebas rechazada; 5 `--out` rechazado. Procedimiento completo en
[VEREDICTO_3_6](../../VEREDICTO_3_6.md).

**Trampas conocidas.** Una sola mirada: volver a correrlo con más datos **no** es un veredicto nuevo.

<!-- hechos:scripts/ai_value_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/ai_value_report.html`](../api/scripts/ai_value_report.html)
- **Importa (internos):** `app.config`, `app.trading.ai_value_report`, `app.trading.measurement_status`, `app.trading.shadow_live_labeler`, `app.trading.verdict_record`, `scripts.measurement_status`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/synthetic_measurement.py`, `tests/unit/test_ai_value_report_script.py`
<!-- /hechos:scripts/ai_value_report.py -->

### `scripts/analyze_risk.py`

**Responsabilidad.** El análisis de riesgo de ruina (bootstrap) sobre las operaciones OOS del backtest real; escribe el informe de riesgo de la Fase 2.

**Funciones y clases clave.**
- `main()`.

**Invariantes — no romper.** Solo lee SQLite (no toca la red); corre **después** de `run_backtest.py`. Ver [backtesting](backtesting.md#appbacktestingrisk_analysispy) para las simplificaciones.

**Trampas conocidas.**
- *Fallo real* con la base real (grilla con un tope de 30 % que dejaba 0 operaciones) que motivó la regla de ensayar con datos reales.
- La constante `REPORT_PATH` apunta a la ruta **anterior** a la reorganización de `docs/` (el informe estaba en la raíz de `docs`; hoy vive en `docs/historial/`): al correrlo
  de nuevo escribiría un archivo nuevo en la raíz en vez de actualizar el histórico.

<!-- hechos:scripts/analyze_risk.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/analyze_risk.html`](../api/scripts/analyze_risk.html)
- **Importa (internos):** `app.backtesting.metrics`, `app.backtesting.risk_analysis`, `app.config`, `app.core.logging`, `app.persistence.database`, `app.persistence.repositories.backtest_repo`, `app.strategies.registry`
- **Lo importan:** ninguno
- **Configuración que lee:** `BACKTEST_INITIAL_CAPITAL`, `DATABASE_PATH`
- **Tablas que lee:** `backtest_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_analyze_risk_script.py`, `tests/unit/test_risk_analysis.py`
<!-- /hechos:scripts/analyze_risk.py -->

### `scripts/backup_db.py`

**Responsabilidad.** El respaldo **consistente** de la base (API de backup de SQLite con el origen en solo lectura): seguro con el bot corriendo en WAL.

**Funciones y clases clave.**
- `run()` — copia a un nombre temporal, verifica con `PRAGMA integrity_check` y solo entonces le da el nombre definitivo `<prefijo>_AAAAMMDD_HHMMSS.db` (UTC); `prune()` (`--keep N`) borra los
  más antiguos con el mismo prefijo.

**Invariantes — no romper.** Un respaldo a medias o corrupto nunca queda con aspecto de bueno; **nunca** imprime resultados. Con la marca de la mudanza se niega a operar sobre la base por defecto.

**Trampas conocidas.** Copiar el archivo a mano con el bot corriendo puede dejar una copia inconsistente. Códigos: 0 ok; 2 uso; 3 origen inexistente; 4 copia inválida; 5 destino existente.

<!-- hechos:scripts/backup_db.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/backup_db.html`](../api/scripts/backup_db.html)
- **Importa (internos):** `scripts.docker_support`
- **Lo importan:** `scripts.docker_db`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_backup_restore_scripts.py`, `tests/unit/test_docker_db_script.py`, `tests/unit/test_docker_mode_guards.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_docker_support.py`, `tests/unit/test_public_docs.py`
<!-- /hechos:scripts/backup_db.py -->

### `scripts/build_dev_docs.py`

**Responsabilidad.** Genera la referencia pdoc de `app/` y `scripts/`, completa los hechos derivados del código de estas guías y actualiza el grafo de paquetes.

**Funciones y clases clave.**
- `main()` (`--check`, `--strict`, `--force`), `api_problems()`, `write_reference()`, `documentation_surface()`, `sync_guides()` y `sync_graph()`.

**Invariantes — no romper.** pdoc **importa** los módulos: por eso corre en un proceso aparte con el directorio de trabajo vacío, el entorno limpio y `os.chdir` anulado; nunca lee `.env`. Cómo usarlo: [ENTORNO_DESARROLLO](../ENTORNO_DESARROLLO.md#documentación-técnica).

**Trampas conocidas.** `PYTHONHASHSEED=0` fija el orden de los `frozenset` que pdoc imprime tal cual; sin él la referencia cambiaría de una corrida a otra.

<!-- hechos:scripts/build_dev_docs.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/build_dev_docs.html`](../api/scripts/build_dev_docs.html)
- **Importa (internos):** `app.config`, `scripts.dev_docs_facts`, `scripts.docker_support`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_build_dev_docs.py`, `tests/unit/test_docstrings.py`
<!-- /hechos:scripts/build_dev_docs.py -->

### `scripts/check_health.py`

**Responsabilidad.** Consulta `GET /health` con timeout corto y reporta `OK` (0), `STALE` (1) o `SIN_RESPUESTA` (2), para un cron o Programador de tareas **externo** al bot.

**Funciones y clases clave.**
- `fetch_health()` y `main()`.

**Invariantes — no romper.** `/health` no exige login a propósito, para que esta herramienta lo consulte sin sesión. `telegram_alert.py` la reutiliza tal cual.

**Trampas conocidas.** Un bot pausado a propósito también sale `STALE`.

<!-- hechos:scripts/check_health.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/check_health.html`](../api/scripts/check_health.html)
- **Importa (internos):** ninguno
- **Lo importan:** `scripts.telegram_alert`, `scripts.telegram_daily`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_dev_docs_facts.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_telegram_alert_script.py`, `tests/unit/test_telegram_daily_script.py`
<!-- /hechos:scripts/check_health.py -->

### `scripts/compare_fase2_runs.py`

**Responsabilidad.** Compara dos corridas del backtest de la Fase 2 guardadas en bases distintas y escribe tablas en Markdown (descartes por tope de SL, veredictos lado a lado, simulación de cartera y ritmo de operaciones OOS).

**Funciones y clases clave.**
- `build_report()` y `main()`.

**Invariantes — no romper.** Solo lee ambas bases en modo solo lectura; correr sobre **copias**.

**Trampas conocidas.** Herramienta histórica de la reejecución de la Fase 2.

<!-- hechos:scripts/compare_fase2_runs.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/compare_fase2_runs.html`](../api/scripts/compare_fase2_runs.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** `backtest_skipped_entries`, `backtest_trades`, `backtest_verdicts`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:scripts/compare_fase2_runs.py -->

### `scripts/db_fingerprint.py`

**Responsabilidad.** La huella de una base: integridad, modo del diario, hash del esquema y filas por tabla, en **una línea JSON**.

**Funciones y clases clave.**
- `fingerprint()`, `differences()` y `main()`.

**Invariantes — no romper.** Solo cuenta filas y resume el esquema: nunca lee resultados, así que es seguro con la medición ciega. Abre la base en solo lectura y no corre migraciones.

**Trampas conocidas.** La mudanza la usa para demostrar que el volumen de Docker tiene las mismas filas que el origen.

<!-- hechos:scripts/db_fingerprint.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/db_fingerprint.html`](../api/scripts/db_fingerprint.html)
- **Importa (internos):** `scripts.restore_db`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_docker_db_script.py`, `tests/unit/test_docker_migration_scripts.py`, `tests/unit/test_docker_setup.py`
<!-- /hechos:scripts/db_fingerprint.py -->

### `scripts/dev_docs_facts.py`

**Responsabilidad.** Calcula con `ast` los hechos de las guías (imports, configuración, tablas, pruebas) y el grafo de paquetes. **Nunca importa `app`.**

**Funciones y clases clave.**
- `collect_facts()`, `render_facts()`, `render_graph()`, `table_facts()` y `tests_by_module()`.

**Invariantes — no romper.** Su alcance está declarado en el docstring del módulo y se repite en las guías (por ejemplo, las tablas son las del SQL del archivo y las de las funciones de repositorio que llama).

**Trampas conocidas.** Es una pieza compartida: no se ejecuta sola.

<!-- hechos:scripts/dev_docs_facts.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/dev_docs_facts.html`](../api/scripts/dev_docs_facts.html)
- **Importa (internos):** ninguno
- **Lo importan:** `scripts.build_dev_docs`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_dev_docs_facts.py`
<!-- /hechos:scripts/dev_docs_facts.py -->

### `scripts/docker_db.py`

**Responsabilidad.** La base del bot cuando corre en Docker: `backup` (respaldo en el volumen y en el host), `export` (una copia verificada) y `run <informe>.py` (copia fresca → informe → la copia se borra).

**Funciones y clases clave.**
- `cmd_backup()`, `cmd_export()`, `cmd_run()` y `build_parser()`.

**Invariantes — no romper.** La copia se hace con la API de backup **dentro del contenedor** y se verifica su SHA-256 al traerla al host; los informes corren en el host sobre esa copia y **nunca** tocan la base viva.

**Trampas conocidas.** Las opciones propias de `run` (`--keep-copy`) van **antes** del nombre del script. Códigos: 0 ok; 2 uso; 5 fallo de Docker o de la copia.

<!-- hechos:scripts/docker_db.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/docker_db.html`](../api/scripts/docker_db.html)
- **Importa (internos):** `scripts.backup_db`, `scripts.docker_support`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/support/fake_docker.py`, `tests/unit/test_docker_db_script.py`, `tests/unit/test_docker_mode_guards.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_public_docs.py`
<!-- /hechos:scripts/docker_db.py -->

### `scripts/docker_support.py`

**Responsabilidad.** Soporte para operar con el bot en Docker: la **marca de la mudanza** (`data/MIGRADO_A_DOCKER.txt`) y sacar una copia consistente de la base del volumen.

**Funciones y clases clave.**
- `docker_mode_active()` / `docker_mode_refusal()`, `run_docker()` / `compose()` (nunca usan el shell ni esperan una terminal), `export_db_copy()` (respaldo dentro del contenedor, `docker compose cp` y SHA-256),
  `run_streaming()` (sin ventana de consola) y `NO_WINDOW`.

**Invariantes — no romper.** Solo biblioteca estándar y viaja en la imagen; dentro del contenedor la marca nunca existe, así que allí no cambia nada. `compose()` no interpola el `.env` real.

**Trampas conocidas.** Mientras exista la marca, `data/minerva_estabilidad.db` es una copia **vieja**: las herramientas que la usarían por defecto se niegan a tocarla en silencio (pasar `--db` lo esquiva a propósito).

<!-- hechos:scripts/docker_support.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/docker_support.html`](../api/scripts/docker_support.html)
- **Importa (internos):** ninguno
- **Lo importan:** `scripts.backup_db`, `scripts.build_dev_docs`, `scripts.check_measurement_safety`, `scripts.docker_db`, `scripts.restore_db`, `scripts.resume_drawdown_stop`, `scripts.telegram_daily`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_docker_db_script.py`, `tests/unit/test_docker_mode_guards.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_docker_support.py`
<!-- /hechos:scripts/docker_support.py -->

### `scripts/download_history.py`

**Responsabilidad.** Descarga **todo** lo que necesita el backtest de la Fase 2 (universo, especificaciones, velas `LAST_PRICE`/`MARK_PRICE` y funding) y lo deja en SQLite.

**Funciones y clases clave.**
- `main()`.

**Invariantes — no romper.** Es idempotente y reanudable: interrumpirlo con Ctrl+C y volver a correrlo no pierde progreso ni vuelve a pedir lo ya guardado. Marca cada serie completa **solo** si llegó al final sin errores.

**Trampas conocidas.** Existe separado de `run_backtest.py` porque antes la descarga y el cálculo estaban entrelazados y no se distinguía «calculando» de «esperando la red».

<!-- hechos:scripts/download_history.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/download_history.html`](../api/scripts/download_history.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.market.bitunix_rest`, `app.market.coingecko_client`, `app.market.contract_specs`, `app.market.funding_history`, `app.market.ohlcv_history`, `app.market.universe`, `app.persistence.database`, `app.persistence.repositories.ohlcv_repo`, `app.strategies.registry`
- **Lo importan:** ninguno
- **Configuración que lee:** `BITUNIX_RATE_LIMIT_PER_SEC`, `BITUNIX_REST_BASE_URL`, `COINGECKO_API_KEY`, `COINGECKO_BASE_URL`, `DATABASE_PATH`, `backtest_control_symbols_list`
- **Tablas que lee:** `ohlcv_cache`, `ohlcv_floor`
- **Tablas que escribe:** `ohlcv_series_complete`
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_download_history.py`
<!-- /hechos:scripts/download_history.py -->

### `scripts/export_docker_requirements.py`

**Responsabilidad.** Genera `docker/requirements.lock`: las versiones **exactas** de las dependencias de ejecución de la imagen, tomadas del entorno que pasó las pruebas.

**Funciones y clases clave.**
- `collect_closure()` (clausura de `[project].dependencies` evaluada para Linux), `render_lock()` y `main()` (`--check`).

**Invariantes — no romper.** Un paquete exigido para Linux que no está instalado aquí (como `uvloop`) se omite y se informa: no está en el conjunto probado, así que no se agrega a ciegas.

**Trampas conocidas.** `pyproject.toml` solo fija rangos; sin este archivo una imagen construida meses después instalaría versiones que nadie probó.

<!-- hechos:scripts/export_docker_requirements.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/export_docker_requirements.html`](../api/scripts/export_docker_requirements.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_docker_setup.py`
<!-- /hechos:scripts/export_docker_requirements.py -->

### `scripts/export_schema_doc.py`

**Responsabilidad.** Genera `docs/ESQUEMA_BD.md` a partir del DDL real (`Database.connect()` sobre una base en memoria), no a mano.

**Funciones y clases clave.**
- `build_document()`, `read_schema()`, `render()` y `main()` (`--check`).

**Invariantes — no romper.** Una tabla nueva sin grupo en `GROUPS` hace fallar la generación (hay que decidir dónde va). El texto de cada tabla es el comentario SQL (`-- ...`) que precede a su `CREATE TABLE`.

**Trampas conocidas.** Los comentarios del DDL citan rutas viejas de documentos movidos a `docs/historial/`; `modernize_doc_paths` las reescribe al generar.

<!-- hechos:scripts/export_schema_doc.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/export_schema_doc.html`](../api/scripts/export_schema_doc.html)
- **Importa (internos):** `app.persistence.database`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_schema_doc.py`
<!-- /hechos:scripts/export_schema_doc.py -->

### `scripts/hash_password.py`

**Responsabilidad.** Genera el hash bcrypt de la contraseña para `AUTH_PASSWORD_HASH`, de **uso único**.

**Funciones y clases clave.**
- `build_hash_line()`.

**Invariantes — no romper.** Pide la contraseña con `getpass` (nunca como argumento, que quedaría en el historial de la shell), imprime solo el hash **entre comillas simples** y nunca toca `.env`.

**Trampas conocidas.** Un hash bcrypt lleva signos `$`; Docker Compose los interpretaría como variables si no van entre comillas simples. Límite de bcrypt: 72 bytes.

<!-- hechos:scripts/hash_password.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/hash_password.html`](../api/scripts/hash_password.html)
- **Importa (internos):** `app.api.security`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_hash_password_script.py`
<!-- /hechos:scripts/hash_password.py -->

### `scripts/lessons_report.py`

**Responsabilidad.** El informe de la memoria de lecciones sobre una copia, **sin mostrar nada** mientras `is_outcome_reveal_allowed` sea falso.

**Funciones y clases clave.**
- `main()`.

**Invariantes — no romper.** Comprueba la puerta con una conexión de solo lectura **antes** de abrir la base para escribir; con la puerta abierta construye la memoria y las lecciones **en la copia**. Códigos: 0 informe; 3 resultados ocultos; 4 bandera rechazada.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:scripts/lessons_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/lessons_report.html`](../api/scripts/lessons_report.html)
- **Importa (internos):** `app.config`, `app.memory.gate`, `app.memory.report`, `app.persistence.database`, `app.trading.ai_value_report`, `app.trading.measurement_status`, `app.trading.shadow_live_labeler`, `scripts.measurement_status`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_lessons_report_script.py`
<!-- /hechos:scripts/lessons_report.py -->

### `scripts/llm_pilot.py`

**Responsabilidad.** El piloto por repetición sobre señales ya guardadas en una copia: hasta `--max-calls` (30 por defecto, **tope duro 50**) grupos sin fila en `llm_logs`.

**Funciones y clases clave.**
- `main()` — en seco por defecto (cliente falso, sin red ni coste); una llamada real exige `--real` **y** `--confirm-real` a la vez. Reporta validez del JSON, tokens y latencia (media y p95), tasa de APROBAR frente a la banda 15 %–85 % y coste real.

**Invariantes — no romper.** Copia obligatoria (`--db`). **Escribe en la copia** filas de `llm_logs` con `fase='PILOTO'` y la etiqueta de las sombras de la copia.

**Trampas conocidas.** Gasta API de verdad con `--real --confirm-real`; compara el coste impreso con la consola de Anthropic antes de confiar en el número.

<!-- hechos:scripts/llm_pilot.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/llm_pilot.html`](../api/scripts/llm_pilot.html)
- **Importa (internos):** `app.config`, `app.llm.client`, `app.llm.decision_service`, `app.llm.prompts`, `app.persistence.database`, `app.persistence.repositories.llm_logs_repo`, `app.persistence.repositories.shadow_repo`
- **Lo importan:** ninguno
- **Configuración que lee:** `ANTHROPIC_SONNET_MODEL`, `DATABASE_PATH`
- **Tablas que lee:** `llm_logs`, `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:scripts/llm_pilot.py -->

### `scripts/llm_smoke.py`

**Responsabilidad.** Una llamada mínima (1 a 3, tope duro 3) para verificar clave, modelo y precio con un gasto de céntimos.

**Funciones y clases clave.**
- `main()` — misma protección que el piloto (en seco por defecto, `--real --confirm-real` juntos, copia obligatoria); imprime el mensaje, estado, etiqueta, tokens, coste, latencia y respuesta cruda.

**Invariantes — no romper.** Nunca imprime `ANTHROPIC_API_KEY` ni ningún secreto.

**Trampas conocidas.** Gasta API con `--real --confirm-real`.

<!-- hechos:scripts/llm_smoke.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/llm_smoke.html`](../api/scripts/llm_smoke.html)
- **Importa (internos):** `app.config`, `app.llm.client`, `app.llm.decision_service`, `app.llm.prompts`, `app.persistence.database`, `app.persistence.repositories.llm_logs_repo`, `app.persistence.repositories.shadow_repo`
- **Lo importan:** ninguno
- **Configuración que lee:** `ANTHROPIC_SONNET_MODEL`, `DATABASE_PATH`
- **Tablas que lee:** `llm_logs`, `shadow_trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:scripts/llm_smoke.py -->

### `scripts/measurement_identity.py`

**Responsabilidad.** Comprueba que la medición usa **un solo** modelo, el prompt congelado y una sola versión de *features*.

**Funciones y clases clave.**
- `load_groups()`, `assess()` y `main()`.

**Invariantes — no romper.** El hash congelado cubre el prompt y las *features* pero **no el modelo**: esta herramienta es la única guarda del modelo. Avisa si hay más de una combinación, si alguna no es la del prompt congelado o si el modelo configurado **ahora** no es el que etiquetó.
Solo imprime conteos y fechas. Códigos: 0 identidad única y congelada (o sin filas); 1 anomalía; 2 uso; 3 la base no existe.

**Trampas conocidas.** Se corre sobre una copia (`scripts/backup_db.py`).

<!-- hechos:scripts/measurement_identity.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/measurement_identity.html`](../api/scripts/measurement_identity.html)
- **Importa (internos):** `app.config`, `app.trading.shadow_live_labeler`
- **Lo importan:** ninguno
- **Configuración que lee:** `ANTHROPIC_SONNET_MODEL`, `DATABASE_PATH`
- **Tablas que lee:** `llm_logs`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_measurement_identity_script.py`
<!-- /hechos:scripts/measurement_identity.py -->

### `scripts/measurement_status.py`

**Responsabilidad.** El avance de la medición sobre una copia (solo lectura): wrapper fino de `app.trading.measurement_status`.

**Funciones y clases clave.**
- `format_report()` y `main()`.

**Invariantes — no romper.** Nunca imprime PnL ni resultados; abre la copia con `mode=ro` (`_connect_ro`, que otros scripts reutilizan).

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:scripts/measurement_status.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/measurement_status.html`](../api/scripts/measurement_status.html)
- **Importa (internos):** `app.config`, `app.trading.ai_value`, `app.trading.measurement_status`
- **Lo importan:** `scripts.ai_value_report`, `scripts.lessons_report`, `scripts.news_status`, `scripts.paper_execution_status`, `scripts.placebo_check`, `scripts.real_money_report`
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_docker_setup.py`, `tests/unit/test_measurement_status_script.py`, `tests/unit/test_measurement_uptime.py`
<!-- /hechos:scripts/measurement_status.py -->

### `scripts/news_status.py`

**Responsabilidad.** El estado de la recolección de noticias sobre una copia: salud por fuente, titulares, Fear & Greed y atribuciones.

**Funciones y clases clave.**
- `format_report()` y `main()` (`--last`).

**Invariantes — no romper.** Sin PnL ni resultados; las atribuciones de las fuentes acompañan cualquier muestra de sus datos.

**Trampas conocidas.** Una copia anterior a la Fase 4 no tiene `news_items`: lo dice y termina.

<!-- hechos:scripts/news_status.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/news_status.html`](../api/scripts/news_status.html)
- **Importa (internos):** `app.config`, `app.news.sentiment`, `app.news.sources`, `scripts.measurement_status`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** `data_source_health`, `fear_greed`, `news_fetch_state`, `news_items`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_news_status_script.py`
<!-- /hechos:scripts/news_status.py -->

### `scripts/paper_execution_status.py`

**Responsabilidad.** El estado del brazo ejecutado sobre una copia: cuándo se armó, cuántas APROBADA se abrieron u omitieron (por motivo) y la calidad de la entrada.

**Funciones y clases clave.**
- `format_report()` y `main()` (`--last`).

**Invariantes — no romper.** Sin PnL: solo la decisión de ejecución y la calidad de la entrada (retraso y diferencia de precio frente a la sombra), que se miden al abrir, no al cerrar.

**Trampas conocidas.** Sirve para verificar un despliegue: esperar muchas filas `SKIPPED/RISK_STRATEGY_NOT_ELIGIBLE`.

<!-- hechos:scripts/paper_execution_status.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/paper_execution_status.html`](../api/scripts/paper_execution_status.html)
- **Importa (internos):** `app.config`, `scripts.measurement_status`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** `paper_execution_log`, `system_state`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_paper_execution_status_script.py`
<!-- /hechos:scripts/paper_execution_status.py -->

### `scripts/placebo_check.py`

**Responsabilidad.** La calibración por placebo del criterio de valor, con los **mismos filtros** que el veredicto.

**Funciones y clases clave.**
- `main()` — `--n-repeats` (1.000 de diseño; unos 11 min), `--out` (guarda el informe sin sobrescribir).

**Invariantes — no romper.** Puede correr antes de que el gate se abra (no revela nada de la IA) y debe registrarse **antes** de la primera decisión real. Códigos: 0 calibrado; 3 nada que calibrar; 4 bandera rechazada; 5 `--out` existe; 6 no calibrado; 7 no informativo.

**Trampas conocidas.** Un resultado «no informativo» **no** corre el veredicto: se esperan 7 días de datos.

<!-- hechos:scripts/placebo_check.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/placebo_check.html`](../api/scripts/placebo_check.html)
- **Importa (internos):** `app.config`, `app.trading.ai_value`, `app.trading.ai_value_placebo`, `app.trading.ai_value_report`, `app.trading.shadow_live_labeler`, `scripts.measurement_status`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_placebo_check_script.py`
<!-- /hechos:scripts/placebo_check.py -->

### `scripts/real_money_report.py`

**Responsabilidad.** El reporte de paso a dinero real sobre una copia. **Solo recomienda**: no importa ningún backend de ejecución y no usa la red.

**Funciones y clases clave.**
- `main()` — `--log-file` (cuenta las líneas CRITICAL «Reconciliacion fallida» del log), `--out` (solo si hay conclusión).

**Invariantes — no romper.** No muestra ningún resultado salvo que `is_outcome_reveal_allowed` sea verdadero. Un `--out` con el nombre del registro oficial es un error de uso (2). Códigos: 0 informe impreso; 3 resultados ocultos; 4 bandera rechazada; 5 `--out` existe.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:scripts/real_money_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/real_money_report.html`](../api/scripts/real_money_report.html)
- **Importa (internos):** `app.config`, `app.trading.ai_value_report`, `app.trading.real_money_report`, `app.trading.shadow_live_labeler`, `app.trading.verdict_record`, `scripts.measurement_status`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_real_money_report_script.py`
<!-- /hechos:scripts/real_money_report.py -->

### `scripts/repair_ohlcv_gaps.py`

**Responsabilidad.** Repara huecos puntuales en `ohlcv_cache` de una copia, para un símbolo, intervalo y tipo de precio, con pedidos estrechos (`find_gaps` / `fill_gaps`).

**Funciones y clases clave.**
- `repair_one()` y `main()`.

**Invariantes — no romper.** Escribe en la copia y pide velas públicas a Bitunix. Nació del incidente del 2026-10-10 (Bitunix omite a veces una vela en una respuesta ancha).

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:scripts/repair_ohlcv_gaps.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/repair_ohlcv_gaps.html`](../api/scripts/repair_ohlcv_gaps.html)
- **Importa (internos):** `app.config`, `app.market.bitunix_rest`, `app.market.ohlcv_history`, `app.persistence.database`, `app.persistence.repositories.ohlcv_repo`
- **Lo importan:** ninguno
- **Configuración que lee:** `BITUNIX_RATE_LIMIT_PER_SEC`, `BITUNIX_REST_BASE_URL`, `DATABASE_PATH`
- **Tablas que lee:** `ohlcv_cache`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:scripts/repair_ohlcv_gaps.py -->

### `scripts/restore_db.py`

**Responsabilidad.** Restaura un respaldo en la ruta configurada, con el bot **detenido**; lo que se usa para mudar el bot de equipo o a Docker sin perder la medición.

**Funciones y clases clave.**
- `run()` — verifica el respaldo (integridad y tablas de Minerva), se niega a tocar un destino existente salvo `--force` (que lo **mueve** a `<nombre>.previo_AAAAMMDD_HHMMSS` con sus `-wal`/`-shm`) y reemplaza de forma atómica.

**Invariantes — no romper.** Nunca con el proceso escribiendo la base. Códigos: 0 ok; 2 uso; 3 el respaldo no existe; 4 el respaldo no es válido; 5 el destino existe; 6 no se pudo escribir (la base anterior queda como estaba).

**Trampas conocidas.** Con la marca de la mudanza, restaurar sobre la base por defecto no cambiaría la del bot: se niega.

<!-- hechos:scripts/restore_db.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/restore_db.html`](../api/scripts/restore_db.html)
- **Importa (internos):** `scripts.docker_support`
- **Lo importan:** `scripts.db_fingerprint`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/support/fake_docker.py`, `tests/unit/test_backup_restore_scripts.py`, `tests/unit/test_docker_migration_scripts.py`, `tests/unit/test_docker_mode_guards.py`, `tests/unit/test_docker_setup.py`
<!-- /hechos:scripts/restore_db.py -->

### `scripts/resume_drawdown_stop.py`

**Responsabilidad.** Levanta **a mano** el stop por drawdown (modo `duro`) del motor de riesgo; ni Telegram ni el dashboard lo hacen.

**Funciones y clases clave.**
- `run()` — sin `--confirm` solo muestra el estado; con `--confirm` solo actúa si el stop está activo (código 3 si no) y reinicia el pico de equity, pero **no** el contador del criterio de dinero real.

**Invariantes — no romper.** Nunca imprime el saldo ni el equity mientras la puerta de resultados esté cerrada. Se puede correr con el bot en marcha (solo escribe claves de `system_state`).

**Trampas conocidas.** Con la marca de la mudanza y sin `--db` se niega: la base por defecto es una copia vieja. Dentro del contenedor: `docker compose exec minerva python scripts/resume_drawdown_stop.py --confirm`.

<!-- hechos:scripts/resume_drawdown_stop.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/resume_drawdown_stop.html`](../api/scripts/resume_drawdown_stop.html)
- **Importa (internos):** `app.config`, `app.persistence.database`, `app.persistence.repositories.system_state_repo`, `app.trading.reveal`, `app.trading.risk_engine`, `scripts.docker_support`
- **Lo importan:** ninguno
- **Configuración que lee:** `INITIAL_CAPITAL_USDT`
- **Tablas que lee:** `system_state`, `trades`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_docker_mode_guards.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_resume_drawdown_stop_script.py`
<!-- /hechos:scripts/resume_drawdown_stop.py -->

### `scripts/run_backtest.py`

**Responsabilidad.** Ejecuta el backtest completo de la Fase 2 y persiste todo en `backtest_*`.

**Funciones y clases clave.**
- `main()` — comprueba **antes de calcular** que están todas las series necesarias y, si falta alguna, las lista y termina.

**Invariantes — no romper.** Ya no toca la red: lee de SQLite. **No tiene `--db`: usa `settings.database_path`** y **borra** los resultados de corridas anteriores; apúntalo a una copia con `DATABASE_PATH` y nunca lo corras contra la base del bot.

**Trampas conocidas.** Los criterios y parámetros están fijados y commiteados antes de la corrida; no se modifican tras ver los resultados.

<!-- hechos:scripts/run_backtest.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/run_backtest.html`](../api/scripts/run_backtest.html)
- **Importa (internos):** `app.backtesting.report`, `app.config`, `app.core.logging`, `app.market.ohlcv_history`, `app.persistence.database`, `app.persistence.repositories.universe_repo`, `app.strategies.registry`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`, `backtest_control_symbols_list`, `backtest_official_end_ms`
- **Tablas que lee:** `asset_universe`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_run_backtest_script.py`
<!-- /hechos:scripts/run_backtest.py -->

### `scripts/run_dev_server.py`

**Responsabilidad.** El servidor de desarrollo en el puerto 8001 contra una **copia temporal** de una base real (`--source-db`).

**Funciones y clases clave.**
- `main()` — copia la base con la API de backup, fija el entorno **antes** de importar `app.*` y lanza uvicorn.

**Invariantes — no romper.** Candados **en el código**, no dependen del `.env`: apaga el etiquetado del LLM (y vacía la clave), los comandos y el push de Telegram (y vacía el token), el brazo ejecutado, las noticias y la memoria. Nunca toca el bot del puerto 8000 ni su base.

**Trampas conocidas.** El orden importa: `Settings()` se crea al importar y lee el entorno en ese momento. Las llamadas públicas del arranque (CoinGecko, Bitunix) son de solo lectura y sin costo.

<!-- hechos:scripts/run_dev_server.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/run_dev_server.html`](../api/scripts/run_dev_server.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_docker_setup.py`, `tests/unit/test_paper_execution_base.py`, `tests/unit/test_phase4_base.py`, `tests/unit/test_phase5_base.py`, `tests/unit/test_telegram_runtime.py`
<!-- /hechos:scripts/run_dev_server.py -->

### `scripts/shadow_report.py`

**Responsabilidad.** Imprime el reporte de sombras por estrategia, o se **niega** mientras `is_outcome_reveal_allowed` sea falso (código 3).

**Funciones y clases clave.**
- `main()`.

**Invariantes — no romper.** Abre la base con `Database` (puede aplicar migraciones): solo sobre una copia. Incluye PnL por estrategia, así que verlo antes del punto de análisis contaminaría la medición.

**Trampas conocidas.** Para el veredicto se usa `ai_value_report.py`, no este.

<!-- hechos:scripts/shadow_report.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/shadow_report.html`](../api/scripts/shadow_report.html)
- **Importa (internos):** `app.config`, `app.persistence.database`, `app.trading.ai_value`, `app.trading.measurement_status`, `app.trading.shadow_report`
- **Lo importan:** ninguno
- **Configuración que lee:** `DATABASE_PATH`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_shadow_report_script.py`
<!-- /hechos:scripts/shadow_report.py -->

### `scripts/telegram_alert.py`

**Responsabilidad.** La alerta externa de caída por Telegram: sondea `GET /health` (con `check_health.py`) y avisa si el bot real está `STALE` o `SIN_RESPUESTA`.

**Funciones y clases clave.**
- `run()` — avisa en un **cambio** de estado (la recuperación incluida) y, mientras siga caído, con un recordatorio cada `--reminder-minutes` (60); `send_telegram_message()`.

**Invariantes — no romper.** El archivo de estado (`--state-file`, JSON) **solo** se actualiza tras un envío exitoso: si falla, el siguiente sondeo reintenta el aviso pendiente. Token y chat nunca se imprimen ni se registran. Corre en un proceso **separado** del bot.

**Trampas conocidas.** Se registra como tarea programada con `conhost.exe --headless`: el `pythonw.exe` de un venv de uv abre una consola visible. `run` devuelve si el **pipeline de alertas** funcionó, no el estado del bot.

<!-- hechos:scripts/telegram_alert.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/telegram_alert.html`](../api/scripts/telegram_alert.html)
- **Importa (internos):** `app.config`, `scripts.check_health`
- **Lo importan:** `scripts.telegram_daily`
- **Configuración que lee:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_telegram_alert_script.py`
<!-- /hechos:scripts/telegram_alert.py -->

### `scripts/telegram_daily.py`

**Responsabilidad.** El resumen diario por Telegram del bot real y de la medición: **un** mensaje al día, siempre.

**Funciones y clases clave.**
- `run()` y `build_message()`; `collect_snapshot()` / `collect_snapshot_from_docker()` — los números salen de una **copia de solo lectura** de la base hecha en un directorio temporal del sistema (nunca dentro de `data/`) y borrada al terminar.

**Invariantes — no romper.** **Nunca PnL ni resultados.** La base por defecto es `data/minerva_estabilidad.db`, no `settings.database_path`; con la marca de la mudanza y sin `--db` la copia sale del contenedor. Un bot caído o una base ilegible se informan dentro del mensaje: no son un fallo del resumen.

**Trampas conocidas.** `--dry-run` imprime sin enviar. `/health` solo da contadores «de hoy» y `/api/measurement/status` exige sesión, de ahí la copia.

<!-- hechos:scripts/telegram_daily.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/telegram_daily.html`](../api/scripts/telegram_daily.html)
- **Importa (internos):** `app.config`, `app.trading.ai_value`, `app.trading.measurement_status`, `app.trading.shadow_live_labeler`, `scripts.check_health`, `scripts.docker_support`, `scripts.telegram_alert`
- **Lo importan:** ninguno
- **Configuración que lee:** `REPORT_TIMEZONE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Tablas que lee:** `llm_logs`
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/e2e/world.py`, `tests/unit/test_docker_mode_guards.py`, `tests/unit/test_docker_setup.py`, `tests/unit/test_measurement_uptime.py`, `tests/unit/test_telegram_daily_script.py`
<!-- /hechos:scripts/telegram_daily.py -->

### `scripts/check_measurement_safety.py`

**Responsabilidad.** Compara el árbol de trabajo, el índice (`--staged`) o los commits desde una revisión (`--since`, `--since-measurement`) con la lista de **zonas protegidas** de la medición y avisa qué archivos, parámetros de configuración o tablas protegidos cambiaron y de qué manera. Es la lista de zonas, en código, de [ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md).

**Funciones y clases clave.**
- `ZONES` (la lista, **fuente única**) y `Zone`; `zones_for()` dice a qué zonas pertenece un archivo.
- `classify_change()` y `code_signature()`: un cambio es `solo documentacion` si el AST sin docstrings es el mismo; los archivos fijados por hash (`app/llm/prompts.py`, `app/llm/market_features.py`) cuentan ante cualquier byte.
- `config_defaults()` / `config_changes()`: detectan el cambio del valor por defecto de una variable en `app/config.py`, que `.env.example` deje de coincidir con él, o que cambie una línea de `docker-compose.yml` o `scripts/start_bot.ps1`.
- `protected_schema()` / `schema_changes()`: comparan el DDL de las tablas de la medición; una columna nueva es aditiva (REVISAR), cambiar o quitar lo existente es PROTEGIDO.
- `gather_findings()`, `render()` y `main()` (códigos de salida: 0 sin cambios protegidos, 1 hay cambios protegidos, 2 error de uso o de git).

**Invariantes — no romper.** **Solo lee**: ejecuta `git` y lee archivos del repositorio; no importa `app`, no lee `.env`, no abre la base y no escribe nada (una prueba lo comprueba con el AST). La salida es solo ASCII (la consola de Windows no es UTF-8). Cada patrón de `ZONES` debe coincidir con un archivo versionado y cada zona debe estar explicada en [ZONAS_PROTEGIDAS](../ZONAS_PROTEGIDAS.md#mapa-rápido): `tests/unit/test_check_measurement_safety.py` lo exige. El subproceso `git` lleva `creationflags=NO_WINDOW`.

**Trampas conocidas.** `MEASUREMENT_START` (`c13f8ca`) es el commit que cierra el congelamiento; si se empieza una medición nueva hay que actualizarlo. No ve el `.env` real ni el contenedor que corre (para el modelo usa `scripts/measurement_identity.py`). El gancho `pre-commit` es opcional y no está instalado: ver [ENTORNO_DESARROLLO](../ENTORNO_DESARROLLO.md#gancho-pre-commit-opcional-no-instalado).

<!-- hechos:scripts/check_measurement_safety.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/scripts/check_measurement_safety.html`](../api/scripts/check_measurement_safety.html)
- **Importa (internos):** `scripts.docker_support`
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_check_measurement_safety.py`
<!-- /hechos:scripts/check_measurement_safety.py -->
