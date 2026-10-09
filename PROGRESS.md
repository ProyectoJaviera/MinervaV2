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
