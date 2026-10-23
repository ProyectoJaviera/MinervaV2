# Configuración

Referencia de **todas** las variables de configuración de Minerva: qué hace cada una, su valor por defecto y cuándo
conviene cambiarla. La plantilla es [`.env.example`](../.env.example) (copia el archivo a `.env`, ver la
[guía de instalación](GUIA_INSTALACION.md)); el código que las lee es [`app/config.py`](../app/config.py). Una
prueba comprueba que cada variable de la plantilla está descrita aquí y que los valores por defecto de las tablas
coinciden con los del código.

## Cómo se lee la configuración

1. **Valores por defecto** en `app/config.py`.
2. El archivo **`.env`** de la raíz del repositorio los sustituye (en Docker se monta de solo lectura en
   `/app/.env`).
3. Las **variables de entorno del proceso** tienen prioridad sobre el `.env`. Es lo que usan los lanzadores
   (`docker-compose.yml`, `scripts/start_bot.ps1`) para fijar los [interruptores](#interruptores): *lo que pongas en el
   `.env` para esas variables se ignora* mientras el lanzador las fije.

Se lee **una sola vez, al arrancar**. Tras editar el `.env` hay que reiniciar el bot; con Docker, recrea el
contenedor (`docker compose up -d --force-recreate`) para que vea el archivo nuevo. Un `.env` que conserve variables
retiradas en versiones anteriores (`TRADING_SYMBOLS`, `KLINE_INTERVAL`, `STRATEGY_HISTORY_BARS`, `ACTIVE_STRATEGY`,
`MARGIN_MODE`, `ANTHROPIC_HAIKU_MODEL`, `CONFLUENCE_SCORE_THRESHOLD`) no falla: se ignoran.

## Unidades: fracciones y porcentajes

El sufijo `_pct` **no** dice la escala. Hay dos convenciones y mezclarlas es un error fácil:

- **Fracción 0–1** (`0.20` = 20 %): `MAX_DRAWDOWN_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_CAPITAL_PCT_PER_ASSET`,
  `MAKER_FEE_PCT`, `TAKER_FEE_PCT`, `TP_GAP_TOLERANCE_PCT`, `UNIVERSE_PRICE_SANITY_TOLERANCE_PCT`,
  `BACKTEST_FALLBACK_SL_PCT`, `BACKTEST_FALLBACK_TP_PCT` y las `BACKTEST_*_PCT` de criterios.
- **Porcentaje 0–100** (`50.0` = 50 %): `LIVE_SL_MARGIN_CAP_PCT` y `MAX_SL_MARGIN_LOSS_PCT`, que se comparan con el
  riesgo planeado de una entrada (`distancia al SL / precio × apalancamiento × 100`).

Al arrancar, el bot **falla con un mensaje claro** si una de las variables de riesgo en vivo
(`MAKER_FEE_PCT`, `TAKER_FEE_PCT`, `MAX_DRAWDOWN_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_CAPITAL_PCT_PER_ASSET`,
`TP_GAP_TOLERANCE_PCT`) queda fuera de 0–1, o `LIVE_SL_MARGIN_CAP_PCT` fuera de 0–100: un error de escala silencioso
podría desactivar un límite por completo. Las demás no se validan.

## Interruptores

Piezas opcionales. Casi todas están **apagadas por defecto** en el código y el lanzador enciende solo las del "bot
real" (el del puerto 8000). Así una instancia de prueba nunca gasta API, ni compite por el token de Telegram, ni
escribe al chat. La excepción es `DASHBOARD_CONTROLS_ENABLED`, que viene encendida.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `LLM_LIVE_LABELING_ENABLED` | Etiquetado en vivo: cada sombra nueva se envía a Claude, que la marca `APROBADA` o `RECHAZADA`. Es el corazón de la medición y la única función que cuesta dinero | `false` | Lo enciende el lanzador del bot real. Sin él no hay medición |
| `PAPER_EXECUTION_ENABLED` | Brazo ejecutado de la cuenta paper: abre posiciones *simuladas* solo con las señales `APROBADA` que llegaron a tiempo, pasando por el motor de riesgo. Exige el etiquetado encendido; **se niega a armarse** si `AUTO_OPEN_WITHOUT_LLM=true` | `false` | Lo enciende el lanzador del bot real |
| `TELEGRAM_COMMANDS_ENABLED` | Bot de comandos de Telegram (`/status`, `/pause`…) por *long-polling*. Un token solo admite **un** consumidor de `getUpdates` | `false` | Lo enciende el lanzador del bot real. Necesita token y `chat_id` |
| `TELEGRAM_PUSH_ENABLED` | Avisos al chat: posición abierta o cerrada, cambio de universo, alertas de riesgo y un mensaje de arranque | `false` | Lo enciende el lanzador del bot real. Necesita token y `chat_id` |
| `NEWS_COLLECTION_ENABLED` | Recolección de noticias gratuitas (GDELT, Fed, Fear & Greed) en una tarea aislada, sin costo de Anthropic. **No entra al prompt de la medición** | `false` | Lo enciende el lanzador del bot real. Apágala si no quieres tráfico a esas fuentes |
| `LESSONS_MEMORY_ENABLED` | Memoria de lecciones aprendidas (solo operaciones cerradas; nunca muestra PnL sin el registro oficial) | `false` | **No la enciendas durante una medición**; ningún lanzador la enciende |
| `AUTO_OPEN_WITHOUT_LLM` | Abre una posición paper por cada señal **sin** decisión del LLM | `false` | **No la cambies.** Mezclaría operaciones sin etiqueta en la cuenta paper y el brazo ejecutado se niega a armarse |
| `DASHBOARD_CONTROLS_ENABLED` | Permite los controles del dashboard (pausar, reanudar, kill switch). En `false`, la API los rechaza con 403 | `true` | Ponla en `false` si no quieres ningún control remoto |

**Quién fija cada interruptor:**

| Lanzador | Etiquetado, brazo, Telegram (comandos y avisos) y noticias | Memoria de lecciones | `AUTO_OPEN_WITHOUT_LLM` |
|---|---|---|---|
| `docker compose up -d` | Todos `true` (por defecto), o todos `false` con `MINERVA_REAL_BOT=false` | `false` | `false` |
| `scripts/start_bot.ps1` | `true` solo con el puerto 8000 (`-Port 8000`, el valor por defecto); `false` con cualquier otro | `false` | `false` |
| `scripts/run_dev_server.py` (puerto 8001, sobre una copia) | Todos `false`, y además vacía la clave de Anthropic y el token de Telegram | `false` | (sin tocar) |

Para **cambiar uno** en el bot real, edita la línea correspondiente donde la fija el lanzador: la sección
`environment:` de [`docker-compose.yml`](../docker-compose.yml) o las variables `$llmLabeling`, `$telegramPush`… de
[`scripts/start_bot.ps1`](../scripts/start_bot.ps1). Para apagarlos **todos** a la vez con Docker (modo prueba) basta
`MINERVA_REAL_BOT=false` (ver [Variables de Docker Compose](#variables-de-docker-compose)).

## Variables del `.env`

En cada tabla, *Por defecto* es el valor del código (y el de `.env.example`).

### General

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `ENV` | Etiqueta del entorno. **Hoy ningún código la lee: no tiene efecto** | `development` | Nunca hace falta |
| `LOG_LEVEL` | Nivel de los registros (`DEBUG`, `INFO`, `WARNING`…) | `INFO` | Los lanzadores la fijan en `INFO`. `DEBUG` solo para diagnosticar. En `INFO` los logs dejan pasar el motivo de cierre y el PnL de operaciones |
| `REPORT_TIMEZONE` | Zona horaria (nombre IANA) que define el "día" del tope diario de gasto del LLM, de la pérdida diaria y de las horas que muestra el resumen diario | `America/Santiago` | **Siempre**: ponla en tu zona (`Europe/Madrid`, `UTC`…) |
| `LOG_DIR` | Carpeta de los registros (`minerva.log` rota solo: 5 archivos de 10 MB) | `logs` | Docker la fija en `/app/logs`; casi nunca hace falta |
| `HEARTBEAT_SECONDS` | Cada cuántos segundos el proceso registra su latido (`last_beat_at` de `/health`) | `300` | Rara vez |

### Bitunix y scheduler

Solo se usan los endpoints **públicos** de mercado de Bitunix: Minerva no usa credenciales del exchange.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `BITUNIX_REST_BASE_URL` | URL base de la API REST pública de Bitunix | `https://fapi.bitunix.com` | Solo si Bitunix cambia su dominio |
| `BITUNIX_WS_PUBLIC_URL` | URL del WebSocket público (precios en tiempo real) | `wss://fapi.bitunix.com/public/` | Ídem |
| `BITUNIX_RATE_LIMIT_PER_SEC` | Máximo de peticiones por segundo del bot a Bitunix (el límite documentado es de 10 por segundo por IP) | `10` | Bájala si compartes IP con otro programa que consulte Bitunix o ves errores 429 |
| `SCHEDULER_POLL_SECONDS` | Cada cuántos segundos el scheduler busca velas cerradas nuevas para evaluar las estrategias | `60` | Rara vez |

### Comisiones simuladas

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `MAKER_FEE_PCT` | Comisión *maker* simulada (fracción). **Hoy no tiene efecto**: todas las órdenes simuladas pagan la tasa taker | `0.0002` | Nunca hace falta |
| `TAKER_FEE_PCT` | Comisión *taker* simulada, cobrada en la entrada y en la salida (fracción: `0.0006` = 0,06 %). Es la tarifa VIP 0 de futuros de Bitunix | `0.0006` | Si tu nivel en el exchange es otro. Cámbiala al empezar una medición, no a mitad |

Además de las comisiones, el simulador aplica un deslizamiento (*slippage*): ver `BACKTEST_SLIPPAGE_BPS` más abajo.

### Capital y tamaño de posición

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `INITIAL_CAPITAL_USDT` | Capital inicial de la cuenta paper (USDT simulados) | `100` | Al empezar una medición; no a mitad |
| `DEFAULT_MARGIN_USDT` | Margen de cada operación, simulada o sombra (USDT). El nocional es margen × apalancamiento | `5` | Al empezar una medición. La comparación del veredicto se expresa como fracción del margen, pero cambiarlo a mitad mezclaría tamaños distintos |
| `LEVERAGE` | Apalancamiento. El simulador solo modela **margen aislado** (no hay modo cruzado) | `10` | Al empezar una medición |

### Gestión de riesgo

El motor de riesgo solo bloquea **entradas nuevas**; nunca impide un cierre. Cada rechazo queda registrado con su
motivo.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `MAX_DRAWDOWN_PCT` | Caída máxima del equity desde su pico (fracción 0–1) que activa el stop por drawdown | `0.20` | Según tu tolerancia |
| `MAX_DAILY_LOSS_PCT` | Pérdida máxima del día (fracción 0–1) que bloquea las entradas nuevas hasta el cambio de día | `0.05` | Según tu tolerancia |
| `MAX_SIMULTANEOUS_POSITIONS` | Posiciones abiertas a la vez, como máximo | `3` | Según tu tolerancia |
| `MAX_CAPITAL_PCT_PER_ASSET` | Fracción del capital que puede quedar comprometida como margen en un mismo símbolo; entra en la fórmula del tamaño de cada entrada | `0.10` | Rara vez |
| `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER` | Pérdidas seguidas que activan el *circuit breaker* (pausa las entradas y genera un *post-mortem*) | `4` | Según tu tolerancia |
| `CIRCUIT_BREAKER_COOLDOWN_HOURS` | Horas que dura la pausa del *circuit breaker* | `8` | Según tu tolerancia |
| `MAX_SAME_DIRECTION_POSITIONS` | Máximo de posiciones abiertas en la **misma dirección** (LONG o SHORT) sin importar el símbolo; acota el riesgo de correlación entre altcoins | `2` | Según tu tolerancia |
| `LIVE_SL_MARGIN_CAP_PCT` | Tope de pérdida del *stop loss* sobre el margen, en **porcentaje 0–100**: una entrada cuyo SL perdería más que esto se rechaza | `50.0` | Rara vez. No uses una fracción (`0.5`) |
| `DRAWDOWN_STOP_MODE` | `duro`: al cruzar el drawdown se detienen las entradas hasta que lo levantes a mano (`scripts/resume_drawdown_stop.py`). Cualquier otro valor (se documenta `alerta`): solo avisa y cuenta el cruce | `duro` | `alerta` si prefieres que no detenga nada |
| `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` | Estrategias que pueden abrir posiciones en la **cuenta paper** (a pesar del nombre, no hay cuenta real). Lista separada por comas; vacío = todas. Las demás solo generan señales y sombras | `ema_cross_9_21,funding_contrarian_experimental,funding_contrarian_percentile_experimental` | Al empezar una medición |

### Vigencia de datos y monitor de posiciones

Si un dato crítico está obsoleto, el bot **no abre entradas nuevas** (los cierres siguen).

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `FUNDING_STALE_MARGIN_HOURS` | Margen (horas) sobre el intervalo de funding del contrato antes de considerar obsoleto el dato de funding | `1` | Rara vez |
| `CANDLE_STALE_TOLERANCE_SECONDS` | Tolerancia (segundos) para considerar obsoleta la última vela cerrada: más de un intervalo más esta tolerancia | `300` | Rara vez |
| `WS_STALE_AFTER_SECONDS` | Sin latido del WebSocket público durante estos segundos no se abren entradas (nunca bloquea cierres) | `45` | Rara vez |
| `TP_GAP_TOLERANCE_PCT` | Fracción 0–1. Un *take profit* solo se rellena al precio observado si el tick lo supera por más que esta fracción (hueco evidente); si no, al precio nominal | `0.002` | Rara vez |
| `TICK_STALE_SECONDS` | Sin ticks de un símbolo con posición abierta durante estos segundos: alerta y consulta el precio por REST | `60` | Rara vez |
| `MONITOR_TICK_SECONDS` | Cada cuántos segundos el monitor refresca marcas, funding, equity y estado | `15` | Rara vez |

### Criterios de paso a dinero real

**Informativos, no automáticos.** Los lee `scripts/real_money_report.py`, que solo *recomienda*; ver
[`PASO_DINERO_REAL.md`](PASO_DINERO_REAL.md). Minerva no tiene ejecución real.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `MIN_PAPER_TRADING_DAYS` | Días mínimos de operación del brazo ejecutado que exige el reporte | `30` | Nunca durante una medición |
| `MIN_CLOSED_TRADES` | Operaciones cerradas mínimas del brazo ejecutado | `100` | Ídem |
| `MIN_PROFIT_FACTOR` | Factor de beneficio mínimo (y su límite inferior del IC debe superar 1,0) | `1.3` | Ídem |

### CoinGecko y universo

El universo son las monedas de mayor capitalización según CoinGecko que además tengan perpetuo USDT en Bitunix, sin
stablecoins ni tokens envueltos. Se refresca cada 24 horas.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `COINGECKO_API_KEY` | Clave gratuita del plan Demo de CoinGecko, enviada en la cabecera `x-cg-demo-api-key`. Es una credencial: nunca la compartas | (vacío) | Ponla (recomendado; ver la [guía](GUIA_INSTALACION.md#35-clave-de-coingecko-opcional-recomendada)) |
| `COINGECKO_BASE_URL` | URL base de la API de CoinGecko | `https://api.coingecko.com/api/v3` | Casi nunca |
| `UNIVERSE_SIZE` | Cuántas monedas entran al universo | `10` | Al empezar una medición |
| `UNIVERSE_CANDIDATE_POOL` | Cuántos candidatos se piden a CoinGecko antes de filtrar stablecoins, tokens envueltos, etc. | `30` | Si el filtro deja menos monedas de las que quieres |
| `UNIVERSE_REFRESH_HOURS` | Cada cuántas horas se refresca el universo | `24` | Rara vez |
| `UNIVERSE_STALENESS_HOURS` | Un universo más viejo que esto no genera señales nuevas hasta el próximo refresco exitoso (los cierres siguen) | `48` | Rara vez |
| `UNIVERSE_EXCLUDE_CATEGORIES` | Categorías de CoinGecko que se excluyen (lista separada por comas) | `stablecoins,wrapped-tokens,liquid-staking-tokens` | Si quieres excluir otras categorías |
| `UNIVERSE_MANUAL_EXCLUSIONS` | Ids de CoinGecko (no símbolos) a excluir a mano, separados por comas | (vacío) | Para excluir un activo concreto |
| `UNIVERSE_PRICE_SANITY_TOLERANCE_PCT` | Fracción 0–1. Si el precio de CoinGecko y el de Bitunix difieren más que esto, el activo no entra al universo (evita un mapeo de ticker equivocado) | `0.05` | Rara vez |

### Backtest

El backtest es la Fase 2 del proyecto (`scripts/download_history.py` y `scripts/run_backtest.py`); **no interviene
en la operación en vivo**, salvo las tres variables que se señalan. Los criterios se fijaron *antes* de correrlo
([historial](historial/FASE2_CRITERIOS.md)): cambiarlos después para forzar un resultado invalida la prueba.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `BACKTEST_INITIAL_CAPITAL` | Capital de referencia de las simulaciones del backtest | `100` | Al reproducir el backtest |
| `BACKTEST_SLIPPAGE_BPS` | Deslizamiento simulado en puntos básicos, en la entrada y en la salida. **Se usa también en vivo** (cuenta paper y sombras), a pesar del nombre | `5` | Al empezar una medición; es un supuesto, no una medición |
| `BACKTEST_MIN_TRADES_PER_CELL` | Operaciones mínimas por celda (estrategia × símbolo × tramo) para contar en el porcentaje de tramos positivos | `30` | Al reproducir el backtest |
| `BACKTEST_MIN_TRADES_TOTAL` | Operaciones totales mínimas de una estrategia; por debajo, "evidencia insuficiente" (ni aprobada ni descartada) | `100` | Ídem |
| `BACKTEST_MIN_PROFIT_FACTOR` | Factor de beneficio mínimo fuera de muestra para no descartar una estrategia | `1.2` | Ídem |
| `BACKTEST_MAX_DRAWDOWN_PCT` | Fracción 0–1. Drawdown máximo tolerado fuera de muestra | `0.50` | Ídem |
| `BACKTEST_CONCENTRATION_LIMIT_PCT` | Fracción 0–1. Máximo del PnL neto total que puede venir de una sola operación o un solo símbolo | `0.40` | Ídem. También lo usa el reporte de paso a dinero real |
| `BACKTEST_MIN_PCT_SYMBOLS_PF_GT1` | Fracción 0–1. Mínimo de símbolos con factor de beneficio individual mayor que 1 | `0.50` | Ídem |
| `BACKTEST_MIN_PCT_FOLDS_POSITIVE` | Fracción 0–1. Mínimo de tramos *walk-forward* con resultado neto positivo | `0.50` | Ídem |
| `BACKTEST_STRESS_FEE_MULTIPLIER` | Multiplicador de comisiones en la prueba de estrés | `2` | Ídem |
| `BACKTEST_STRESS_SLIPPAGE_MULTIPLIER` | Multiplicador del deslizamiento en la prueba de estrés | `2` | Ídem |
| `BACKTEST_OOS_SPLIT_PCT` | Fracción 0–1 del historial más reciente reservada como fuera de muestra | `0.30` | Ídem |
| `BACKTEST_WALK_FORWARD_FOLD_MONTHS` | Duración (meses) de cada tramo *walk-forward* | `6` | Ídem |
| `BACKTEST_WALK_FORWARD_STEP_MONTHS` | Avance (meses) entre tramos *walk-forward* (se solapan) | `2` | Ídem |
| `BACKTEST_CONTROL_SYMBOLS` | Símbolos del grupo de control contra el sesgo de supervivencia | `BTCUSDT,ETHUSDT` | Ídem |
| `MAX_SL_MARGIN_LOSS_PCT` | **Porcentaje 0–100**, solo backtest: si el SL de una entrada perdería más de este porcentaje del margen, la entrada se omite (en vivo manda `LIVE_SL_MARGIN_CAP_PCT`) | `50.0` | Ídem |
| `BACKTEST_FALLBACK_SL_PCT` | Fracción 0–1 de movimiento de precio para el SL de las estrategias que no definen el suyo. **Se usa también en vivo** | `0.05` | Al empezar una medición |
| `BACKTEST_FALLBACK_TP_PCT` | Ídem para el *take profit*. **También en vivo** | `0.10` | Al empezar una medición |
| `BACKTEST_PORTFOLIO_SIM_RUNS` | Corridas Monte Carlo de la simulación de cartera (informativa) | `200` | Ídem |
| `BACKTEST_OFFICIAL_END_DATE` | Fecha final fija (UTC, `AAAA-MM-DD`) del backtest "oficial": dos corridas con la misma caché dan exactamente los mismos números | `2026-10-01` | Ídem |

### Claude (Anthropic)

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Clave de la API de Anthropic. **Credencial**; es lo único que cuesta dinero | (vacío) | Es obligatoria para medir |
| `ANTHROPIC_SONNET_MODEL` | Modelo que etiqueta las señales. El hash congelado cubre el prompt y las *features*, **no el modelo** | `claude-sonnet-5-5` | **No la cambies mientras dure una medición**: contaminaría el veredicto sin que nada lo avise |
| `LLM_DAILY_BUDGET_USD` | Tope diario de gasto con **corte duro** (el día se mide en `REPORT_TIMEZONE`). Al agotarse, las señales quedan `SIN_LLM` | `1.0` | Un tope demasiado bajo deja señales sin etiqueta y daña la medición (más del 10 % de `SIN_LLM` hace el veredicto `INCONCLUSO`) |
| `LLM_MAX_TOKENS` | Tokens máximos de la respuesta (un JSON corto de decisión, confianza y razonamiento) | `300` | **No la cambies durante una medición** |
| `LLM_TIMEOUT_SECONDS` | Tiempo máximo de cada llamada; si se excede, la señal queda `SIN_LLM` | `20` | **No la cambies durante una medición** |
| `LLM_MAX_CONCURRENCY` | Llamadas simultáneas al LLM | `4` | Rara vez |
| `LLM_REAL_MAX_DELAY_SECONDS` | El brazo ejecutado solo abre si la decisión llegó antes de este retraso (segundos) desde el cierre de la vela | `60` | Al empezar una medición |
| `LLM_PRICE_INPUT_PER_MTOK` | Precio de los tokens de entrada, USD por millón. Solo sirve para calcular el costo y el corte del tope diario | `2.0` | Si Anthropic cambia sus precios (verifícalos en su página) |
| `LLM_PRICE_OUTPUT_PER_MTOK` | Ídem, tokens de salida | `10.0` | Ídem |

### Noticias y memoria de lecciones

Todo esto está **fuera del prompt congelado**: las noticias son informativas y ninguna operación depende de ellas.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `NEWS_SOURCES` | Fuentes habilitadas (ids): `gdelt`, `fed_monetary`, `fear_greed` | `gdelt,fed_monetary,fear_greed` | Para quitar alguna fuente |
| `NEWS_RETENTION_DAYS` | Días que se conservan las noticias antes de podarlas | `180` | Rara vez |
| `NEWS_USER_AGENT` | Cómo se identifica el bot ante esas fuentes (sin datos personales) | `MinervaPaperBot/1.0 (personal paper-trading research, non-commercial)` | Si quieres añadir un contacto |
| `NEWS_MAX_RESPONSE_BYTES` | Tamaño máximo aceptado de cada respuesta | `2000000` | Rara vez |
| `LESSONS_MIN_SAMPLE` | Muestra mínima antes de que una lección o una cohorte similar influya (evita el sobreajuste) | `30` | Rara vez |
| `LESSONS_REBUILD_HOURS` | Cada cuántas horas se reconstruye la memoria de lecciones (si está encendida) | `24` | Rara vez |
| `LESSONS_TOP_K` | Cuántas operaciones ganadoras y perdedoras parecidas recupera por decisión | `3` | Rara vez |

### Telegram

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot que te dio @BotFather. **Credencial** | (vacío) | Para usar Telegram |
| `TELEGRAM_CHAT_ID` | Identificador del único chat **privado** autorizado a dar órdenes y que recibe los avisos (un número positivo; un grupo o canal, negativo, se rechaza) | (vacío) | Para usar Telegram |
| `TELEGRAM_PUSH_POLL_SECONDS` | Cada cuántos segundos el notificador revisa la base en busca de cambios que avisar | `10` | Rara vez |

Los interruptores `TELEGRAM_COMMANDS_ENABLED` y `TELEGRAM_PUSH_ENABLED` están en [Interruptores](#interruptores).

### Dashboard y API

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `AUTH_USERNAME` | Usuario del dashboard y de la API con sesión | (vacío) | Al instalar |
| `AUTH_PASSWORD_HASH` | Hash bcrypt de la contraseña, entre **comillas simples** (`'$2b$12$…'`), tal como lo imprime `scripts/hash_password.py`. **Credencial** | (vacío) | Al instalar o al cambiar la contraseña |
| `JWT_SECRET` | Clave que firma la sesión (cookie `httpOnly` de 24 h). **Al menos 32 caracteres**; con menos, el login responde 503. **Credencial** | (vacío) | Al instalar. Cambiarla cierra todas las sesiones |

El interruptor `DASHBOARD_CONTROLS_ENABLED` está en [Interruptores](#interruptores).

### Base de datos

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `DATABASE_PATH` | Ruta del archivo SQLite (modo WAL). **Los lanzadores la fijan** y por eso lo que pongas aquí se ignora para el bot real: `docker-compose.yml` usa `/app/data/minerva_estabilidad.db` (nombre histórico, en un volumen) y `start_bot.ps1` usa `data\minerva_estabilidad.db`. Solo cuentan los scripts de backtest y las pruebas | `./data/minerva.db` | Casi nunca |

## Variables de Docker Compose

No son de la aplicación: las lee `docker-compose.yml` al crear el contenedor. Puedes ponerlas en el entorno del
shell o en el `.env` de la raíz (Compose lo lee para interpolar). Todas son opcionales.

| Variable | Qué hace | Por defecto | Cuándo cambiarla |
|---|---|---|---|
| `MINERVA_PORT` | Puerto del equipo (siempre en `127.0.0.1`) donde se publica la API y el dashboard | `8000` | Solo para una instancia de prueba. Dos bots reales a la vez duplicarían el token y la clave |
| `MINERVA_REAL_BOT` | `true`: los mismos interruptores que `start_bot.ps1` con el puerto 8000. `false`: los de una instancia de prueba (sin etiquetado del LLM, Telegram, brazo ejecutado, noticias ni avisos) | `true` | `false` para explorar sin gastar API ni competir por el token |
| `MINERVA_RESTART` | Política de reinicio del contenedor: `unless-stopped` (vuelve solo tras reiniciar Docker o el equipo, salvo que lo detengas tú) o `no` | `unless-stopped` | `no` para instancias de prueba |
| `MINERVA_ENV_FILE` | Archivo de credenciales que se monta de solo lectura en `/app/.env` | `.env` | Para probar con otro archivo |

## Qué no cambiar durante una medición

La medición del valor del LLM depende de que no cambien el prompt, las *features* ni el modelo. Mientras corra:

- **No edites** `app/llm/prompts.py` ni `app/llm/market_features.py`. El hash del prompt se comprueba en cada ciclo y, si no
  coincide, el etiquetado se detiene solo.
- **No cambies** `ANTHROPIC_SONNET_MODEL`, `LLM_MAX_TOKENS` ni `LLM_TIMEOUT_SECONDS`. El modelo **no** está
  vigilado por el hash: un cambio contaminaría la medición sin aviso. `scripts/measurement_identity.py` comprueba a
  mano que haya un único modelo, un único prompt y una única versión de *features* en las filas medidas.
- Los parámetros de simulación y de universo (margen, apalancamiento, comisiones, deslizamiento, SL/TP de respaldo,
  tamaño del universo, estrategias elegibles) cambian *qué población de señales se mide y cómo se simula*. **Recomendación:**
  cámbialos solo al empezar una medición nueva.
- Los de riesgo y de vigencia de datos (topes de pérdida, *circuit breaker*, cupos) solo afectan a la cuenta paper
  del brazo ejecutado, no a las sombras con las que se calcula el veredicto.
