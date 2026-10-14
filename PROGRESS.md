# PROGRESS -- Minerva

## Fase 0 -- Evaluacion, verificacion de APIs y diseno preliminar

**Estado: APROBADA.**

- Entregable: `docs/FASE0.md`.
- Evaluacion critica de `docs/openspec.md` vs `docs/SPEC.md`: reutilizable
  como referencia de bajo nivel (firma HMAC, reconexion WS, mecanica de TP
  parcial/trailing); descartado su alcance mono-activo, SL/TP fijos y LLM
  binario, por contradecir SPEC.md.
- Endpoints publicos de Bitunix verificados contra `www.bitunix.com/api-docs`
  (ver seccion 2 de `docs/FASE0.md`). Confirmado: sin fee publico (se usa
  VIP0 oficial), sin Open Interest publico (se omite en v1), sin funding
  historico documentado explicitamente (endpoint existe, profundidad real
  a validar empiricamente).
- CoinGecko: plan Demo si requiere API key gratuita (100 llamadas/min,
  10.000/mes) -- correccion a una asuncion inicial de que era anonimo.
- Decisiones del usuario (10 respuestas, ver `docs/FASE0.md` seccion 5):
  capital 100 USDT, margen 10 USDT, drawdown max 20%, criterio de paso a
  real = 30 dias Y 100 operaciones (lo que ocurra mas tarde), circuit
  breaker a 4 perdidas consecutivas con enfriamiento de 8h (pausa
  indefinida reservada para drawdown/perdida diaria), presupuesto LLM
  1 USD/dia con corte duro, fees VIP0 configurables (taker en mercado/stops,
  maker en limite), un solo usuario/password, TZ reportes America/Santiago
  (calculos internos en UTC), moneda USDT.

## Fase 1 -- Estructura, datos de mercado Bitunix y vertical minima end-to-end

**Estado: APROBADA.**

Plan aprobado: `C:\Users\Renzo\.claude\plans\abundant-knitting-pearl.md`
(resumen tambien en la conversacion). Instrucciones adicionales del usuario
seguidas: vertical minima antes de ampliar breadth; sin pandas-ta (EMA/RSI/ATR
con pandas puro); descarga historica paginada y cacheada; verificacion de
funding historico; aclaracion de que el backtest (Fase 2) solo valida
estrategias por reglas, no al LLM (que se valida con paper trading hacia
adelante, Fase 4+).

### Que se construyo

- Estructura de carpetas completa (`app/`, `frontend/`, `tests/`, `docker/`).
- `config.py`: toda la configuracion acordada en Fase 0, incluidas variables
  que Fase 4/5 usaran (modelos LLM `claude-haiku-4-5` / `claude-sonnet-5`,
  presupuesto diario, umbral de confluencia) para no reabrir este archivo.
- Formula de sizing de riesgo (`Settings.max_margin_for_new_trade`) que
  respeta el tope de 10%/activo y el limite de 3 posiciones simultaneas,
  con tests de coherencia (incluye el caso de capital reducido por perdidas).
- Cliente REST publico de Bitunix (`market/bitunix_rest.py`): 7 endpoints
  verificados, rate limiter propio (10 req/s), reintentos con backoff
  exponencial. Construye su cliente HTTP via `app/core/http.py`
  (`build_async_http_client`), el modulo de red centralizado que usa
  `truststore` -- almacen de certificados del SO -- para funcionar tambien
  en redes corporativas con inspeccion TLS; todo cliente HTTP futuro
  (CoinGecko en Fase 2, etc.) debe construirse con esa misma funcion.
- Cache historica de velas con paginacion (`market/ohlcv_history.py`) sobre
  la tabla `ohlcv_cache`.
- Cliente WS publico (`market/bitunix_ws.py`): reconexion con backoff,
  heartbeat ping/pong, suscripcion al canal `tickers` -- verificado en vivo
  como componente independiente (no integrado a la decision todavia, ver
  "Limitaciones conocidas").
- `indicators/engine.py`: EMA, RSI (Wilder) y ATR con pandas puro.
- `strategies/`: `EMACrossStrategy` (9/21) como primera de las 3-5
  estrategias candidatas a backtestear en Fase 2.
- `execution/`: `PaperBackend` (activo, usa mark price real de Bitunix,
  aplica fee taker, aplica el tope de riesgo); `BitunixBackend` (clase que
  **lanza `NotImplementedError` en `__init__`**, documentada, jamas
  instanciada -- regla critica de CLAUDE.md).
- `persistence/`: `aiosqlite` con tablas `trades`, `system_state`,
  `ohlcv_cache`, `contract_specs_cache` (el resto del esquema de
  `docs/FASE0.md` se crea en la fase que lo necesite).
- `core/scheduler.py` + `main.py`: loop asyncio que evalua la estrategia
  sobre velas reales y opera en `PaperBackend`.
- API minima (`GET /health`, `/positions`, `/trades`), sin auth (Fase 5).
- Frontend minimo (React + Vite): una pantalla con polling a `/positions` y
  `/trades`, banner permanente de "MODO PAPER TRADING". Sin auth/WS/diseno
  final (Fase 5).
- Docker: `docker/Dockerfile.backend` + `docker-compose.yml`, esqueleto
  minimo (no obligatorio en esta fase).

### Verificacion realizada

- `pytest`: **35 passed, 2 skipped** (los 2 omitidos son integracion en vivo
  contra la red real, marcados `@pytest.mark.slow`, se activan con
  `MINERVA_RUN_LIVE_TESTS=1`).
- `ruff check .`: **sin errores**.
- Verificacion end-to-end manual con datos reales de Bitunix (no mockeados):
  se levanto el servidor, el scheduler obtuvo klines reales de BTCUSDT
  (`GET .../market/kline` 200 OK), y un trade de verificacion abrio/cerro
  una posicion LONG al precio real de mercado (~83738.6 USDT en el momento
  de la prueba), calculando fees y PnL correctamente; la operacion fue
  visible de inmediato en `GET /trades`. El entorno de desarrollo usado en
  esta sesion requirio Python 3.11/3.12 (un bug del build de Python 3.14 de
  `python-build-standalone` en Windows hace crashear el modulo `ssl` con
  `OPENSSL_Uplink: no OPENSSL_Applink`, no relacionado con este proyecto) y
  `truststore` para el certificado TLS de la red local -- ambos ya resueltos
  en el codigo/README.

### Ajustes solicitados tras la aprobacion de Fase 1

1. **Endpoints de abrir/cerrar posiciones**: no existen en la API. `api/routes/`
   solo tiene 3 rutas, las 3 `GET` y de solo lectura (`/health`, `/positions`,
   `/trades`); no hay ningun `POST`/`PUT`/`DELETE`. La apertura/cierre que se
   veia en la verificacion manual de esta fase se hizo con un script Python
   suelto que llamaba a `PaperBackend` directamente (fuera de la API HTTP, sin
   pasar por red) y nunca se guardo en el repositorio. Fase 5 es la que
   definira si se expone algun control de apertura/cierre manual por HTTP, y
   en tal caso ira detras de la autenticacion de esa fase.
   **API atada a loopback:** `docker-compose.yml` publica el puerto como
   `"127.0.0.1:8000:8000"` (antes `"8000:8000"`) -- el contenedor sigue
   escuchando en `0.0.0.0` *dentro* de su propio namespace de red (asi
   funciona el port-forwarding de Docker), pero Docker solo enruta ese
   puerto publicado hacia la interfaz de loopback del host, asi que la API
   nunca es alcanzable desde la LAN ni desde internet. En local (`uvicorn`
   directo) se fija explicitamente `--host 127.0.0.1` en `README.md`.
2. **`truststore` centralizado**: se creo `app/core/http.py` con
   `build_async_http_client()`, la unica forma permitida de construir un
   cliente HTTP asincrono en el proyecto. `market/bitunix_rest.py` ya lo usa;
   el cliente de CoinGecko (Fase 2) y cualquier otro futuro deben usarlo
   tambien en vez de instanciar `httpx.AsyncClient` por su cuenta.
   **Pendiente para Fase 6:** un contenedor Docker recien construido no tiene
   el certificado raiz de la inspeccion TLS corporativa de esta red en su
   almacen de confianza del sistema operativo (a diferencia de la maquina
   host, que si lo tiene instalado vía Windows). Si al ejecutar
   `docker compose up` las llamadas a Bitunix/CoinGecko fallan con
   `CERTIFICATE_VERIFY_FAILED` *solo dentro del contenedor* (pero funcionan
   en local), Fase 6 debera montar ese certificado raiz en la imagen (por
   ejemplo, copiarlo a `/usr/local/share/ca-certificates/` y correr
   `update-ca-certificates` en el Dockerfile, o montarlo como volumen) --
   `truststore` dentro del contenedor leera el almacen de certificados de
   ESE sistema operativo (el de la imagen), no el de Windows.
3. **`.gitignore` y `requires-python`**: `.venv/` -> `.venv*/` (cubre
   variantes como la `.venv311` temporal que se uso durante la depuracion de
   esta fase); ya cubria `.env`, `*.db` (+ `-wal`/`-shm`), `node_modules/` y
   `*.log`. `pyproject.toml` ahora fija `requires-python = ">=3.11,<3.13"`
   (antes solo `>=3.11`, lo que habria permitido instalar en el 3.14 con el
   bug de `ssl` encontrado en esta fase).
4. **Ejemplo numerico de fees y PnL** (formulas y calculo verificable a
   mano; corresponde exactamente al test automatizado
   `test_open_and_close_long_position_computes_pnl_and_fees` en
   `tests/unit/test_paper_backend.py`):

   ```
   Entrada:  margen = 10 USDT, apalancamiento = 10x, taker_fee = 0.06% (0.0006)
             precio de entrada = 100.0, precio de salida = 110.0 (LONG)

   notional        = margen * apalancamiento           = 10 * 10        = 100.0 USDT
   qty             = notional / precio_entrada          = 100 / 100      = 1.0
   fee_entrada     = notional * taker_fee                = 100 * 0.0006  = 0.06 USDT
   notional_salida = qty * precio_salida                 = 1.0 * 110     = 110.0 USDT
   fee_salida      = notional_salida * taker_fee          = 110 * 0.0006 = 0.066 USDT

   pnl_bruto (LONG) = (precio_salida - precio_entrada) * qty
                    = (110 - 100) * 1.0                                 = 10.0 USDT

   pnl_neto = pnl_bruto - fee_entrada - fee_salida
            = 10.0 - 0.06 - 0.066                                       = 9.874 USDT

   ROI sobre el margen = pnl_neto / margen = 9.874 / 10                 = 98.74 %
   ```

   Caso SHORT (simetrico, mismo ejemplo con precio cayendo de 100 a 90):
   ```
   pnl_bruto (SHORT) = (precio_entrada - precio_salida) * qty
                     = (100 - 90) * 1.0                                 = 10.0 USDT
   ```
   (misma formula de fees; `qty` y `fee_entrada` no cambian porque dependen
   del precio de entrada, no del de salida).

   Formulas generales usadas por `PaperBackend` (ver
   `app/execution/paper_backend.py`):
   ```
   notional      = margen_usdt * leverage
   qty           = notional / precio_entrada
   fee_entrada   = notional * taker_fee_pct
   fee_salida    = (qty * precio_salida) * taker_fee_pct
   pnl_bruto     = (precio_salida - precio_entrada) * qty          si LONG
   pnl_bruto     = (precio_entrada - precio_salida) * qty          si SHORT
   pnl_neto      = pnl_bruto - fee_entrada - fee_salida
   ```
   No incluye funding ni slippage (Fase 3).

### Limitaciones conocidas de esta fase (documentadas, no son bugs)

- **Sin SL/TP/trailing/liquidacion/funding/slippage/reconciliacion** en
  `PaperBackend` -- eso es el "simulador realista" de Fase 3. El cierre solo
  ocurre por senal contraria de la estrategia o llamada manual.
- **Universo mono-simbolo** (`TRADING_SYMBOLS`, default `BTCUSDT`) en vez
  del pipeline completo CoinGecko top-10 + exclusion stablecoins/wrapped.
  Se construye en un paso previo a o durante Fase 2.
- **Cliente WS construido pero no integrado** a la decision/ejecucion
  todavia (la vertical minima usa REST, que ya expone `markPrice` explicito
  en `tickers`; el canal WS `tickers` no lo expone, solo `la`/last price).
  Se integrará cuando el simulador necesite evaluacion a nivel de tick
  (Fase 3, exigido por SPEC.md para SL/TP/trailing).
- **API sin autenticacion** todavia (Fase 5).
- **Profundidad real de `get_funding_rate_history` sin validar** contra
  un rango largo (el endpoint existe y responde; falta un test dirigido que
  pida, por ejemplo, 90 dias atras y confirme cuantos registros devuelve).
- Nota para el informe final (Fase 6): el backtest de Fase 2 valida
  **unicamente las estrategias por reglas**; la capa LLM (Fase 4) no se
  puede backtestear con estos datos y se valida exclusivamente con paper
  trading hacia adelante -- esto debe quedar explicito en el informe de
  criterios de paso a dinero real.

## Fase 2 -- Universo dinamico y backtesting riguroso

**Estado: CERRADA.** Ver `docs/FASE2_RESULTADOS.md` (veredicto final por
estrategia y simulacion de cartera) y `docs/FASE2_RIESGO.md` (analisis de
riesgo de ruina con bootstrap, informa los parametros de Fase 3).

### Que se construyo

- Universo dinamico (CoinGecko top-N menos stablecoins/wrapped/liquid-staking,
  intersectado con perpetuos USDT de Bitunix) + grupo de control BTC/ETH.
- Motor de backtest sin sesgo de anticipacion (`app/backtesting/engine.py`):
  fill en el open de la vela siguiente a la senal, liquidacion por
  MARK_PRICE, SL/TP por LAST_PRICE, orden de eventos adversos por cercania
  de precio, ejecucion al OPEN si hay gap, funding real/aproximado
  prorrateado por vela, indicadores precalculados una sola vez por serie.
- 6 estrategias candidatas de familias distintas (`ema_cross_9_21`,
  `trend_atr_stop_9_21_50`, `mean_reversion_rsi14_bb20`,
  `donchian_breakout_20`, y 2 variantes experimentales de
  `funding_contrarian`), validadas IS/OOS + walk-forward, con criterios de
  descarte pre-registrados ANTES de la corrida completa
  (`docs/FASE2_CRITERIOS.md`).
- Simulacion de cartera Monte Carlo (200 corridas, capital/margen/tope de
  posiciones reales, drawdown mark-to-market) como metrica informativa
  adicional, nunca como criterio de descarte.
- Descarga incremental/reanudable de velas y funding
  (`scripts/download_history.py`) separada del calculo
  (`scripts/run_backtest.py`, que ya no toca la red).

### Resultado

**Ninguna estrategia por reglas supera los criterios congelados** (PF OOS:
`ema_cross` 1.12, `trend_atr_stop` 1.02, `mean_reversion` 0.74, `donchian`
0.90 -- todas por debajo del minimo 1.2, o con otros criterios
incumplidos). Esto se reporta honestamente, sin relajar ningun umbral --
exactamente el resultado que el criterio de exito de esta fase ("el
pipeline corre de punta a punta con resultados auditables, no que exista
una estrategia ganadora") contemplaba como posible. Detalle completo,
incluida la simulacion de cartera por estrategia, en
`docs/FASE2_RIESGO.md` y `docs/FASE2_RESULTADOS.md`.

### Reejecucion v2 (vigente) -- ver `docs/FASE2_REEJECUCION.md`

- **Error de redondeo en el tope de SL** (`50.000000000000014 > 50`): descarto por
  azar segun el precio 2.042 entradas de `ema_cross_9_21`, 509 de
  `funding_contrarian_experimental` y 1.037 de `funding_contrarian_percentile_experimental`.
  Corregido en v2. La v1 queda archivada como corrida con el error.
- **Control de validez**: las estrategias sin ese error cambian entre v1 y v2 por la
  **ventana**, no por el redondeo ni por la cache. v1 uso la hora de ejecucion como
  fin (no reproducible); el fin fijo llego 23 min despues (`1e15257`). Las 14 filas
  distintas de los tres controles quedan explicadas, 0 sin explicar.
- **Resultado**: ninguna estrategia de reglas supera los criterios congelados. Las
  dos experimentales no se evaluan por diseño y fallarian el criterio de
  concentracion (76 % y 78 %). El PF de ema pasa de 1,12 (v1) a 0,97 (v2, 556
  operaciones OOS).
- **Regla de decision sobre la IA (fijada)**: sin IC del 95 % que excluya cero para
  la diferencia APROBADA vs RECHAZADA con N >= 100 por lado, "la IA no aporta valor"
  y se detiene el gasto en la API.
- **Ninguna estrategia de reglas tiene ventaja demostrable; no se justifica dinero
  real con la evidencia actual.**
- Benchmark de entradas aleatorias propuesto (no implementado, ver seccion 6 del doc).

### Problemas reales encontrados y corregidos durante esta fase

Ver el detalle completo (con la justificacion de cada correccion) en las
secciones "Correcciones..." de `docs/FASE2_CRITERIOS.md` y en
`docs/FASE2_BLOQUEO_RED.md`. Resumen:

- Un diagnostico inicial de "bloqueo de red" durante la primera corrida
  completa resulto ser incorrecto -- la causa real era computo lento sin
  logging de progreso, mas una redescarga completa innecesaria en cada
  reintento. Corregido con descarga incremental + logging de progreso.
- Bug real en `funding_contrarian_percentile_experimental`: nunca generaba
  señales porque exigia mas historia de la que el motor le pasaba por
  vela evaluada. Corregido precalculando los percentiles sobre la serie
  completa.
- Bug real en `simulate_portfolio`: el desempate de operaciones con el
  mismo `entry_time` sesgaba sistematicamente la admision por orden
  alfabetico de simbolo. Corregido con una simulacion Monte Carlo que
  baraja el desempate.
- Bug real en `backtest_repo.insert_verdict`: un INSERT escrito a mano se
  desincronizo (29 valores para 30 columnas) al agregar la simulacion de
  cartera. Corregido generando columnas y valores desde el modelo
  (`BacktestVerdict.model_dump()`) en vez de listas paralelas a mano.
- Bug real de piso de cache ausente (`MissingHistoricalDataError` evitable
  en BNBUSDT 1h) pese a que los datos cacheados si cubrian lo pedido.
  Corregido con una marca afirmativa de "serie completa", mas una
  verificacion previa de TODAS las series antes de calcular nada.
- Off-by-one en el chequeo de huecos de cache (comparaba contra "ahora" en
  vez de contra la ultima vela realmente descargada), que generaba un
  falso positivo en 46 de 60 series. Corregido.

### Pendientes explicitos para fases siguientes

- **Fase 3**: plan aprobado (`docs/FASE3_PLAN.md`), en implementacion
  subfase por subfase -- ver seccion "Fase 3" mas abajo para el estado
  detallado. Fusiona deliberadamente lo que `docs/FASE0.md` separaba en
  "Fase 3" (motor de riesgo, simulador realista con SL/TP/trailing/
  liquidacion por tiers, funding periodico, slippage, reconciliacion tras
  downtime) y "Fase 4" (LLM como motor de decision) -- medir el valor del
  LLM exige que el LLM ya este tomando decisiones reales, asi que no puede
  quedar en una fase posterior separada.
- Fase 4 (lo que queda tras la fusion anterior): noticias (RSS CoinDesk/
  Cointelegraph, sentimiento), memoria/lecciones entre operaciones,
  recalibracion automatica del umbral de confianza del LLM -- mejoras
  sobre el filtro aprobar/rechazar que Fase 3 ya entrega, no requisitos
  para su primera medicion.
- Fase 5: autenticacion (un usuario/password), dashboard completo (tiempo
  real via WS, graficos, controles), API completa, Telegram.
- Fase 6: Docker final multi-stage, pruebas E2E, informe de metricas de
  paper trading con la nota sobre backtest-vs-paper-trading del LLM.

## Fase 3 -- Gestion de riesgo, ejecucion realista y medicion del valor del LLM

**Estado: APROBADA (plan), EN IMPLEMENTACION (subfase por subfase).**

Plan: `docs/FASE3_PLAN.md` (3 rondas de revision antes de la aprobacion;
ver el encabezado del documento para el detalle de cada ronda). Ajustes de
la tercera ronda ya incorporados al plan: la columna "posiciones
simultaneas" de `docs/FASE2_RIESGO.md` no es fiable para justificar
`MAX_SIMULTANEOUS_POSITIONS` (sesgo documentado, nuevo limite
`MAX_SAME_DIRECTION_POSITIONS` agregado); ritmo de operaciones reales
recalculado con cifras OOS reales (antes mezclaba IS+OOS); criterio de
valor de la IA elevado a N=100 por lado con intervalo de confianza
bootstrap; stop por drawdown configurable (`DRAWDOWN_STOP_MODE`) con
contador que alimenta el criterio de paso a dinero real igual en ambos
modos; precio de Sonnet marcado explicitamente como no verificado.

Se avanza subfase por subfase (8 en total, ver la tabla en
`docs/FASE3_PLAN.md`), deteniendose a esperar aprobacion al terminar cada
una:

- **3.1 -- Extraer `stop_engine.py` + modo tick: HECHA.** Nuevo paquete
  `app/trading/`, modulo `stop_engine.py`: `order_adverse_thresholds`,
  `check_adverse_bar`, `check_favorable_tp_bar` son una extraccion PURA de
  `app/backtesting/engine.py` (antes privadas con guion bajo) -- mismo
  comportamiento exacto, verificado porque los 196 tests existentes del
  backtest pasan sin haberlos tocado. Nuevas `check_adverse_tick` y
  `check_favorable_tp_tick` (modo tick, sin la nocion de gap/apertura de
  vela que tiene el modo vela -- devuelven siempre el precio real
  observado en el tick, nunca un umbral nominal). 22 tests nuevos en
  `tests/unit/test_stop_engine.py`. No toca persistencia ni red -- no
  requeria smoke test contra copia de la base real.
- **3.2 -- Motor de riesgo en vivo: HECHA.** Nuevo `app/trading/
  risk_engine.py`: `check_new_entry` evalua, en orden, kill switch, stop
  por drawdown (solo modo `"duro"`), circuit breaker, perdida diaria,
  elegibilidad de estrategia, `MAX_SIMULTANEOUS_POSITIONS`,
  `MAX_SAME_DIRECTION_POSITIONS` (nuevo), tope de SL (`sl_margin_loss_pct`
  opcional) y margen disponible -- **solo bloquea entradas nuevas, nunca
  cierres** (verificado con un test explicito: posicion abierta se sigue
  cerrando con el kill switch activo). Cada rechazo se audita en la tabla
  nueva `risk_rejections` (motivo + valores exactos). Perdida diaria y
  drawdown se calculan sobre `PaperBackend.get_equity()` (realizado MAS
  PnL flotante de posiciones abiertas, nunca solo lo realizado); el "dia"
  usa `REPORT_TIMEZONE` (America/Santiago por defecto). Todo el estado
  (kill switch, stop por drawdown, circuit breaker, racha de perdidas,
  pico de equity, linea base del dia) vive en `system_state` -- persiste
  solo, sin codigo extra, a traves de un reinicio (verificado con 2 tests
  que cierran y reabren la conexion a un archivo real). Circuit breaker:
  auto-reanuda tras el enfriamiento; stop por drawdown en modo `"duro"`:
  reanudacion MANUAL unicamente (nunca se levanta solo), con un contador
  `drawdown_stop_would_have_triggered_count` que sube en cualquier modo.
  Nuevos settings: `MAX_SAME_DIRECTION_POSITIONS`, `LIVE_SL_MARGIN_CAP_PCT`,
  `DRAWDOWN_STOP_MODE`, `REAL_ACCOUNT_ELIGIBLE_STRATEGIES` (no se pudo
  agregar a `.env.example` -- acceso a ese archivo bloqueado por permisos
  de la herramienta en esta sesion; son opcionales, ya tienen default en
  `app/config.py`). 22 tests nuevos en `tests/unit/test_risk_engine.py`
  (uno por limite, racha de perdidas con auto-resume, 2 de persistencia
  tras reinicio simulado, kill-switch-no-bloquea-cierre, zona horaria
  configurable, y un smoke test de punta a punta). Verificado ademas
  contra una copia temporal de la base real (`risk_rejections` se crea
  limpio, `check_new_entry` corre sin errores con los datos reales).
  Bug real encontrado y corregido durante esta subfase: un smoke test con
  una excepcion sin capturar dejaba una conexion de archivo sin cerrar, lo
  que colgaba el proceso entero al salir (hilo de aiosqlite nunca se unia)
  -- corregido con `try/finally` alrededor de todo ciclo de vida de una
  `Database` sobre archivo en los tests nuevos.
  - **Correcciones solicitadas al aprobar 3.2 (hechas antes de iniciar 3.3,
    commit separado pequeno con tests)**:
    1. `resume_drawdown_stop` ahora reinicia el pico de equity al equity
       actual y la marca de brecha en curso, y registra el instante de la
       reanudacion en `system_state` -- antes el stop quedaba inerte para
       el resto de la vida de la cuenta tras reanudar (el pico viejo nunca
       se volvia a cruzar). El contador `drawdown_stop_would_have_triggered_count`
       (criterio de paso a dinero real) sigue sin tocarse por una
       reanudacion manual, a proposito.
    2. `PaperBackend.open_position` serializa comprobacion+apertura con un
       `asyncio.Lock` -- antes dos aperturas concurrentes podian leer
       ambas "cupo libre" antes de que ninguna hubiera escrito su fila.
    3. Perdida diaria: el bloqueo por `DAILY_LOSS_LIMIT` ahora queda
       enganchado (persistido) hasta el cambio de dia local, sin importar
       si el PnL flotante se recupera dentro del mismo dia -- antes se
       recalculaba el % contra el equity actual en cada consulta y se
       desactivaba solo.
    4. `strategy=None` ya no salta la lista de elegibilidad por defecto --
       solo una entrada `is_manual=True` la salta. `sl_margin_loss_pct` es
       obligatorio (`ValueError`) para toda entrada no manual. `app/config.py`
       documenta junto a cada campo "_pct" si es fraccion [0,1] o
       porcentaje [0,100], y valida al arrancar los campos de riesgo en
       vivo mas sensibles (`max_drawdown_pct`, `max_daily_loss_pct`,
       `max_capital_pct_per_asset`, `maker_fee_pct`, `taker_fee_pct`,
       `live_sl_margin_cap_pct`) -- deliberadamente NO valida los
       parametros de investigacion del backtest, que varios tests usan a
       proposito con valores fuera de escala para desactivar un tope.
    5. `.env.example` actualizado con `MAX_SAME_DIRECTION_POSITIONS`,
       `LIVE_SL_MARGIN_CAP_PCT`, `DRAWDOWN_STOP_MODE` y
       `REAL_ACCOUNT_ELIGIBLE_STRATEGIES`.
- **3.3 -- Generador de senales en vivo: HECHA.** Nuevo
  `app/trading/signal_generator.py`: `run_signal_generation_cycle` reemplaza
  `Scheduler._evaluate_symbol` (Fase 1, una estrategia sobre un simbolo fijo
  -- `settings.active_strategy`/`settings.symbols` quedan vestigiales, ya no
  se usan) por un generador que en cada tick itera TODO el universo vigente
  (`asset_universe` con `included=1`) x las 6 estrategias de
  `app/strategies/registry.py` en sus timeframes, llamando
  `precompute`+`evaluate` -- mismo codigo de indicadores que el backtest.
  - **Tabla nueva `signals`**: una fila por (simbolo, estrategia, timeframe,
    vela cerrada), HOLD incluido -- `UNIQUE(symbol, strategy, timeframe,
    candle_close_time)` + `INSERT OR IGNORE` (verificado: `cursor.rowcount`
    de aiosqlite reporta correctamente 0 en el duplicado) evita reprocesar
    una vela ya evaluada en un poll anterior. Solo velas CERRADAS
    (`drop_incomplete_last_bar`, reutilizado de `app/market/ohlcv_history.py`
    sin cambios). OHLCV se descarga/cachea con el mismo
    `download_missing`/`get_cached_or_raise` que ya usa el backtest (nunca
    un cliente REST aparte) -- polls sucesivos de la misma vela no vuelven a
    tocar la red. Funding (solo para las 2 estrategias `funding_contrarian_*`)
    se lee de `funding_cache` (nunca red nueva); si esta vacia para un
    simbolo, la estrategia simplemente HOLD (degradacion ya incorporada en
    su diseno de Fase 2).
  - **Filtro de tope de SL** (`signals_discarded_by_sl_cap`, nueva tabla):
    `sl_margin_loss_pct` se calcula con la MISMA formula que el backtest,
    ahora extraida a `app/trading/sl_calc.py` (`fallback_sl_tp_prices`,
    `margin_loss_pct` -- extraccion PURA de `app/backtesting/engine.py`,
    verificada porque sus 23 tests existentes pasan sin tocarlos). Si supera
    `LIVE_SL_MARGIN_CAP_PCT`, la fila de `signals` queda
    `status="DISCARDED_SL_CAP"` y se audita en la tabla nueva; nunca se
    agrupa ni se intenta abrir.
  - **Deduplicacion de senales simultaneas entre estrategias** (adelantada
    de la subfase 3.5 por pedido explicito del usuario, porque este
    generador ya intenta abrir posiciones reales): las senales accionables
    que sobreviven el tope de SL se agrupan por (simbolo, direccion, vela)
    en un `SignalCandidate` con `contributing_strategies` -- nunca se abren
    2-3 posiciones por la misma oportunidad de mercado.
    `pick_representative_strategy` elige, para el intento de apertura real,
    la primera (orden alfabetico) que SI este en
    `REAL_ACCOUNT_ELIGIBLE_STRATEGIES`; si ninguna lo esta, la primera sin
    mas (el motor de riesgo audita el rechazo igual). El desglose COMPLETO
    por estrategia contribuyente es diseno de `shadow_trades` (subfase 3.5),
    fuera de alcance aqui.
  - **`app/core/scheduler.py` reescrito**: `run_cycle` llama al generador,
    agrupa, y por cada candidato cierra la posicion OPUESTA abierta (señal
    contraria) o abre una nueva via `PaperBackend.open_position` --
    `sl_margin_loss_pct` SIEMPRE se envia (nunca `None`), cumpliendo la
    obligatoriedad agregada en el fix de la subfase 3.2. Un candidato que
    falla (rechazo de riesgo o error inesperado) no tumba el resto del ciclo.
  - 29 tests nuevos (`test_sl_calc.py`, `test_signals_repo.py`,
    `test_signal_generator.py`, `test_scheduler.py`): formulas de SL/TP de
    respaldo y riesgo planeado: casos limite LONG/SHORT/leverage/distancia
    cero; dedup de `insert_signal` (estrategia distinta = fila nueva, misma
    clave = `None`); `_evaluate_one` HOLD/accionable-dentro-del-tope/
    accionable-sobre-el-tope/vela-repetida; `_group_actionable_signals`
    (confluencia, simbolos/lados distintos, peor-caso de SL);
    `pick_representative_strategy` (prefiere elegible, fallback alfabetico);
    smoke test de punta a punta con las 6 estrategias REALES sobre universo
    sintetico (vela en formacion excluida, segundo poll sin duplicados, cero
    llamadas a la red tras el primer poll); `Scheduler._process_candidate`
    (abre, revierte, no duplica misma direccion, no tumba el ciclo ante un
    rechazo o un candidato invalido).
  - **Verificado contra una copia temporal de la base real** (universo
    vigente real, 10 simbolos: BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, SOLUSDT,
    TRXUSDT, ZECUSDT, HYPEUSDT, DOGEUSDT, LINKUSDT): ciclo 1 completo en
    14.9s, puebla `signals` con exactamente 70 filas (10 simbolos x 7 pares
    estrategia/timeframe -- `donchian_breakout_20` corre en 4h Y 1d);
    ciclo 2 inmediato (misma vela, sin red) en 0.6s, 70 filas sin cambios
    (confirma "sin duplicados"); 0 candidatos accionables y 0 descartes por
    tope de SL en el mercado real del momento de la corrida (sin señal
    vigente en ninguna estrategia/simbolo -- resultado valido, no un fallo).
- **Revision de 3.3 (commit `0f96935`, pusheado)**: funding en vivo con
  refresco por API publica (`download_missing_funding` con tolerancia =
  intervalo del contrato + `FUNDING_STALE_MARGIN_HOURS`); si el ultimo evento es
  mas viejo, las estrategias `funding_contrarian_*` quedan en HOLD con
  `reason="FUNDING_STALE"`; los bares posteriores al ultimo evento real se marcan
  como aproximados en vivo. Vela obsoleta: `reason="CANDLE_STALE"` y senal
  `STALE_DATA`, contada en `system_state.signals_stale_data_count`. Se elimino
  `SIGNAL_REVERSAL`: una senal contraria con posicion abierta se ignora, igual que
  el backtest. `DECISION_SOURCE` (SIN_LLM) y `AUTO_OPEN_WITHOUT_LLM=false`.
  Migraciones idempotentes para `trades`/`signals` sobre bases existentes.
- **Hallazgo de la revision: error de redondeo en el tope de SL** (ver
  `docs/FASE2_REEJECUCION.md`): el SL de respaldo al 5% a 10x cae exactamente en
  50% de margen; sin redondeo, `50.000000000000014 > 50` descartaba al azar. En la
  base real descarto 2042 entradas de `ema_cross_9_21`, 509 de
  `funding_contrarian_experimental` y 1037 de `funding_contrarian_percentile_experimental`.
  Corregido en `app/trading/sl_calc.py` (compartido por backtest y vivo). La
  reejecucion v2 **ejecutada**: ver el estado de Fase 2 abajo.
- **3.4 -- Monitor de posiciones, feed y reconciliacion: HECHA.**
  - `app/trading/position_monitor.py`: unico que cierra por SL, TP, trailing y
    liquidacion. Modo tick con `stop_engine`. SL/trailing al precio observado
    (peor caso); TP al nominal salvo hueco evidente (`TP_GAP_TOLERANCE_PCT`); la
    liquidacion exige que el mark por REST confirme el cruce (si no confirma o la
    consulta falla, no se liquida). Antiguedad de ticks por simbolo
    (`TICK_STALE_SECONDS`): si se supera, alerta y consulta el precio por REST.
    Equity (pico y linea base del dia) actualizado en cada periodo.
  - Latido del feed: cualquier mensaje del servidor (incluido pong) refresca
    `data_source_health` (`ws_feed`). Sin latido en `WS_STALE_AFTER_SECONDS`, el
    motor de riesgo rechaza entradas con `DATA_STALE`; los cierres nunca se bloquean.
  - **Reconciliacion al arrancar**: reproduce con velas 1m (LAST para SL/TP/
    trailing, MARK para liquidacion) el periodo caido desde el ultimo latido
    persistido, mas el funding de esa ventana. Si faltan mas de 2 velas, la
    posicion queda abierta y se registra CRITICAL.
  - **Slippage** (`app/trading/slippage.py`): misma formula y parametro que el
    backtest (`BACKTEST_SLIPPAGE_BPS`), aplicada en entradas, cierres a mercado,
    SL y TP. El backtest importa las mismas funciones.
  - **Columna `fill_source`** en cada cierre: `TICK` (monitor), `CANDLE_RECON`
    (reconciliacion, precio nominal) o `REST_MARK` (cierre manual).
  - Validacion de niveles al abrir: SL o TP del lado equivocado respecto al
    llenado se rechaza (antes un TP invertido se habria ejecutado en el primer tick).
  - Tests nuevos: monitor (secuencia de ticks que cruza el SL, TP nominal vs hueco,
    trailing, liquidacion con confirmacion REST, feed obsoleto sin bloquear cierres,
    equity periodico), reconciliacion (SL, TP, liquidacion por MARK, funding,
    ventana vacia, velas incompletas), slippage con paridad frente al backtest,
    funding idempotente, trailing y relleno del TP.
  - **Corrida real corta** (copia temporal de la base, WebSocket y REST reales,
    90 s): 57 ticks recibidos, latido siempre por debajo de 8 s, marcas por REST,
    niveles correctos (SL -1%, TP +1%, liquidacion ~76.592 a 10x), reconciliacion
    con 10 minutos de velas 1m reales (sin hueco, `ABIERTA`), cierre manual con
    slippage y `fill_source=REST_MARK`. La corrida no alcanzo SL ni TP en vivo: el
    precio no se movio lo suficiente en 90 s.
  - **Pendiente (no implementado en 3.4)**: funding por evento en vivo sin esperar
    al refresco del cache (la liquidacion usa mark REST, no el mark stream).
    `rest_kline` y `rest_mark` no tienen fila en `data_source_health`; la vigencia
    de velas se controla por senal (`STALE_DATA`).
  - **Ajustes de la revision de 3.4 (hechos)**:
    - *Vela parcial en la reconciliacion*: una vela 1m que empieza antes de la
      apertura no se evalua para precio (una mecha previa podia cerrar una posicion
      que aun no existia; test de regresion que lo reproducia). El funding de esa
      vela si se aplica por tiempo. Riesgo residual: como mucho un minuto.
    - *Consultas REST de ticker unificadas*: una sola consulta por simbolo cada
      2 s (`TICKER_CACHE_SECONDS`) para marcas, antiguedad de ticks y confirmacion
      de liquidacion. Test: una sola llamada por ciclo.
    - *Validacion del TP al abrir*: un TP del lado equivocado se rechaza (antes se
      habria ejecutado en el primer tick; detectado en la corrida real).
  - **RECORDATORIO**: revisar A MANO el primer cierre real por SL, TP y trailing
    (precio de salida, `fill_source`, comisiones, slippage y funding, contra los
    logs y la tabla `trades`) antes de confiar en el monitor sin supervision.
- **3.5 -- Operaciones sombra y reporte por estrategia: HECHA.**
  - `shadow_trades` (tabla nueva): una operacion por senal agrupada
    (simbolo, direccion, vela), margen ilimitado, sin cupos de la cuenta real.
    `signal_group_key` es UNICA: la misma senal nunca genera dos sombras.
  - Mismas funciones que la cuenta paper para llenado, niveles, slippage, funding
    y cierre: `compute_open_fill`, `resolve_trade_levels`, `entry_slippage_usdt`,
    `compute_funding_accrual` y `settle_close` (`app/trading/fills.py`, extraido
    ahora de `PaperBackend.close_if_open`, que lo usa igual). Test de paridad: mismo
    PnL neto que la cuenta paper para la misma senal.
  - `llm_decision`: **SIN_LLM** en todas las filas hasta la 3.6. El plan decia
    "PENDIENTE"; se usa SIN_LLM por indicacion expresa, con la misma funcion (sin
    decision del LLM todavia).
  - Atribucion: una operacion agrupada cuenta en CADA estrategia contribuyente, con
    su resultado completo (`app/trading/shadow_report.py`). El reporte muestra el
    total aparte (sin doble conteo) y el desglose por estrategia.
  - El monitor de posiciones evalua las sombras con las mismas reglas que las reales
    (SL, TP, trailing, liquidacion, funding), con `fill_source` TICK o CANDLE_RECON.
  - El scheduler abre la sombra de cada senal agrupada, tenga o no apertura
    automatica. `executed_in_real_account` queda en 1 si la cuenta real tambien la abre.
  - Verificado sobre una copia de la base real con REST real: 3 grupos, dos de ellos
    con dos estrategias cada uno, cada uno como UNA operacion sombra.
  - Tests: `tests/unit/test_shadow_book.py` (9 tests: paridad, agrupacion, dedup,
    independencia de cupos y de la bandera AUTO, ejecucion marcada, niveles
    invalidos, ciclo de vida con el monitor y atribucion del reporte).
  - **Pendiente**: el reporte se verifica con datos reales cuando el monitor corra
    en vivo; hoy solo hay sombras abiertas de una corrida corta. Sin LLM no hay
    APROBADA/RECHAZADA todavia.
- **Ajustes a la 3.5 (aprobada con dos cambios, mas una documentacion)**:
  1. *Solapes en la sombra*: la sombra NO bloquea. Con el LLM, bloquear sesgaria la
     comparacion APROBADA vs RECHAZADA (una rechazada impediria la siguiente). Se
     corrige en el analisis: N efectivo por conglomerados (operaciones del mismo
     simbolo y direccion con intervalos de vida solapados = una unidad) y bootstrap
     por conglomerados (`app/trading/ai_value.py`). El reporte muestra N bruto y N
     efectivo. El backtest no permite solapes por celda (`FASE2_CRITERIOS.md`, punto 6).
  2. *Grupos mixtos*: el reporte separa grupos de una sola estrategia (comparables con
     el backtest de esa estrategia) de grupos mixtos (no comparables, con aviso).
  3. *Entrada de la sombra*: al precio de cierre de la vela evaluada, sin demora ni
     slippage de llenado. No es comparable con la cuenta real; dentro de la sombra
     APROBADA y RECHAZADA entran igual, asi que la comparacion no se afecta.
  - Regla de decision sobre la IA implementada tal como se fijo: sin IC del 95 % que
    excluya el cero con N efectivo >= 100 por lado, "LA_IA_NO_APORTA_VALOR". Sin
    decisiones del LLM el veredicto es SIN_DATOS.
- **3.6 -- LLM: DISEÑO v2 APROBADO el 2026-10-09, en implementacion por fases**
  (`docs/FASE3_6_LLM.md`). Decisiones cerradas: regla de tres veredictos sobre
  `r = pnl_net_usdt / margin_usdt` (APORTA_VALOR si `lo > 0`; NO_APORTA_VALOR si
  `hi < δ`; INCONCLUSO en el resto o si SIN_LLM > 10 % de la muestra); **δ = 3 % del
  margen por operacion** (no USDT fijos, para que valga con margen 5 o 10); punto de
  analisis unico en 300 conglomerados efectivos por lado; caso `lo > 0` y `hi < δ` ->
  APORTA_VALOR con marca `MAGNITUD_BAJA`; potencia (σ = 44,9 % del margen medido en el
  backtest OOS con margen 10; MDE ≈ 10 % del margen a N=300 con potencia 80 %; un
  efecto de 3 % necesita del orden de 3.500 conglomerados por lado a potencia 80 %, o
  ~1.700 a potencia 50 %); max_tokens 300 (coste maximo 0,0054 USD por llamada con
  Sonnet 5.5); contexto solo con posiciones reales; piloto de 30 a 50 señales con
  prompt congelado y banda de aprobacion 15-85 %; semaforo 4, 20 s por llamada y 60 s
  de retraso maximo para abrir en cuenta real; reporte de SIN_LLM por causa, hora y
  volatilidad; placebo con IC de Wilson. SDK y modelo `claude-sonnet-5-5` autorizados.
  - Correccion de diseño: la sombra se abre al cierre de la vela con SIN_LLM y la
    decision escribe la etiqueta una sola vez (las llamadas son asincronas).
  - Unico pendiente operativo (no bloquea el codigo): confirmar los precios de la
    seccion 0 en la consola antes de la primera llamada real.
  - Implementacion en 3 fases, cada una con su commit: (i) cliente LLM + `llm_logs` +
    etiqueta inmutable + presupuesto + semaforo, sin red; (ii) `ai_value.py` con los
    tres veredictos, potencia y placebo; (iii) `scripts/llm_smoke.py` + modo PILOTO.
    `AUTO_OPEN_WITHOUT_LLM` se mantiene en `false` durante toda la 3.6.
  - **Correccion al diseño durante la implementacion de (i), verificada con la skill
    `claude-api` y la documentacion vigente de Anthropic (2026-10-09):** Sonnet 5.5
    rechaza con `400` un `temperature`/`top_p`/`top_k` distinto del de la API -- el
    diseño original pedia `temperature = 0.0`, ya invalido. Se usa
    `thinking={"type": "between_tools"}` con `output_config={"effort": "low"}` (el
    ajuste de menor razonamiento en este modelo; `{"type": "disabled"}` tambien da
    `400`); sin herramientas declaradas no genera bloques de razonamiento extendido,
    asi que los 300 tokens de `max_tokens` quedan enteros para el JSON de salida.
    `docs/FASE3_6_LLM.md` seccion (b) actualizada.
  - **Fase (i) completada** (`app/llm/`: `schemas.py`, `client.py` con
    `FakeLlmClient`/`build_anthropic_client`, `_anthropic_client.py` con el SDK real
    -- import diferido, nunca se carga en los tests --, `budget.py`,
    `decision_service.py`). Tabla `llm_logs` y disparador `llm_decision_inmutable`
    en `app/persistence/database.py`; `shadow_repo.set_llm_decision`. Config nueva:
    `LLM_MAX_TOKENS=300`, `LLM_TIMEOUT_SECONDS=20`, `LLM_MAX_CONCURRENCY=4`,
    `LLM_REAL_MAX_DELAY_SECONDS=60`, `LLM_PRICE_INPUT_PER_MTOK=2.0`,
    `LLM_PRICE_OUTPUT_PER_MTOK=10.0`. Dependencia nueva `anthropic>=1.10,<2` en
    `pyproject.toml` (SDK 1.x, usa `httpx2`, no choca con el `httpx` del proyecto).
    `LlmDecisionService` y `real_open_allowed` **no estan conectados a
    `app/core/scheduler.py`**: nada de esto corre todavia en el ciclo del bot.
    31 tests nuevos (`test_llm_schemas.py`, `test_llm_budget.py`,
    `test_llm_decision_service.py`), todos sin red (cliente falso o fabrica que
    lanza si se invoca); cubren: etiqueta correcta en cada caso, una fila de
    `llm_logs` por llamada, fallo -> SIN_LLM nunca RECHAZADA, etiqueta inmutable
    (el disparador bloquea un `UPDATE` directo), presupuesto agotado sin construir
    ni llamar al cliente real, y concurrencia (10 llamadas simultaneas con
    presupuesto para 3) sin superar el tope diario. Suite completa: 394 passed,
    2 skipped; `ruff check .` limpio.
  - **Nota de instalacion:** el venv del proyecto (`.venv`) no tiene `pip` como
    modulo; la dependencia se instalo con
    `uv pip install --python .venv/Scripts/python.exe --system-certs "anthropic>=1.10,<2"`.
    Quien reproduzca esto en otra maquina deberia poder usar `uv sync` desde la raiz.
  - **Fase (i) APROBADA el 2026-10-09, con 3 ajustes:**
    1. `AnthropicLlmClient` fija `max_retries=0`: con el valor por defecto del SDK
       (2 reintentos) un timeout de 20 s podia estirarse hasta 60 s, justo el limite
       de `LLM_REAL_MAX_DELAY_SECONDS`. Test mecanico sin red
       (`test_llm_anthropic_client.py`) que confirma `client._client.max_retries == 0`.
    2. TIMEOUT y ERROR_HTTP ya no registran `cost_usd=0`: un fallo de nuestro lado no
       garantiza que Anthropic no facturara nada del otro lado. Ahora registran el
       coste estimado solo de entrada (`estimated_input_tokens`), con "(coste
       estimado)" en `error`. Test de regresion que muestra el efecto practico: sin
       este ajuste, una segunda llamada que no deberia caber en el presupuesto
       pasaba de todos modos.
    3. El prompt exige JSON crudo, sin backticks ni ```json; documentado en la
       seccion (b). Ya funcionaba sin cambios de codigo (el parser JSON estricto de
       pydantic rechaza el texto envuelto en backticks igual que cualquier otro
       texto fuera del objeto) -- se agregaron los dos casos explicitos a
       `test_llm_schemas.py` para dejarlo probado, no solo documentado.
    - Suite completa tras los 3 ajustes: 398 passed, 2 skipped; `ruff check .` limpio.
  - **Fase (ii) completada** (`app/trading/ai_value.py`, reescrito): tres veredictos
    `APORTA_VALOR`/`NO_APORTA_VALOR`/`INCONCLUSO` con marca `MAGNITUD_BAJA` (`lo > 0`
    y `hi < δ`); `Δ` ahora sobre `r = pnl_net_usdt / margin_usdt` (fraccion del margen,
    no USDT: comparable entre margen 5 y 10); `δ = 0.03` y `MIN_EFFECTIVE_N = 300`
    (antes 100) como parametros de la funcion, igual que ya era `min_effective_n`;
    tope de SIN_LLM del 10 % evaluado ANTES que el intervalo (si se supera,
    INCONCLUSO sin mirar el IC); nueva `placebo_calibration` (relabelado por
    conglomerado, 1.000 repeticiones por defecto, IC 95 % de Wilson de la
    frecuencia, sin depender de scipy). Se elimina el veredicto `SIN_DATOS` y el
    antiguo `LA_IA_NO_APORTA_VALOR`: quedan los tres de arriba, con el caso
    "sin datos en ambos lados" dentro de `INCONCLUSO`.
    - `app/persistence/repositories/llm_logs_repo.py`:
      `get_piloto_signal_group_keys(db)` -- el filtro de `llm_logs.fase = 'PILOTO'`
      lo hace el llamador (pasa `piloto_keys` a `ai_value_verdict`), no la funcion de
      analisis, que sigue sin tocar la base.
    - `app/trading/shadow_report.py`: `build_report` ahora pasa TODAS las
      operaciones sombra (abiertas y cerradas, no solo cerradas) a
      `ai_value_verdict`, porque el SIN_LLM se cuenta sobre el total de grupos del
      periodo, no solo sobre los cerrados; y ya excluye el piloto. El markdown
      muestra el SIN_LLM (N y %) y la marca `MAGNITUD_BAJA`.
    - `tests/unit/test_ai_value.py` reescrito (24 tests): N efectivo bajo,
      APORTA_VALOR con y sin `MAGNITUD_BAJA`, NO_APORTA_VALOR (sin diferencia y
      cuando la IA resta valor), SIN_LLM > 10 % con una señal que de otro modo
      seria clara, SIN_LLM por debajo del tope, todos APROBADA/todos RECHAZADA,
      exclusion del piloto, y placebo (frecuencia baja sin efecto real,
      determinismo). Los tests de placebo usan menos repeticiones y
      `min_effective_n` mas chico que en produccion para que la suite corra
      rapido (documentado en el docstring de `placebo_calibration`).
    - **Smoke de punta a punta** con `scripts/shadow_report.py` sobre una copia de
      la base real (ruta distinta de `settings.database_path`, verificada con
      assert): corre sin errores. La base real a esta fecha solo tiene 3 grupos
      sombra, todos abiertos y SIN_LLM (el LLM no esta conectado todavia), asi que
      el veredicto es INCONCLUSO por el tope de SIN_LLM al 100 % -- resultado
      correcto para el estado actual, no un fallo.
    - `AUTO_OPEN_WITHOUT_LLM` sigue en `false`; nada de la fase (ii) toca
      `app/core/scheduler.py`. No se hicieron llamadas reales a la API.
    - Suite completa: 409 passed, 2 skipped; `ruff check .` limpio.
  - **Fase (ii) APROBADA el 2026-10-09, con 4 ajustes:**
    1. **`measurement_keys`** en `ai_value_verdict`: sin el, `shadow_trades` de antes
       de la 3.6 (o de otra version del prompt) contaban como SIN_LLM para siempre,
       inflando el tope del 10 % sin remedio. Nuevo
       `llm_logs_repo.get_measurement_signal_group_keys(db, prompt_sha256=None)`
       (usa la version mas reciente con `fase='MEDICION'` si no se fija una) --
       usado en `shadow_report.build_report`. Un fallo (TIMEOUT/ERROR_HTTP/
       BUDGET_EXCEEDED) SI deja fila en `llm_logs`, asi que su grupo entra en
       `measurement_keys` y sigue contando como SIN_LLM (correcto: se intento
       decidir). 7 tests nuevos (`test_ai_value.py`, `test_llm_logs_repo.py` nuevo).
    2. Typo "adem{as" -> "además" en el docstring.
    3. `placebo_calibration` confirmado que NUNCA corre dentro de `build_report`
       (no lo hacia). Comando aparte: `scripts/placebo_check.py --db COPIA
       [--n-repeats N]`, con el tiempo aproximado documentado en su docstring.
       Probado en `--n-repeats` por defecto (1000) contra una copia de la base
       real: termina sin errores (no hay filas de medicion todavia, lo dice y
       sale).
    4. Nota nueva en la seccion (j): a N=300/lado el IC mide ±7 puntos del margen,
       asi que `NO_APORTA_VALOR` solo es alcanzable si la IA resta valor con
       claridad (`Δ < -4 %` aprox.); lo esperable es `INCONCLUSO` o `APORTA_VALOR`.
    - Suite completa tras los 4 ajustes: 416 passed, 2 skipped; `ruff check .` limpio.
  - **Fase (iii) completada (2026-10-09), en dos partes:**
    - **(A)** `app/llm/prompts.py`: `SYSTEM_PROMPT`/`PROMPT_VERSION`/`PROMPT_SHA256`
      fijos; exige JSON crudo (prohibe backticks explicitamente); `build_user_message`
      (JSON ordenado, nulos explicitos); `estimate_input_tokens` (caracteres/3,
      conservador); `build_features_from_shadow_trade(db, trade)` junta lo YA
      guardado (`signals.indicators_json` por estrategia contribuyente,
      `sl_margin_loss_pct`, identidad) y deja en `null` EXPLICITO lo que este
      proyecto todavia no calcula (funding, volatilidad, correlacion con BTC,
      posiciones reales abiertas) -- no se inventa nada. Nueva
      `signals_repo.get_by_symbol_strategy_candle`. 7 tests nuevos
      (`test_llm_prompts.py`).
    - **(B)** `scripts/llm_smoke.py`: una sola llamada (`--max-calls`, por defecto 1,
      tope duro 3) sobre un grupo de señal ya guardado en una COPIA de la base
      (`--db`, assert de ruta distinta de `settings.database_path`). `--dry-run`
      por defecto (`FakeLlmClient`, sin red ni coste); real solo con `--real
      --confirm-real` juntos. Reutiliza `LlmDecisionService` (fase i): la reserva
      de presupuesto corre dentro, antes de llamar. Imprime modelo, `prompt_version`,
      tokens reales, coste, latencia, respuesta cruda y si validó -- nunca la API key.
    - **(C)** `scripts/llm_pilot.py`: igual que (B) pero en lote sobre hasta
      `--max-calls` grupos (por defecto 30, tope duro 50) sin fila en `llm_logs`,
      con `fase='PILOTO'`. Reporta validez del JSON, tokens y latencia (media y p95),
      tasa de APROBAR, coste real total, y si cae en la banda 15 %-85 %. Documentado
      en `docs/FASE3_6_LLM.md` seccion (k) que es **por repeticion, no en vivo**: no
      esta conectado a `app/core/scheduler.py` (conectar eso es la decision aparte
      que activaria `AUTO_OPEN_WITHOUT_LLM`, no tomada).
    - **Probados ambos en `--dry-run` contra copias de la base real** (regla de
      CLAUDE.md): `llm_smoke.py` con 1, luego con hasta 3 grupos (salta los que ya
      tienen fila en `llm_logs`, probado al reusar la misma copia); `llm_pilot.py`
      con los 3 grupos reales disponibles (avisa que pidio 30 y solo habia 3, no es
      error) y de nuevo con 0 disponibles (tambien termina limpio). Las validaciones
      de argumentos (`--max-calls` fuera de rango, `--real` sin `--confirm-real`,
      `--confirm-real` sin `--real`) se probaron explicitamente. Ninguna llamada
      real a la API.
    - `AUTO_OPEN_WITHOUT_LLM` sigue en `false`; nada de la fase (iii) toca
      `app/core/scheduler.py`.
    - Suite completa: 423 passed, 2 skipped; `ruff check .` limpio.
    - **Pendiente operativo de Renzo:** correr `scripts/llm_smoke.py --real
      --confirm-real` sobre una copia, comparando el coste con su consola de
      Anthropic, antes de confiar en el calculo de precios.
  - **Subfase 3.6 (iv) completada (2026-10-09): features de mercado completas,
    antes del piloto.** La revision de la fase (iii) encontro que
    `build_features_from_shadow_trade` solo entregaba identidad, indicadores y
    `sl_margin_loss_pct`; funding, volatilidad, correlacion con BTC y posiciones
    abiertas quedaban en `null` fijo (no calculadas), faltaban timeframe y niveles,
    y no existian las 3 pruebas de fuga de la seccion (a). Como congelar el prompt
    implica una medicion de 30+ dias y cualquier cambio la reinicia, se completo
    esto antes de congelar nada.
    1. Nuevo `app/llm/market_features.py`: timeframe de referencia fijo "1h" (el
       mas fino que ya se cachea para todo el universo, via
       `mean_reversion_rsi14_bb20`) para ATR/volatilidad/correlacion, independiente
       del timeframe propio de cada estrategia contribuyente. `fetch_closed_candles`
       nunca devuelve una vela que no cerro antes o en el instante evaluado
       (`open_time + duracion <= as_of_ms`). `compute_atr14_pct` (ATR simple de 14
       rangos, no el suavizado de Wilder -- documentado como simplificacion),
       `compute_returns_std` (30 retornos logaritmicos), `compute_btc_correlation`
       (Pearson sobre retornos, alineado por `open_time`, a 30 y 100 velas, sin
       numpy/scipy), `compute_funding_features` (ultimo evento de `funding_cache`
       con `funding_time <= vela evaluada`, intervalo del contrato desde
       `contract_specs_cache`, `funding_is_approximated` si el evento es mas viejo
       que el intervalo + `FUNDING_STALE_MARGIN_HOURS`), `compute_open_real_positions`
       (nuevo `trades_repo.get_open_positions_as_of(db, as_of)`: `opened_at <= as_of`
       y sigue abierta o cerro despues -- NUNCA `shadow_trades`). Todo `None`
       explicito si falta el dato (velas insuficientes, sin funding cacheado).
    2. `app/llm/prompts.py`: `build_features_from_shadow_trade` ahora pide
       `settings` y agrega `timeframe_by_strategy`, `sl_price`, `tp_price`,
       `trailing_distance`, `liq_price_estimated` (de la propia sombra, fijados al
       abrir -- nunca `effective_stop`/`best_price`, que siguen al precio
       POSTERIOR a la decision) y las features de `market_features`. Nuevo
       `signals_repo.get_by_symbol_strategy_candle` (ya existia, reusado para
       timeframe tambien).
    3. `SYSTEM_PROMPT` ampliado: contexto de la cuenta (paper, 10x aislado, SL hasta
       50 % del margen), que mide cada estrategia contribuyente, que conviene
       sopesar (tendencia/volatilidad, funding en contra, correlacion con BTC,
       posiciones reales abiertas, riesgo del SL), que `null` nunca se infiere, y
       que NO hay una tasa de aprobacion objetivo. Sigue en `prompt_version =
       "signal_review_v1"`: todavia no se uso en ninguna medicion real (solo
       copias), y lo que de verdad evita mezclar dos versiones es `PROMPT_SHA256`
       (via `measurement_keys`), no la etiqueta legible.
    4. `scripts/llm_smoke.py`: imprime tambien el `user_message` completo enviado
       (son datos, no secretos).
    5. `scripts/llm_pilot.py`: cuenta y reporta, por campo, en cuantos de los
       grupos procesados quedo en `null` (y, para `indicators_by_strategy`, cuantos
       slots por estrategia sin fila en `signals`) -- para saber con que
       informacion real decidio el piloto.
    6. Documentado en `docs/FASE3_6_LLM.md` (seccion e): el tope diario es
       `SUM(cost_usd)` sobre `llm_logs` de la base que recibe `--db`, no
       acumulativo entre copias distintas; la proteccion real entre corridas con
       copias es `--max-calls` y el limite de la consola de Anthropic.
    - **Pruebas de fuga (seccion a, las 3 obligatorias) en
      `tests/unit/test_llm_market_features.py`:** velas futuras con valores
      extremos no cambian ATR ni la desviacion de retornos; la serie "truncada" al
      cierre evaluado da exactamente los mismos bares y el mismo ATR que leerla de
      una base con el resto de la serie "completa" ya cargado; ninguna vela
      devuelta tiene `open_time` posterior al cierre evaluado (ni, mas estricto,
      sin cerrar a ese instante). 16 tests en total en ese archivo (incluye ATR,
      desviacion, correlacion, funding y posiciones reales). `test_llm_prompts.py`
      ampliado a 11 tests (niveles planeados, contexto del prompt, sin tasa
      objetivo, nulos de mercado sin datos cacheados).
    - **Smoke de punta a punta contra copias de la base real** (regla de
      CLAUDE.md): `llm_pilot.py` con los 3 grupos reales -- 0/5 slots de
      indicadores en null (ya habia fila en `signals` para los 5), solo
      `trailing_distance` en null para los 3 (esa estrategia no la usa); `llm_smoke.py`
      con 1 grupo, imprimiendo un `user_message` real con ATR, RSI, bandas de
      Bollinger, correlacion con BTC (0.73/0.27), funding, niveles y 0 posiciones
      reales abiertas. Ambos sin red ni llamadas reales.
    - `AUTO_OPEN_WITHOUT_LLM` sigue en `false`; nada de la fase (iv) toca
      `app/core/scheduler.py`. No se hicieron llamadas reales a la API.
    - Suite completa: 443 passed, 2 skipped; `ruff check .` limpio.
  - **Correccion de unidades en `funding_rate_pct` (revision de la fase iv,
    2026-10-10):** `market_features.compute_funding_features` devolvia la
    fraccion cruda de `funding_cache` (0,0001) sin convertir a porcentaje; el
    LLM habria visto un funding 100 veces menor que el real.
    `signal_generator.py` linea ~351 (`signals.funding_rate_pct`) ya multiplica
    por 100 -- se corrigio `market_features.py` para usar la misma convencion
    (`latest_rate * 100`), documentado en el docstring de la funcion y en
    `docs/FASE3_6_LLM.md` seccion (a): "`funding_rate_pct` está en porcentaje
    (0,01 = 0,01 %)". Revisados `prompts.py`, `llm_pilot.py`/`llm_smoke.py` y el
    doc: ninguno mas asumia la unidad cruda. `PROMPT_VERSION` se queda en
    `signal_review_v1` (no se tocó `SYSTEM_PROMPT`, el hash no cambia).
    - Tests corregidos en `test_llm_market_features.py` (0,0001/0,0002 de
      fraccion -> 0,01/0,02 de porcentaje) y nuevo test explicito de unidades
      (`test_funding_rate_pct_is_in_percent_not_the_raw_cache_fraction`).
    - Probado `llm_smoke.py` en `--dry-run` contra una copia fresca de la base
      real: `funding_rate_pct` pasa de 0,0001 (error) a 0,01 (correcto) para el
      mismo grupo real (DOGEUSDT SHORT).
    - Suite completa: 444 passed, 2 skipped; `ruff check .` limpio. Sin llamadas
      reales, sin tocar el scheduler.
  - **Recalibracion de `estimate_input_tokens` con el primer smoke real (2026-10-10):**
    el smoke `--real` sobre DOGEUSDT SHORT (con las features completas de la fase iv)
    midio **1.393 tokens de entrada, 145 de salida, 0,004236 USD, 2,5 s de latencia**
    -- confirma el precio de Sonnet 5.5 ($2/$10 por MTok:
    `1.393·2/1e6 + 145·10/1e6 = 0,004236`). La estimacion de caracteres/3 daba 1.048,
    un 25 % por debajo de lo real (unos 2,3 caracteres por token en este prompt, no 3).
    `estimate_input_tokens` (`app/llm/prompts.py`) pasa a **caracteres/2** (da ~1.570
    para el mismo mensaje, por encima de lo medido: vuelve a ser conservadora en vez
    de subestimar `coste_max` frente al presupuesto). `docs/FASE3_6_LLM.md` seccion
    (e) anota el dato real y el gasto esperado con el (no el maximo de `max_tokens=300`,
    que sigue fijo a proposito para el presupuesto): ≈0,039 USD/dia a 9,32 grupos/dia
    (≈1,18 USD/mes), ≈0,106 USD/dia a 25 (≈3,18 USD/mes); con 1 USD/dia caben ≈236
    llamadas. Es un solo dato, no una medicion: el piloto tiene que recalibrar con sus
    propios tokens medios.
    - Test actualizado (`test_estimate_input_tokens_is_chars_over_two`).
    - Suite completa: 444 passed, 2 skipped; `ruff check .` limpio. Sin llamadas
      reales, sin tocar el scheduler.
- **Valores por defecto alineados con `docs/FASE3_PLAN.md` (previo a implementar 3.6)**:
  `DEFAULT_MARGIN_USDT` pasa de 10 a **5** (seccion 8 del plan; la cartera de Fase 2 se
  arruina a 10 USDT/3 posiciones en 3 de 4 estrategias por reglas) y
  `ANTHROPIC_SONNET_MODEL` pasa de `claude-sonnet-5` (sin verificar) a
  **`claude-sonnet-5-5`** (verificado, `docs/FASE3_6_LLM.md` seccion 0), en
  `app/config.py` y `.env.example`. Ninguna prueba dependia del valor implicito de
  margen (las 9 que usan `DEFAULT_MARGIN_USDT` lo fijan explicitamente); suite completa
  sigue en 363 passed, 2 skipped. Los resultados ya documentados de la Fase 2
  (`docs/FASE2_*.md`) se corrieron con margen 10 y no se tocan; una recorrida futura del
  backtest sin fijar `DEFAULT_MARGIN_USDT=10` explicitamente usara 5. Nota añadida en
  `docs/SPEC.md` junto al valor original.
- **Universo (arranque del bot, previo a la 3.6)**: commit `c5b432b`.
  - Comprobacion de antiguedad: `is_universe_stale` con `UNIVERSE_STALENESS_HOURS=48`.
    El generador de señales no consulta la red si el universo esta obsoleto.
  - Refresco diario con `UniverseRefresher` (cada hora revisa si el snapshot supera
    `UNIVERSE_REFRESH_HOURS=24`). Si CoinGecko o Bitunix fallan, se conserva el snapshot
    anterior y el error queda en `system_state.universe_last_refresh_error`; un exito
    posterior limpia ese error. Corre como tarea aparte: no bloquea cierres.
  - Un snapshot sin ningun simbolo incluido se rechaza.
  - Smoke test sobre una copia de la base (no la base de trabajo): antes, snapshot del
    2026-10-01, obsoleto; despues, refrescado con 10 simbolos incluidos y no obsoleto.
    Sin `COINGECKO_API_KEY` en el smoke (el asistente no lee `.env`); la clave del `.env`
    del bot no se probo.
  - Tests: `tests/unit/test_universe_refresh.py` (12). Suite completa: 363 passed,
    2 skipped; `ruff check .` limpio.
  - `httpx` queda en WARNING en `setup_logging`.
- 3.6 a 3.8: pendientes.

## Incidente de estabilidad (2026-10-10): reconciliacion e integridad de velas

Al arrancar el bot de estabilidad (copia de la base, ~5,75 dias apagado) la
reconciliacion fallo con CRITICAL para las 3 sombras abiertas (ids 1 XRPUSDT,
2 TRXUSDT, 3 DOGEUSDT) por "velas 1m LAST_PRICE incompletas". El monitor, sin
ticks desde el arranque, cerro trade=3 (TP) y trade=1 (SL) con el precio REST
del momento del reinicio, no con el nivel de SL/TP real.

**Diagnostico (ver `docs/FASE2_INTEGRIDAD_VELAS.md` para el detalle completo):**
- Cuantificado con los propios fee/slippage de cada trade: trade 3 cerro en
  +9,99 USDT (~100 % del margen) contra un ~+1,70 USDT (~17 %) si hubiera
  cerrado en el nivel del TP -- 5,9x. Trade 1 cerro en -7,05 USDT (~-70 %)
  contra un ~-5,22 USDT (~-52 %, el tope de diseño del 50 % mas fees/slippage)
  si hubiera cerrado en el SL -- 35 % mas perdida de la prevista. Trade 2
  (TRXUSDT) sigue OPEN: no se sabe si en esos dias toco SL o TP, no hay
  evidencia de que no lo haya hecho.
- El mismo mecanismo de reconciliacion corre igual para sombras y reales
  (`PositionMonitor._reload_trades` carga ambas en la misma estructura):
  confirmado con datos reales, no hipotetico.
- Verificado EN VIVO contra la API publica de Bitunix (lectura, sin
  credenciales): la semantica de `endTime` es estrictamente exclusiva sobre
  `open_time` (`open_time < endTime`) -- la formula de paginacion existente
  (`cursor = oldest_time - 1`) ya era correcta bajo esa semantica.
- La causa real de las velas 1m faltantes (40-41 por simbolo, aisladas, cada
  ~200 minutos): Bitunix, en respuestas de `limit=200` sin `startTime` (el
  patron real de `_download_range`), a veces omite una vela real que SI esta
  presente si se pide con una ventana chica -- confirmado en vivo, 3 veces, sin
  poder determinar una regla exacta ni encontrar documentacion oficial sobre
  esto. No es un bug de la formula de paginacion.

**Implementado (Etapa 1, sin tocar la logica de SL/TP ni la exigencia de
integridad -- eso es la Etapa 2, pendiente de aprobacion aparte):**
- `app/market/ohlcv_history.py`: `find_gaps`/`fill_gaps` (reparacion con
  pedidos estrechos, agrupando huecos contiguos) y `download_missing` llama a
  `_verify_and_repair` al final (haya tocado la red o no) -- una cache vieja
  con huecos se autorepara en la siguiente llamada normal. Docstring del
  modulo actualizado con la semantica verificada y el hallazgo.
- `scripts/repair_ohlcv_gaps.py`: repara huecos de un simbolo/intervalo/
  price_type sobre una copia (assert de ruta).
- **Reparados los 3 simbolos del incidente** sobre una copia de
  `data/minerva_estabilidad.db`: 244 huecos (40 DOGEUSDT + 41 TRXUSDT + 41
  XRPUSDT, x2 por LAST/MARK), **244 reparados, 0 sin reparar** -- verificado
  con una consulta SQL independiente. Confirmacion de punta a punta: se
  reconstruyo el escenario exacto (`_reconcile_trade` para TRXUSDT con la
  ventana completa de 5,75 dias) sobre la copia ya reparada -- resultado
  `"ABIERTA"` (reconcilio con velas, sin lanzar), en vez del `CRITICAL`
  original.
- **Cuantificados los huecos de las series 1h/4h/1d de la Fase 2**
  (`data/backups/minerva_fase2_v2.db`, solo lectura): 236 huecos en 60
  combinaciones simbolo/intervalo/price_type, concentrados en ZECUSDT (142 en
  1h, 34 en 4h, 4 en 1d) y LINKUSDT (40 en 4h). A diferencia de los del
  incidente, son bloques CONTIGUOS (no aislados) y de 2023 -- verificado en
  vivo que Bitunix tampoco los devuelve hoy: son un hueco real y permanente
  del historial del exchange, no el hallazgo de arriba. `fill_gaps` repara 0
  de esos 236 (correctamente: no hay nada que reparar). `get_cached_or_raise`
  (el que usa el motor de backtest) no los detecta porque solo verifica los
  bordes del rango pedido, no la densidad interna.
  - **Efecto estimado** (subconjunto representativo -- ZECUSDT, sus 7 celdas
    estrategia/timeframe -- sin relanzar el backtest completo): resultados
    "antes" y "despues" de intentar `fill_gaps` son IDENTICOS (nada reparable).
    Esos 7 casos ya estaban fuera de los criterios de la Fase 2 por otras
    razones ya documentadas; estos huecos no cambian ninguna conclusion de
    `docs/FASE2_REEJECUCION.md`.
  - Pendiente de decision, no implementado: si vale la pena que el motor de
    backtest detecte y reporte huecos internos (no solo de borde). No se toco
    el motor ni se corrio la grilla completa (se pidio no hacerlo sin
    consultar).
- Tests nuevos en `tests/unit/test_ohlcv_history.py` (13 nuevos, 29 en total
  en el archivo): `find_gaps`/`fill_gaps` puros, y un cliente falso
  (`FlakyRestClient`) que simula el hallazgo real para 1m/1h/4h/1d, probando
  que `download_missing` termina sin huecos en los 4 casos y no lanza cuando
  un hueco es genuinamente irreparable.
- Suite completa: 457 passed, 2 skipped; `ruff check .` limpio.

**Añadido 0 (antes de la Etapa 2, pedido junto con su aprobacion):**
`_verify_and_repair` escaneaba TODA la serie cacheada en cada llamada de
`download_missing` -- con el generador de senales llamandola cada ciclo, un
hueco permanente de hace años (seccion 5 de arriba) se reintentaria contra la
API en cada ciclo, e inundaria el log con el mismo WARNING. Corregido:
- `_verify_and_repair` ahora solo revisa la VENTANA PEDIDA (`start_time`,
  `end_time` del llamador), no toda la serie.
- Tabla nueva `ohlcv_unrepairable_gaps` (huecos ya confirmados irreparables,
  con `last_attempted_at`): no se reintentan hasta pasadas
  `GAP_RETRY_COOLDOWN_HOURS` (24h), y el WARNING de "no se pudo reparar" se
  registra una sola vez al confirmarlo, no en cada ciclo.
- `fill_gaps` ahora parte una racha contigua mas larga que
  `GAP_FILL_CHUNK_BARS` (14) en varios pedidos -- antes `limit=20` la truncaba
  en silencio y nunca reintentaba el resto.
- `_align_up`/`_align_down` (recorte de la ventana a la grilla) se anclan a
  `min_cached` (un dato real), no a la grilla de epoca 0 -- mismo resultado en
  produccion (las velas reales de Bitunix SI caen en esa grilla, verificado),
  pero no depende de ese supuesto para series que no caigan ahi.
- Bug de fidelidad de tests encontrado y corregido en el camino (no de
  produccion): el `BASE_MS` sintetico de `tests/unit/test_ohlcv_history.py` no
  estaba alineado a la grilla de 4h, lo que rompia `_align_up`/`_align_down` y
  generaba huecos falsos (y llamadas de red de mas) solo en esa prueba.

**Implementado (Etapa 2, aprobada con los dos añadidos de arriba):**
- **a. Tolerancia recortada** (`app/trading/position_monitor.py`,
  `_residual_gaps_are_tolerable`): tras `fill_gaps`, solo se tolera un hueco
  residual de LAST_PRICE si es AISLADO, su minuto existe en MARK_PRICE, y el
  total no supera `max(2, 0,1 % de la ventana)`. Si se tolera, esa vela se
  SUSTITUYE por la de MARK_PRICE para evaluar SL/TP/liquidacion, con
  `fill_source = CANDLE_RECON_MARK_SUBSTITUTE`. Cualquier otro caso sigue
  marcando la reconciliacion como fallida (ya no solo CRITICAL en el log).
- **b. Marcar y excluir**: columnas `reconciliation_failed`/
  `reconciliation_attempts` en `trades` y `shadow_trades`.
  `shadow_repo.get_unreliable_signal_group_keys` junta los grupos con
  reconciliacion fallida (en curso o cerrados `RECONCILE_FAILED`), y
  `ai_value_verdict`/`shadow_report.build_report` los excluyen con el
  parametro `unreliable_keys`, SEPARADO de `piloto_keys`.
- **c. Comportamiento mientras esta marcada, con reintentos**: una SOMBRA se
  CONGELA (`_evaluate` no la toca por tick) y se reintenta la reconciliacion
  completa cada `RECONCILE_RETRY_SECONDS` (300s) y al arrancar; tras
  `MAX_SHADOW_RECONCILE_ATTEMPTS` (3) intentos fallidos se cierra
  administrativamente SIN PnL (`close_reason=RECONCILE_FAILED`,
  `ShadowBook.close_unreliable`). Una posicion REAL sigue vigilada en vivo
  indefinidamente (nunca se congela ni se cierra sola): solo queda marcada, y
  un cierre por tick mientras lo esta usa `fill_source=TICK_UNRECONCILED` en
  vez de `TICK` (mismo precio, misma regla de peor caso, auditable aparte).
  Sigue contando en `compute_open_real_positions`: el LLM debe ver la
  exposicion real aunque este marcada.
- **d. Tests**: 8 nuevos en `tests/unit/test_position_monitor.py` (función
  pura de tolerancia, sustitucion por MARK_PRICE, marcado en vez de solo
  loguear, congelamiento de sombra marcada, `TICK_UNRECONCILED` en reales,
  reintento exitoso limpia la marca, cierre sin PnL tras 3 intentos, y el caso
  de punta a punta: reconciliacion falla -> sombra marcada y sin cierre por
  tick -> excluida del veredicto). 2 nuevos en `tests/unit/test_ai_value.py`
  (`unreliable_keys` excluye, y es independiente de `piloto_keys`).
- **e. Huecos internos de la Fase 2, solo reporte** (decidido explicitamente:
  sin cambiar comportamiento ni relanzar la grilla): `check_series_availability`
  ahora registra INFO/WARNING con la cuenta de huecos internos de cada serie
  cuyos bordes ya daba por completos, sin tocar su contrato de retorno --
  `scripts/run_backtest.py` ya la llama para validar de antemano, asi que
  queda avisado sin cambiar su codigo. `docs/FASE2_INTEGRIDAD_VELAS.md`
  (secciones 5 y 6) deja escrito que los resultados de la Fase 2 se calcularon
  con esas 236 velas ausentes, sin cambiar ninguna conclusion.
- Suite completa: 467 passed, 2 skipped; `ruff check .` limpio. Commits:
  `160c8b9` (Añadido 0), `761cd19` (columnas), `75254a5` (2a/2c), `af9d590`
  (2b), `4c43e18` (2e + documentacion).
- **Smoke de punta a punta** (copia de `data/minerva_estabilidad.db`, con
  WAL/SHM, assert de ruta distinta a `settings.database_path`, red real
  publica sin credenciales): la sombra TRXUSDT (id 2, todavia OPEN en la base
  real) reconcilio una ventana de ~35 min desde el ultimo `monitor_last_seen_ms`
  con outcome `"ABIERTA"` y `reconciliation_failed=False` -- sin ERROR.
  Confirma que el camino normal (sin huecos que tolerar) sigue funcionando
  igual con todo lo nuevo de la Etapa 2 encima.

**Revision de la Etapa 2 (misma fecha): aprobada con tres correcciones antes
de reiniciar el bot.**
- **Correccion 1 -- reintentar desde la ventana de la falla, no desde
  `opened_at`**: `_attempt_reconcile_retry` llamaba `_reconcile_trade(trade,
  None, now_ms)`, que resuelve a `start_ms = opened_ms` -- cada reintento
  rejugaba TODA la vida de la posicion con el `effective_stop`/`best_price`
  ACTUALES (ya avanzados por trailing) contra velas viejas, de antes de que
  el stop avanzara: una mecha vieja podia cruzar el stop de HOY y cerrar la
  posicion por error. Dos columnas nuevas, fijadas SOLO en la primera falla
  de la racha (no se mueven en fallas posteriores ni entre reinicios):
  `reconciliation_window_start_ms` (desde donde reintentar) y
  `reconciliation_first_failed_at_ms` (cuando empezo la racha, para la
  correccion 2). `reconcile_on_startup` tambien corregido: si una posicion YA
  esta marcada, usa esa ventana persistida en vez del `monitor_last_seen_ms`
  global (que para ella ya avanzo de mas).
- **Correccion 2 -- rendirse por tiempo, no solo por intentos**: 3 intentos
  cada 300s son ~10 min; un corte de internet o de la exchange de esa
  duracion descartaria sombras sanas. Nueva constante
  `MIN_SHADOW_RECONCILE_GIVE_UP_HOURS = 6.0`: una sombra solo se cierra como
  `RECONCILE_FAILED` con `reconciliation_attempts >=
  MAX_SHADOW_RECONCILE_ATTEMPTS` (3) Y `(now_ms -
  reconciliation_first_failed_at_ms) >= 6h` a la vez. Compromiso
  documentado: puede quedar congelada hasta 6h antes de descartarse, se
  prefiere esa demora a perder una sombra sana por un corte corto.
- **Correccion 3 -- separar RECONCILE_FAILED de los agregados del reporte**
  (`app/trading/shadow_report.py`): su PnL 0 es forzado (cierre
  administrativo), no un resultado real -- mezclarlo en cerradas/winrate/PF/
  esperanza por estrategia diluia esas tasas. `build_report` separa
  `trades_closed` en confiables y `RECONCILE_FAILED` ANTES de
  `summarize_by_strategy`/`summarize_totals`, y reporta las excluidas aparte
  (conteo simple). El veredicto de `ai_value_verdict` no cambia (ya las
  excluia desde la Etapa 2b).
- Tests con reloj controlado (`now_ms` explicito, no tiempo real): 3 nuevos
  en `tests/unit/test_position_monitor.py`
  (`test_retry_resumes_from_the_original_failure_window_not_from_opened_at`,
  `test_shadow_does_not_give_up_after_max_attempts_if_little_time_elapsed`,
  `test_shadow_closes_as_reconcile_failed_after_max_attempts_and_enough_
  elapsed_time`) y 1 en `tests/unit/test_ai_value.py`
  (`test_report_excludes_reconcile_failed_shadows_from_the_aggregates`).
- Suite completa: 470 passed, 2 skipped; `ruff check .` limpio.
- **Smoke de punta a punta** (nueva copia de `data/minerva_estabilidad.db`,
  con WAL/SHM, assert de ruta distinta, red real publica sin credenciales):
  la sombra TRXUSDT (id 2) reconcilio sin ERROR (`"ABIERTA"`,
  `reconciliation_failed=False`, columnas nuevas en `None` tras exito), y
  `shadow_report.build_report` incluye la linea de exclusion de
  RECONCILE_FAILED.
- Push realizado (pedido explicito: "Detente y realiza el push").

## Incidente de estabilidad (2026-10-11): logging no bloqueante, heartbeat, /health

El bot se congelo ~4h (05:00 locales / 08:00 UTC, en medio de un ciclo) porque
la consola quedo pausada (Tee-Object externo, fuera del repo) y el pipe de
stderr se llamo: `logging.basicConfig` escribe SINCRONO, el `write()` del
`StreamHandler` bloqueo, y con todo en un solo hilo (asyncio) eso bloqueo el
event loop completo.

**Implementado** (`app/core/logging.py`, `app/core/heartbeat.py`,
`app/core/scheduler.py`, `app/api/deps.py`, `app/api/routes/health.py`,
`app/main.py`, `app/config.py`, `scripts/check_health.py`):
- **Logging no bloqueante**: `BoundedQueueHandler` (subclase de
  `QueueHandler`) en el logger raiz -- `enqueue` es siempre `_nowait`, nunca
  bloquea ni lanza. DOS `QueueListener` independientes, cada uno en su propio
  hilo: uno a `RotatingFileHandler` en `settings.log_dir` (destino PRINCIPAL,
  cola grande: 20.000), otro a consola (best-effort, cola chica: 1.000). Si
  consola se traba, solo se traba SU hilo -- nunca el event loop, nunca el
  archivo.
- **Descarte con prioridad, no en silencio**: cola llena descarta DEBUG/INFO
  (contado en `dropped_count`); un WARNING+ primero intenta desalojar un
  registro viejo de baja prioridad para hacerle lugar, y solo se pierde si la
  cola esta llena de puro WARNING+ (caso extremo). Nunca usa
  `handleError`/stderr (podria ser el mismo pipe trabado).
- **uvicorn**: configura sus propios handlers sincronos en `uvicorn`/
  `uvicorn.error`/`uvicorn.access` ANTES del lifespan -- `setup_logging` se
  los quita y los deja propagar al logger raiz, o sus logs de acceso/error
  podrian volver a bloquear exactamente igual que el incidente original
  (verificado en el smoke: esos tres loggers aparecen en `logs/minerva.log`
  con el mismo formato que el resto).
- **Apagado**: `shutdown_logging()` para el listener de archivo con join SIN
  limite (vacia su cola antes de salir) y el de consola con join CON timeout
  (2s) -- nunca cuelga el apagado aunque consola este trabada. Formato sin
  cambios (`"%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"`): los
  `Select-String`/grep existentes siguen funcionando.
- **Heartbeat** (`app/core/heartbeat.py`, nueva clase, mismo patron
  `run_forever`/`stop` que `Scheduler`/`PositionMonitor`): loguea cada
  `settings.heartbeat_seconds` (default 300s) y guarda `last_beat_at` --
  prueba que el EVENT LOOP sigue vivo, nada de logica de negocio.
- **`Scheduler.last_cycle_at`**: distinto del heartbeat -- la ULTIMA
  evaluacion COMPLETA del generador de senales, para notar un cuelgue del
  CICLO de negocio aunque el loop siga vivo (p.ej. la API de Bitunix caida).
- **`GET /health`**: `last_beat_at`, `last_cycle_at`, `status` ("stale" si
  `last_cycle_at` es `None` o pasa de `STALE_AFTER_MINUTES`=90, constante del
  modulo) y `log_dropped_count`. `scripts/check_health.py` (nuevo, opcional):
  consulta `/health` con timeout de 10s, imprime OK/STALE/SIN_RESPUESTA con
  codigo de salida (0/1/2) -- para un cron/Task Scheduler externo.
- `Settings`: `log_dir` (default "logs") y `heartbeat_seconds` (default
  300.0) nuevos; tamanos de cola, de rotacion y el timeout de apagado de
  consola quedan como constantes del modulo (decision explicita).
- Se corrigieron dos tests preexistentes (`test_universe_refresh.py`,
  `test_analyze_risk_script.py`) que llaman `setup_logging()`/`main()` y, con
  el cambio, empezaban a escribir de verdad en el `logs/` del repo -- ahora
  usan `tmp_path` via `monkeypatch`.
- Tests nuevos: `tests/unit/test_logging.py` (11 -- nunca bloquea con cola
  llena, descarta DEBUG/INFO en silencio, prioriza WARNING+ con desalojo,
  quita y silencia los handlers de uvicorn con uno que bloquea de verdad,
  idempotencia, formato, archivo vacia su cola al apagar, consola nunca
  cuelga el apagado aunque su handler este trabado para siempre), 1 en
  `test_scheduler.py` (`last_cycle_at`), 3 en `test_heartbeat.py`, 4 en
  `test_health_route.py` (ok/stale/sin-ciclo-nunca/justo-bajo-el-umbral).
- Suite completa: 489 passed, 2 skipped; `ruff check .` limpio.
- **Smoke de arranque real**: uvicorn real en el puerto 8001 (el bot real
  sigue en 8000, PID verificado antes y despues, sin tocar), `DATABASE_PATH`
  y `LOG_DIR` TEMPORALES (asserts de ruta distinta a la real). Confirmado en
  vivo: `/health` con los 5 campos, `last_beat_at`/`last_cycle_at` avanzando,
  `status="ok"`, `log_dropped_count=0`; `logs/minerva.log` con las lineas de
  `uvicorn.error`/`uvicorn.access` en el mismo formato que el resto (prueba
  directa de que ya no tienen su propio handler sincrono); `check_health.py`
  devolvio OK con exit 0; apagado via CTRL_BREAK_EVENT (equivalente a
  Ctrl+C) completo en 1,5s, sin colgarse -- `logs/minerva.log` muestra
  "Shutting down" / "Waiting for application shutdown." de uvicorn antes de
  que `shutdown_logging()` cerrara los listeners.

**Revision (misma fecha): defecto real en `test_stopping_the_console_
listener_never_hangs_even_if_its_handler_is_stuck`, no en el codigo de
produccion.** El handler de prueba bloqueaba `emit()` con un `Event()` NUEVO
que jamas se ponia. `Handler.handle()` toma `self.lock` (un `RLock`) antes de
llamar a `emit` y lo libera recien al volver -- con ese `Event()` sin liberar
nunca, ese lock queda tomado PARA SIEMPRE. `logging.shutdown()` (registrado
por el modulo `logging` via `atexit`, recorre TODO handler alguna vez creado
sin importar si esta adjunto a algun logger) hace `h.acquire()` sobre ese
mismo lock al salir del proceso: pytest ya reporta la prueba como "passed",
pero el PROCESO ENTERO queda colgado esperando ese `acquire()` -- confirmado
en Python 3.13 (`pytest tests/unit/test_logging.py` no terminaba solo; con
`-k "not console_listener_never_hangs"` si). Corregido: el `Event` ahora es
un atributo de instancia (`handler.release_event`) que el test libera en un
`finally`, dejando que el hilo termine de verdad (y suelte el lock) antes de
que el proceso intente salir. Verificado con `timeout` externo, 3 corridas
seguidas, `pytest tests/unit/test_logging.py` sale solo cada vez
(Python 3.11.15 en este entorno no llegaba a manifestar el colgado, pero el
riesgo era real en cualquier entorno).

**Caveat operativo que queda documentado, no corregido (es del mundo real,
no de la prueba):** si la CONSOLA del bot real queda trabada de verdad al
salir (el escenario del incidente), `logging.shutdown()` puede quedar
esperando para siempre el lock del `StreamHandler` de consola de la misma
manera, y el proceso necesitaria un `kill` (no un apagado limpio) para
terminar. Es aceptable: el bot corre con `Start-Process` sin consola propia
(sin un terminal interactivo detras, no hay pipe de Tee-Object que trabar en
ese modo), asi que este camino especifico no deberia darse en operacion
normal -- `shutdown_logging()` en si (el listener de consola con `join` con
timeout) ya no se cuelga por este motivo, el riesgo residual es solo el
`atexit` de la libreria `logging`, fuera de nuestro control.
- Sin push (pedido explicito).

## Piloto real de signal_review_v1 y revision a v2 (2026-10-11/12)

**Piloto real** (copia `data/piloto_real.db`, 21 grupos, `prompt_version =
"signal_review_v1"`): 20/21 JSON valido (95%), 1.389 tokens de entrada de
media, 1,8s de latencia media, 0,0884 USD de coste real total, pero 90% de
APROBAR -- fuera de la banda 15%-85% (seccion k). Se revisaron las 21
respuestas crudas una por una (no solo el numero agregado) y se encontraron
defectos CONCRETOS del prompt/features, no ruido de muestreo. Corregidos en
`signal_review_v2`:

- **Invalidez por longitud** (`app/llm/schemas.py`): la unica respuesta
  invalida fue un `razonamiento` > 300 caracteres con `decision`/`confianza`
  perfectamente validos. Nueva `parse_decision()` TRUNCA el razonamiento a
  `RAZONAMIENTO_MAX_LENGTH` (300) antes de validar, en vez de invalidar toda
  la decision; `decision_service.py` la usa en vez de `model_validate_json`
  directo. INVALID sigue aplicando SOLO a JSON roto o a `decision`/
  `confianza` fuera de esquema. El prompt ahora pide 250 caracteres (mas
  margen bajo el tope duro).
- **Convenciones explicitas en el prompt** (`app/llm/prompts.py`,
  `SYSTEM_PROMPT`): (a) funding POSITIVO = LONG paga a SHORT, funding
  NEGATIVO = SHORT paga a LONG (el modelo escribio dos veces la convencion
  invertida); (b) en un LONG la liquidacion esta por DEBAJO de la entrada y
  el SL entre ambas, en un SHORT por ENCIMA -- que la liquidacion quede mas
  alla del SL es SIEMPRE asi por diseno, nunca una inconsistencia (el modelo
  rechazo un SHORT por esto).
- **Normalizacion de unidades, no explicacion** (`app/llm/prompts.py`,
  `_normalize_funding_units`): las columnas de funding dentro de
  `indicators_by_strategy` (de `funding_contrarian*`) venian en fraccion
  cruda (0,0001 = 0,01%), distinto de `funding_rate_pct` (features de
  mercado, ya en porcentaje) -- el modelo confundio las dos unidades. Se
  convierten a porcentaje y se renombran con sufijo `_pct` ANTES de armar el
  mensaje, en vez de agregar una explicacion de texto que el modelo puede
  ignorar.
- **Features nuevas, solo con datos ya disponibles al cierre de la vela,
  mismas pruebas de fuga que las demas**
  (`app/llm/prompts.py::_compute_entry_risk_features`,
  `app/llm/market_features.py::compute_trend_features`): precio de entrada
  planeado (`entry_price_planned`, antes NO se enviaba -- el modelo se
  quejo de no poder verificarlo), `sl_distance_pct`, `tp_distance_pct`,
  `risk_reward`; y contexto de tendencia en 1h (`return_24h_pct`,
  `return_72h_pct`, `distance_to_ema50_1h_pct`, reusando las mismas 101
  velas ya pedidas para ATR/correlacion -- el modelo se quejo varias veces
  de "sin datos de tendencia"). Se quito `trailing_distance`: `null` en las
  21 respuestas del piloto, ninguna estrategia activa lo usa hoy.
- **Reduccion de complacencia, sin tasa objetivo**: nueva instruccion en el
  prompt -- antes de decidir, nombrar el principal riesgo concreto de la
  operacion y evaluar si la señal lo compensa; que la señal exista no es un
  argumento a favor, y un chequeo que siempre se cumple (SL bajo el tope,
  sin posicion abierta) tampoco cuenta. NINGUN porcentaje objetivo agregado
  (sigue prohibido por diseno).
- `PROMPT_VERSION = "signal_review_v2"` (nuevo hash `PROMPT_SHA256`, no
  mezcla con la medicion de v1 -- que de todas formas nunca empezo).
- Tests nuevos: 7 en `test_llm_schemas.py` (truncar vs. seguir rechazando
  JSON roto/`decision`/`confianza` invalidos), 1 en
  `test_llm_decision_service.py` (punta a punta: razonamiento largo ->
  STATUS_OK, no INVALID), 4 en `test_llm_prompts.py` (convenciones en el
  prompt, version, sin tasa objetivo con porcentaje) + 4 mas (entry/riesgo,
  trailing_distance ausente, normalizacion de funding), 7 en
  `test_llm_market_features.py` (retornos/EMA puros) + extension de las 2
  pruebas de fuga existentes para cubrir `compute_trend_features`.
- Suite completa: 515 passed, 2 skipped; `ruff check .` limpio.
- **Smoke en seco** (base temporal nueva, nunca `data/piloto_real.db` ni la
  de trabajo, sin tocar el bot del puerto 8000): 2 sombras sinteticas
  (BTCUSDT SHORT con una estrategia de funding, ETHUSDT LONG), velas 1h
  cacheadas para que ATR/correlacion/tendencia no salgan null. Confirmado de
  punta a punta: indicadores de funding normalizados (`funding_rate_pct`
  presente, `funding_rate` crudo ausente), `trailing_distance` ausente,
  `entry_price_planned`/`sl_distance_pct`/`tp_distance_pct`/`risk_reward`
  correctos, features de tendencia con valor. `python scripts/llm_pilot.py
  --db <temporal> --max-calls 2` (dry-run) corrio sin red ni coste,
  `prompt_version: signal_review_v2`, 2/2 JSON valido, dentro de banda.
- **`estimate_input_tokens`, nuevo vs. el real esperado**: la formula
  (caracteres/2) NO cambia -- solo crecio el contenido. `SYSTEM_PROMPT` pasa
  de 2.155 a 3.318 caracteres (+54%, las convenciones nuevas y la
  instruccion contra la complacencia); el mensaje de ejemplo del smoke
  (BTCUSDT SHORT, con indicadores de funding) da 1.228 caracteres. Estimado
  nuevo: **2.273 tokens** (vs. ~1.570 que daba la formula para v1, y vs. los
  1.393 reales medidos en el UNICO smoke real que existe, que fue sobre
  v1). Es un salto real (~+63% sobre esos 1.393), no un artefacto de la
  formula: el prompt de sistema creció y el mensaje ganó 7 campos nuevos
  (perdiendo 1, `trailing_distance`, que no pesaba nada util). Impacto en
  presupuesto: `cost_max` por llamada (peor caso, 300 tokens de salida) sube
  de ~0,0061 a ~0,0075 USD, un ~+23%. **No hay una medicion real de v2**
  todavia -- la unica forma de confirmar si caracteres/2 sigue siendo
  conservador es correr el piloto de v2 con `--real --confirm-real`
  (pendiente, anotado en docs/FASE3_6_LLM.md seccion k).
- Sin push (no se pidio).

## Piloto real de signal_review_v2 y revision a v3 (2026-10-12)

**Piloto real** (copia `data/piloto_v2.db`, 31 grupos, `prompt_version =
"signal_review_v2"`): APROBAR 51,7% (dentro de banda 15%-85%), 0 campos
null, 1.985 tokens de entrada de media (estimado ~2.273 -- conservador,
sigue sobrestimando), 2,05s de latencia, 0,168 USD de coste real. Los tres
defectos de v1 (signo del funding, geometria liquidacion/SL, unidades de
funding) desaparecieron. Quedaron tres cosas corregidas en esta revision,
documentadas en detalle en docs/FASE3_6_LLM.md seccion k:

1. **2/31 INVALID (6,5%), mismo error de sintaxis:** el modelo omite la
   comilla de cierre de `razonamiento` antes de la llave final
   (`json.JSONDecodeError: "Unterminated string..."`), ambas APROBAR.
   - Investigadas salidas estructuradas de Anthropic (`output_config.format`
     con `json_schema`) ANTES de escribir un parser: confirmado GA para
     `claude-sonnet-5-5` (platform.claude.com/docs/en/build-with-claude/
     structured-outputs), pero NINGUNA pagina oficial confirma ni descarta
     la combinacion exacta con `thinking={"type":"between_tools"}` +
     `output_config.effort="low"` que usa este proyecto -- la pagina de
     thinking solo restringe los campos PROPIOS de `thinking`, no
     `output_config`. Sin una llamada real no se puede confirmar que no de
     400, y esta revision es en seco (sin llamadas autorizadas) -- **no se
     adopto, queda documentado como candidato futuro** que necesitaria 1-2
     llamadas reales de verificacion antes de confiarlo.
   - Implementado en su lugar: `app/llm/schemas.py::
     _try_repair_unterminated_razonamiento`, un parser MUY acotado que solo
     actua cuando el JSON estricto falla Y la forma es EXACTAMENTE
     `decision`/`confianza` validos seguidos de `razonamiento` sin comilla de
     cierre. Cualquier otro JSON roto sigue INVALID. `parse_decision` ahora
     devuelve `ParsedDecision(decision, repaired: bool)`; `llm_logs.
     json_repaired` (columna nueva) lo deja auditable, y
     `scripts/llm_pilot.py` lo cuenta y reporta aparte ("JSON reparado").
     Probado con las DOS respuestas reales de `data/piloto_v2.db` (leidas en
     modo solo lectura, ids 2 y 28).
2. **Bug de unidades, misma clase que el funding de v1:**
   `volatility_atr14_pct` (`app/llm/market_features.py`) era la fraccion
   cruda (0,0028 = 0,28%) pese al sufijo `_pct`; el modelo la leyo literal
   como porcentaje y rechazo señales por "volatilidad muy baja" cuando en
   realidad era ~100 veces mayor. `compute_atr14_pct` ahora multiplica por
   100. `returns_std_30` (sin sufijo, mismo problema) se renombra a
   `returns_std_30_pct` (`compute_returns_std_pct`) y tambien se multiplica
   por 100. `llm_logs.atr_pct` nunca estuvo poblado (ningun llamador le pasa
   `atr_pct` a `decide_group`), asi que no habia ningun valor guardado que
   corregir ahi -- documentado como hallazgo, no como bug adicional. Tests:
   una serie conocida reconstruye ATR/std a mano en fraccion y confirma
   `funcion() == fraccion * 100`; una prueba general verifica que NINGUNA
   clave `_pct` del mensaje completo sea sospechosamente chica en un
   escenario deliberadamente volatil.
3. **La congelacion ahora cubre tambien las features, no solo el texto:**
   `PROMPT_SHA256` antes solo hasheaba `SYSTEM_PROMPT` -- el bug de unidades
   del punto 2 no habria cambiado ese hash, y `measurement_keys` podria
   haber mezclado dos mensajes distintos bajo el mismo hash. Nueva
   `FEATURES_VERSION = "features_v1"` (constante, independiente de
   `PROMPT_VERSION` a proposito), guardada en `llm_logs.features_version`
   (columna nueva) y plegada DENTRO de `PROMPT_SHA256` junto con
   `SYSTEM_PROMPT` -- `measurement_keys` no cambia su logica, pero el hash
   que usa ahora es sensible a los dos ejes. `PROMPT_VERSION` sube a
   `signal_review_v3` (el texto no cambio en esta revision, pero la
   etiqueta legible avanza con la tercera generacion del experimento).
- Tests nuevos: 6 en `test_llm_schemas.py` (reparo con las 2 cadenas reales,
  no-reparo con JSON distinto roto, `repaired=False` en el camino normal),
  1 de integracion en `test_llm_decision_service.py` (reparo de punta a
  punta: `FakeLlmClient` con el texto real -> `STATUS_OK`/`APROBADA` ->
  `llm_logs.json_repaired=1`), 6 en `test_llm_market_features.py`
  (ATR/returns_std en fraccion a mano vs. la funcion, nombres nuevos), 1 en
  `test_llm_prompts.py` (prueba general de unidades `_pct`) + 4 mas
  (PROMPT_VERSION v3, FEATURES_VERSION, PROMPT_SHA256 cubre los dos ejes).
- Suite completa: 525 passed, 2 skipped; `ruff check .` limpio.
- **Smoke en seco** (base temporal nueva, nunca `data/piloto_v2.db` ni la de
  trabajo, sin tocar el bot del puerto 8000): features de un trade sintetico
  confirmaron `volatility_atr14_pct`/`returns_std_30_pct` en escala de
  porcentaje (no fraccion) y la clave vieja `returns_std_30` ausente;
  `LlmDecisionService.decide_group` con un `FakeLlmClient` (una respuesta
  normal + la cadena real rota) escribio `json_repaired=0`/`1` y
  `features_version="features_v1"` correctamente en `llm_logs`; y
  `python scripts/llm_pilot.py --db <temporal> --max-calls 1` (dry-run) corrio
  sin red ni coste con `prompt_version: signal_review_v3` y el reporte de
  nulls mostrando los nombres de campo nuevos.
- Sin push (pedido explicito).

## Piloto real de signal_review_v3 y revision a v4 (2026-10-12, ULTIMA iteracion)

**Piloto real** (32 grupos, `prompt_version = "signal_review_v3"`): 100% JSON
valido, 0 campos null, 1.986 tokens de entrada de media, 0,173 USD de coste
real, 46,9% APROBAR. El bug de unidades del ATR (revision de v2) desaparecio.
Leidas las 32 respuestas una por una: queda UN defecto, el signo del funding
en SHORT con funding negativo (2/4 casos con esa combinacion: TRXUSDT SHORT
2026-10-11 04:00 y 2026-10-12 00:00), **pese a la convencion explicita de
texto** ya presente desde v2 -- el modelo la sigue infiriendo mal con cierta
frecuencia aunque se le explique correctamente.

**Correccion de `signal_review_v4` (ULTIMA iteracion del prompt antes de
congelar, pedido explicito -- no se agrega nada mas despues de esto):**
resuelto de forma DETERMINISTA en vez de con mas texto (que ya habia
demostrado no alcanzar). Nueva feature `funding_effect_on_position`
(`"RECIBE"`/`"PAGA"`/`"NEUTRO"`/`null`,
`app/llm/prompts.py::_compute_funding_effect`): calcula directamente, a
partir de `funding_rate_pct` y `side`, si ESTA posicion cobra o paga funding.
`SYSTEM_PROMPT` le dice al modelo que use el campo tal cual, sin
recalcularlo. `funding_rate_pct` no se toca (sigue como esta, en porcentaje).
`FEATURES_VERSION` sube a `"features_v2"`, `PROMPT_VERSION` a
`signal_review_v4`.
- Tests nuevos: 6 casos puros de `_compute_funding_effect` (las 4
  combinaciones LONG/SHORT x funding positivo/negativo -- incluido
  exactamente el caso real TRXUSDT SHORT + funding negativo = `PAGA` que el
  modelo leia mal --, mas `NEUTRO` en 0 y `null` sin dato), 1 de contenido
  del prompt, y 2 de integracion con `build_features_from_shadow_trade`
  (incluida una prueba de fuga: un evento de funding futuro de signo
  contrario no cambia el resultado).
- Suite completa: 534 passed, 2 skipped; `ruff check .` limpio.
- **Smoke en seco** (base temporal nueva, nunca la de trabajo, sin tocar el
  bot del puerto 8000): sombra TRXUSDT SHORT con funding negativo sembrado
  a mano -- `funding_rate_pct=-0.025`, `funding_effect_on_position="PAGA"`
  (el caso real exacto, ahora correcto por calculo, no por inferencia del
  modelo); `SYSTEM_PROMPT` y el mensaje de usuario contienen el campo nuevo.
- **Pendiente:** correr el piloto de `signal_review_v4` con llamadas reales
  antes de congelar y empezar la medicion -- no se hizo en esta revision.
  Con esta iteracion se considera **cerrado el ciclo de ajustes de texto/
  features del prompt**: la proxima revision esperada es el resultado de ESE
  piloto real, no un cambio nuevo (pedido explicito del usuario).
- Sin push (pedido explicito).

## Piloto real de signal_review_v4, CONGELAMIENTO, y TAREA B/C (2026-10-12)

**Piloto real** (45 grupos, `prompt_version = "signal_review_v4"`): 45/45
JSON valido (1 reparado por el parser tolerante de la revision de v2), 0
campos null, 60% APROBAR (dentro de banda 15-85%), y el defecto de v3 (signo
del funding en SHORT con funding negativo) **desaparecio por completo**:
signo correcto en los 45/45 casos gracias a `funding_effect_on_position`
(campo determinista, no inferido por el modelo).

**PROMPT CONGELADO** (pedido explicito del usuario, docs/FASE3_6_LLM.md
seccion k): a partir de aqui, ningun cambio de `SYSTEM_PROMPT`,
`FEATURES_VERSION` ni `build_user_message`/features sin su aprobacion
explicita -- cualquiera de los dos invalida la medicion.

```
PROMPT_VERSION   = "signal_review_v4"
FEATURES_VERSION = "features_v2"
PROMPT_SHA256    = "0fa0dd75a3fecdae36f3b77c8b7ec5d97d58a2238349283c61bfa7632188c777"
```

### TAREA B: etiquetado en vivo de sombras durante la medicion

Modulo nuevo `app/trading/shadow_live_labeler.py` (`ShadowLiveLabeler`),
plan previamente aprobado por el usuario, ahora implementado con sus 5
ajustes:

1. `FROZEN_PROMPT_SHA256` (mismo valor de arriba). Si `PROMPT_SHA256` en
   vivo no coincide, el etiquetado NO corre (grupos quedan `SIN_LLM`),
   WARNING en el log en CADA ciclo (no solo el primero, via `Scheduler.
   run_cycle` -> `note_cycle_start()`), y `GET /health` expone
   `llm_labeling.frozen_hash_ok`. Nunca se descartan filas ya escritas.
2. Presupuesto agotado: `decide_group` ahora logea un WARNING de aplicacion
   en el momento exacto (antes solo quedaba en `llm_logs`, sin nadie
   mirando la consola de un etiquetado en vivo). `llm_logs_repo.
   get_status_counts_since`/`get_first_occurrence_since` (funciones nuevas)
   distinguen `BUDGET_EXCEEDED` de `TIMEOUT`/`ERROR_HTTP`/`INVALID`,
   expuesto en `GET /health` (bloque `llm_labeling`) y preparado para la
   seccion (m) del informe de sesgo por exclusion.
3. Tests (14 casos nuevos en `tests/unit/test_shadow_live_labeler.py` + 2 en
   `test_scheduler.py`): sin fuga de datos futuros (reusa `build_features_
   from_shadow_trade`, ya probado); retraso > 1800s deja `SIN_LLM` sin fila
   nueva (y justo debajo del limite si etiqueta); fallo real (`FakeLlmClient`
   con timeout) deja `SIN_LLM`, nunca `RECHAZADA`; nunca crea ni toca un
   trade real; flag apagado (`live_labeler=None`, el default hoy) no cambia
   el comportamiento del ciclo. **Smoke punta a punta real** sobre una DB
   temporal nueva, puerto 8001, confirmando ANTES y DESPUES que el bot real
   del puerto 8000 seguia con `status: ok` sin ninguna interrupcion: cliente
   del LLM parcheado por uno sin red (cero costo posible), `scheduler.
   run_cycle()` real abrio una sombra via un candidato construido a mano y
   la etiqueto `APROBADA` con una fila `llm_logs` real, `GET /health` reflejo
   el bloque antes/despues, `trades_repo.get_open_positions` siguio vacio, y
   el apagado limpio no dejo tareas pendientes.
4. Tareas en un `set` con `done_callback` que logea cualquier excepcion
   inesperada (`logger.error(..., exc_info=exc)`, nunca silenciosa); el
   cierre limpio del bot (`live_labeler.shutdown()`, llamado desde el
   `finally` del lifespan de `app/main.py`) cancela toda tarea pendiente y
   espera a que terminen antes de cerrar la base.
5. `Settings.llm_live_labeling_enabled = False` por defecto
   (`LLM_LIVE_LABELING_ENABLED` en `.env.example`). **El flag sigue apagado
   en el bot real -- no se activo ni se reinicio el bot real; la activacion
   es decision del usuario.**

Suite completa: 551 passed, 2 skipped; `ruff check .` limpio.

### TAREA C: borde del reconcile al arrancar que no limpiaba el flag

`PositionMonitor.reconcile_on_startup` limpiaba `reconciliation_failed`
cuando un reintento PERIODICO tenia exito, pero no cuando el PRIMER intento
al arrancar el proceso (para un trade ya marcado de una racha anterior) ya
tenia exito de entrada -- quedaba marcado (sombra congelada / `fill_source
TICK_UNRECONCILED` en reales) hasta el siguiente reintento periodico, hasta
300s despues, sin necesidad. Corregido: se limpia en el momento (misma
`_clear_reconciliation_failed`, extendida para tambien actualizar el objeto
en memoria). Test nuevo: `test_startup_reconciliation_success_clears_a_flag_
from_a_previous_run`. Commit `79ceb53`.

- Sin push (pedido explicito). Commits pequenos para TAREA B/C y esta
  documentacion.

## TAREA D: scripts/measurement_status.py (avance de la medicion en vivo)

Script nuevo, solo lectura, que muestra el avance de la medicion bajo el
hash de prompt CONGELADO (`FROZEN_PROMPT_SHA256`) sin mirar PnL ni
resultados de las operaciones (nunca lee `pnl_net_usdt`, `pnl_gross_usdt`,
`exit_price` ni `close_reason` -- solo conteos de `llm_logs` y las marcas de
tiempo/simbolo/direccion/etiqueta de `shadow_trades` necesarias para agrupar
en conglomerados, mismo criterio de `app.trading.ai_value.assign_clusters`
pero reimplementado sobre filas crudas para no tener que poblar un
`ShadowTrade` completo con sus campos de PnL).

Reporta: (1) grupos con fila OK por lado y total; (2) tasa de APROBAR y si
cae en la banda 15-85%; (3) filas no OK por causa (TIMEOUT/ERROR_HTTP/
INVALID/BUDGET_EXCEEDED) y su % sobre el total de filas MEDICION; (4)
sombras posteriores a la primera fila MEDICION sin ninguna fila en
`llm_logs` (por demora > 1800s o hash no coincidente), separado para la
seccion (m); (5) filas con `json_repaired=1`; (6) coste acumulado y coste
medio por fila; (7) N efectivo (conglomerados) por lado frente a
`MIN_EFFECTIVE_N=300`, cuantos faltan, y una estimacion de dias segun el
ritmo de los ultimos 7 dias (o todos los disponibles si hay menos) --
anclada al dato MAS RECIENTE de la propia copia, no al reloj de pared (una
copia de hace horas/dias no debe salir con ritmo artificialmente bajo solo
por ser vieja).

Abre la base en modo solo lectura (`sqlite3`, URI `mode=ro`, igual que
`scripts/compare_fase2_runs.py`) -- nunca escribe, y por eso no pasa por las
migraciones de `app.persistence.database.Database` (que si escriben).
`--db` obligatorio, con el mismo assert de ruta que `llm_pilot.py`/
`llm_smoke.py` (distinta de `settings.database_path`).

21 tests nuevos con datos sinteticos (`tests/unit/test_measurement_status_
script.py`): clustering por (simbolo, direccion) con solapes, un
conglomerado con ambas etiquetas cuenta para los dos lados, simbolo o
direccion distintos nunca se fusionan, filas bajo un hash distinto al
congelado se ignoran, `N efectivo` solo cuenta sombras CERRADAS (una abierta
cuenta para el punto 1 pero no para el 7), el ritmo se ancla al dato mas
reciente de la copia (no al reloj real), y un reporte de extremo a extremo
contra una base de archivo real (sintetica, poblada via los repos de
produccion) reabierta en solo lectura. Suite completa: 572 passed, 2
skipped; `ruff check .` limpio.

**Verificacion contra datos reales (regla de CLAUDE.md):** respaldo online
consistente (`sqlite3.Connection.backup`, sobre una conexion fuente en modo
solo lectura) de `data/minerva_estabilidad.db` a `data/backups/
measurement_status_verif.db`, y el script corrio limpio contra esa copia:
1 fila MEDICION bajo el hash congelado (APROBADA, sombra todavia abierta por
eso N efectivo=0 en el punto 7), 0 filas no-OK, 0 omitidas, coste acumulado
0,005916 USD. **Nota para el usuario:** esa fila real confirma que
`LLM_LIVE_LABELING_ENABLED` ya esta activo en el bot real (yo no lo active;
la activacion era decision tuya, como pediste en TAREA B) y que el
etiquetado en vivo esta funcionando en produccion.

- Sin push (pedido explicito, pendiente confirmar). Commit pequeno para
  TAREA D.
