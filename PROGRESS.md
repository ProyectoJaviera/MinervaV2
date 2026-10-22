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

## Pre-Fase 5: plan aprobado (2026-10-14) y Subfase A (alerta Telegram)

Plan de 3 items (alerta Telegram, auth + endpoints de solo lectura, dashboard
React) propuesto en Plan Mode y aprobado por el usuario -- plan completo en
`C:\Users\Renzo\.claude\plans\radiant-twirling-waterfall.md`. Resumen de lo
acordado: el bot real (puerto 8000, `data/minerva_estabilidad.db`,
`LLM_LIVE_LABELING_ENABLED=true`) no se toca salvo UN restart al final (tras
auth + endpoints nuevos bajo `/api/` + el mount de `StaticFiles`), cronometrado
justo despues de un cierre de vela de 4h. El dashboard nuevo NO muestra PnL ni
resultados de sombras etiquetadas hasta `MIN_EFFECTIVE_N=300` por lado (choke
point unico: `shadow_trade_to_public_dict`, a construir en la Subfase D).
Subfases: A (alerta Telegram, sin restart) -> B (auth backend) -> C (medicion
portada a async) -> D (decisiones/sombras recientes + el choke point) -> E
(mount de `StaticFiles`) -> F (dashboard React) -> G (smoke final + el unico
restart real). Diferido para despues del veredicto: posiciones/equity,
controles/kill switch, noticias, memoria, bot de comandos de Telegram.

### Subfase A: `scripts/telegram_alert.py`

Script nuevo, reutiliza `scripts/check_health.py::main` sin duplicar su logica
de HTTP/parseo (import calificado `from scripts.check_health import ...`, no
uno suelto -- funciona igual corrido directo o importado desde un test).
Avisa por Telegram en cualquier CAMBIO de estado (`ok->stale`,
`ok->sin_respuesta`, `stale<->sin_respuesta` -- cambia la causa aunque siga
caido --, y la recuperacion a `ok`), y mientras sigue igual y caido, un
recordatorio cada `--reminder-minutes` (default 60). Arranque en frio (sin
archivo de estado) con el bot ya caido avisa de inmediato; con el bot ya bien
no avisa nada (se trata como si el ultimo estado visto fuera OK). El estado
(`data/telegram_alert_state.json`, `data/` ya esta completo en `.gitignore`)
solo se actualiza tras un envio exitoso a Telegram -- si falla, el proximo
poll (10 min despues, via el Programador de tareas de Windows) reintenta el
aviso pendiente en vez de perderlo. Token/chat_id vienen de
`settings.telegram_bot_token`/`telegram_chat_id` (ya existian vacios); nunca
se imprimen ni se loguean, ni siquiera en un error.

Verificado en vivo (no inventado) antes de escribir el script: la API de
Telegram (`sendMessage` solo exige `chat_id`+`text`, sin tarjeta para uso
normal, ~1 msj/seg de limite practico) y la sintaxis real de
`schtasks /create` en esta misma maquina.

12 tests nuevos (`tests/unit/test_telegram_alert_script.py`, `check_health_fn`/
`send_fn` inyectados, nunca una llamada real): todas las transiciones de
estado, el reintento tras un envio fallido, y que un fallo de red nunca
loguea el token/chat_id. Suite completa: 584 passed, 2 skipped; `ruff check .`
limpio. Probado manualmente: sin `TELEGRAM_BOT_TOKEN`/`CHAT_ID` en `.env` el
script se detiene con un mensaje claro (exit code 1) antes de intentar nada;
`GET /health` contra el bot real (puerto 8000) confirmado `status: ok` sin
cambios antes y despues de esta prueba.

**Pendiente del usuario** (fuera de Claude, por diseno): crear el bot con
`@BotFather`, obtener el `chat_id` con `getUpdates`, completar `.env`, y
programar la tarea de Windows (comando exacto en el docstring del script).

### Revision de Subfase A (2026-10-14): 2 correcciones del usuario

1. **Bug real reproducido**: el Programador de tareas de Windows arranca en
   un directorio ajeno (tipicamente System32, no la raiz del repo) -- antes
   de esto, `sys.path.insert(0, ".")` resolvia a ESE directorio y rompia el
   import de `app.config`/`scripts.check_health` con `ModuleNotFoundError`
   (y `.env`/`data/telegram_alert_state.json`, ambos relativos, tambien
   habrian quedado mal ubicados). Corregido con
   `os.chdir(Path(__file__).resolve().parent.parent)` al inicio del script,
   ANTES de `sys.path.insert`/cualquier import de `app`/`scripts`. Test
   nuevo que corre el script de verdad por `subprocess` con el cwd puesto a
   un directorio temporal ajeno al repo (`--help`, sin necesitar
   credenciales) -- confirma que ya NO sale `ModuleNotFoundError`. Docstring
   del `schtasks` actualizado: ya no depende de correr desde la raiz del
   repo para funcionar (aunque `%cd%` sigue siendo la forma mas corta de
   armar la ruta absoluta que `/TR` exige).
2. **Robustez del archivo de estado**: `_load_state` ahora trata un JSON
   corrupto o bytes illegibles como "sin estado" (en vez de tumbar el
   script) -- perder el historial de cuando empezo una caida es mucho menos
   grave que dejar de avisar del todo. `_save_state` ahora escribe de forma
   atomica (`archivo .tmp` + `os.replace`): si el proceso se interrumpe a
   mitad de camino, el peor caso es un `.tmp` huerfano, nunca un
   `state.json` truncado. 5 tests nuevos (JSON corrupto, bytes no-UTF8, que
   un estado corrupto deja avisar igual que un arranque en frio, y que
   `_save_state` de verdad pasa por un `.tmp` + `os.replace` antes de dejar
   el archivo final).

Suite completa: 589 passed, 2 skipped; `ruff check .` limpio. Verificado a
mano: el script corre limpio desde `/tmp` (directorio no relacionado), y el
bot real en el puerto 8000 sigue en `status: ok` sin cambios antes y
despues.

- Sin push (pedido explicito). Subfase B (auth backend) sigue, segun el plan
  aprobado, contra DB temporal en el puerto 8001, sin reiniciar ni modificar
  el bot real ni el prompt congelado.

## Subfase B: auth backend (2026-10-14)

**Dependencias nuevas**: `bcrypt>=4.0`, `pyjwt>=2.8` en `pyproject.toml`
(instaladas con `uv pip install --system-certs` -- el entorno necesita ese
flag para TLS, mismo motivo por el que el proyecto ya depende de
`truststore` para sus propias llamadas HTTP). `pip` no esta disponible en
este venv (gestionado por `uv`); instalar con `uv pip install --python
.venv/Scripts/python.exe --system-certs ...`.

**`app/api/security.py`** (nuevo): `hash_password`/`verify_password`
(`bcrypt` directo, no `passlib`), `verify_username` (`hmac.compare_digest`),
`create_session_token`/`decode_session_token` (JWT HS256 via `PyJWT`, claims
`sub`/`iat`/`exp`=+24h, sin refresh token), y `require_auth` (dependencia de
FastAPI que lee la cookie `minerva_session`, pensada para aplicarse a nivel
de ROUTER completo, no endpoint por endpoint). `app/api/deps.py` gana
`get_settings(request) -> Settings` (mismo patron que
`get_db`/`get_scheduler`); `app/main.py` fija `app.state.settings` en el
lifespan.

**`app/api/routes/auth.py`** (nuevo): `POST /api/auth/login` (cookie
`httpOnly`, `SameSite=Strict`, sin `Secure` -- loopback HTTP plano),
`POST /api/auth/logout`, `GET /api/auth/me` (protegido). `GET /health`
sigue sin auth a proposito (lo usa `scripts/telegram_alert.py`, sin login).

**`scripts/hash_password.py`** (nuevo): `getpass` (nunca como argumento de
CLI), imprime solo `AUTH_PASSWORD_HASH=<hash>` para pegar en `.env`.

**`scripts/run_dev_server.py`** (nuevo, seccion 6 del plan): copia una base
real a un archivo temporal y arranca `app.main:app` en el puerto 8001,
forzando `LLM_LIVE_LABELING_ENABLED=false`/`ANTHROPIC_API_KEY=""` SIN
IMPORTAR lo que diga el `.env` real (que hoy tiene el flag en `true` --
evita gasto real contra la copia). **Ajuste respecto del plan**: `--source-db`
quedo OBLIGATORIO en vez de con default a `settings.database_path` -- leer
esa settings ANTES de fijar las variables de entorno crearia el singleton
`Settings()` con el entorno viejo, y el `import app.main` de mas abajo
reusaria ese mismo objeto ya cacheado en `sys.modules`, ignorando los
overrides. Pedir la ruta explicita evita ese problema de orden de
importacion sin ningun truco fragil (borrar modulos de `sys.modules` a
mano, etc.).

**20 tests nuevos** (`tests/unit/test_auth.py`, primer uso de
`fastapi.testclient.TestClient` en el repo, sobre una app FastAPI minima --
solo el router de auth + `app.state.settings`, nunca la app real ni ningun
puerto): hash/verify de contrasena y usuario, ida y vuelta del JWT, rechazo
por secreto distinto/expirado/basura, login correcto/incorrecto (usuario,
contrasena, ninguno configurado), atributos de la cookie (`HttpOnly`,
`SameSite=Strict`, sin `Secure`), `me` sin cookie/con cookie valida/con
cookie alterada/con secreto equivocado, logout limpiando la sesion. Suite
completa: 609 passed, 2 skipped; `ruff check .` limpio.

**Smoke de nivel 2** (`scripts/run_dev_server.py` contra un respaldo online
fresco de `data/minerva_estabilidad.db`, puerto 8001, proceso en segundo
plano detenido limpiamente al terminar): `/health` respondio `status: ok`
con `llm_labeling.enabled: false` (confirma el candado de seguridad --
`.env` real lo tiene en `true`); `POST /api/auth/login` con cualquier
credencial devolvio 401 (`AUTH_USERNAME`/`AUTH_PASSWORD_HASH` siguen vacios
en el `.env` real: el auth nunca abre sesion sin configurar, por diseno);
`GET /api/auth/me` sin cookie devolvio 401. Confirmado antes y despues que
el bot real del puerto 8000 seguia en `status: ok`, con `llm_labeling.
enabled: true` intacto y `cost_today_usd` sin cambios (ninguna llamada real
al LLM se disparo).

**Pendiente del usuario** (fuera de Claude, por diseno): generar
`JWT_SECRET` (`python -c "import secrets; print(secrets.token_hex(32))"`),
correr `scripts/hash_password.py` para `AUTH_PASSWORD_HASH`, y completar
`AUTH_USERNAME`/`AUTH_PASSWORD_HASH`/`JWT_SECRET` en `.env` -- sin esto el
login seguira fallando siempre, por diseno (no es un bug).

- Sin push (pedido explicito). Commit pequeno para esta subfase; se detiene
  aqui a esperar aprobacion antes de la Subfase C (medicion portada a
  async), como pidio el usuario.

### Revision de Subfase B (2026-10-14): 4 correcciones del usuario

Probadas contra la Subfase B real: ninguna de las 3 primeras daba acceso,
pero las 3 fallaban con 500 en vez de un 401/503 limpio.

1. **`JWT_SECRET` vacio o corto fallaba ABIERTO en la practica**: un
   atacante que lo adivinara (vacio es adivinable al primer intento) podia
   forjar un token valido para cualquier usuario, porque `jwt.decode`/
   `jwt.encode` no rechazan una clave HMAC corta por si solos (solo
   advierten). `jwt_secret_is_safe` (`len() >= 32`, RFC 7518 Section 3.2)
   corta ese camino antes de firmar o verificar nada: `login` responde 503,
   `require_auth` responde 401, y `warn_if_jwt_secret_is_unsafe` deja un
   WARNING al arrancar (extraido como funcion propia, testeable con
   `caplog`, en vez de un `if` suelto en `app/main.py`).
2. **`require_auth` solo atrapaba `jwt.InvalidTokenError`**, pero
   `jwt.InvalidKeyError` (clave HMAC malformada o vacia) es subclase DIRECTA
   de `jwt.PyJWTError`, no de `InvalidTokenError` -- confirmado verificando
   la jerarquia real de excepciones de PyJWT instalado, y reproducido con un
   token bien formado decodificado contra un secreto vacio
   (`HMAC key must not be empty.`). Corregido a `except jwt.PyJWTError`.
   Encontre el mismo hueco en `login` (un secreto largo pero con FORMA de
   JWK pasa el chequeo de largo pero sigue haciendo que `jwt.encode` lance
   `InvalidKeyError`) y lo corregi igual ahi, aunque el usuario solo lo pidio
   para `require_auth` -- mismo principio, mismo riesgo de 500.
3. **`verify_username` comparaba `str` con `hmac.compare_digest`**, que
   exige ASCII puro y lanza `TypeError` con cualquier otro caracter
   (confirmado: `usuário` lo dispara) -- corregido comparando bytes UTF-8.
   **`verify_password`** ahora atrapa el `ValueError` que `bcrypt` lanza con
   contrasenas > 72 bytes (confirmado con el bcrypt instalado) y devuelve
   `False` en vez de dejarlo escapar; `LoginRequest.username`/`.password`
   ganan `max_length=128` (defensa en otra capa: no sustituye el catch
   anterior, porque 128 caracteres Unicode pueden superar 72 bytes igual).
4. **`scripts/run_dev_server.py`**: `shutil.copy2` reemplazado por
   `sqlite3.Connection.backup` (conexion fuente en modo solo lectura) para
   copiar la base viva de forma consistente -- `shutil.copy2` podria copiar
   a mitad de una transaccion WAL y dejar la copia corrupta.

16 tests nuevos en `tests/unit/test_auth.py` (37 en total), incluido un
secreto con forma de JWK que pasa el chequeo de largo pero sigue exigiendo
el catch amplio (prueba real del punto 2, no solo del chequeo de largo del
punto 1). Suite completa: 626 passed, 2 skipped; `ruff check .` limpio.
Confirmado que el bot real del puerto 8000 seguia intacto. Commit `785547b`.

- Sin push (pedido explicito). Sigue la Subfase C (endpoints de solo
  lectura bajo `/api/`: medicion, decisiones recientes, sombras recientes --
  protegidos con `require_auth`, sin PnL de sombras hasta `MIN_EFFECTIVE_N`
  por lado), segun el plan aprobado, mismas reglas: DB temporal, puerto
  8001, sin reiniciar ni modificar el bot real ni el prompt congelado.

## Subfase C: endpoints de solo lectura bajo /api/ (2026-10-14)

**`app/trading/measurement_status.py`** (nuevo): la logica de
`scripts/measurement_status.py` (`ClosedLabeledTrade`, `cluster_effective_
counts`, `MeasurementStatus`, `compute_status`) se mudo aqui, hermana de
`ai_value.py`/`shadow_live_labeler.py`. Se agrega `compute_status_async(db:
Database, ...)` -- MISMA logica que `compute_status`, solo cambia
`conn.execute(...).fetchall()` por `await db.fetch_all(...)` (`aiosqlite.
Row` soporta el mismo acceso `row["col"]`) -- y `is_measurement_gate_open
(status) -> bool` (`n_eff_approved >= MIN_EFFECTIVE_N and n_eff_rejected >=
MIN_EFFECTIVE_N`), la UNICA fuente de verdad sobre si ya se puede revelar un
resultado. `scripts/measurement_status.py` queda como wrapper fino (solo
`_connect_ro`/`format_report`/`main`/`argparse`, importando el resto desde
el modulo nuevo) -- los 21 tests existentes siguen pasando sin cambiar sus
aserciones (refactor puro), mas 2 tests nuevos que comparan `compute_status`
(sync) vs `compute_status_async` (async) sobre los MISMOS datos sinteticos
sembrados en una base `:memory:` y en un archivo temporal reabierto en modo
solo lectura, y confirman que el `MeasurementStatus` resultante es idéntico
campo por campo.

**Funciones de repo nuevas**, mismo patron que `trades_repo.get_trades(db,
limit=100)`: `llm_logs_repo.get_recent(db, limit=50, fase=None)` y
`shadow_repo.get_recent(db, limit=50)` (aditiva, no toca `get_open`/
`get_closed`, que `ai_value_verdict` sigue usando sin limite ni orden).

**`app/api/serializers.py`** (nuevo) -- **el unico punto de paso** para
revelar un resultado de sombra etiquetada: `shadow_trade_to_public_dict
(trade, *, reveal_outcome)`. Lista BLANCA explicita (nunca `model_dump()`
ni un atajo "todo menos lo prohibido" -- un campo nuevo en `ShadowTrade` no
aparece hasta que alguien lo agregue a mano aqui). Con `reveal_outcome=
False` (el gate cerrado), 5 campos quedan AUSENTES del dict, no `None`:
`pnl_gross_usdt`, `pnl_net_usdt`, `exit_price`, `close_reason` Y
`closed_at` (se gatean juntos a proposito -- un cierre sin PnL/motivo pero
con fecha es un revelado parcial raro). Tambien fuera del whitelist en
CUALQUIER caso (no son PnL explicito, pero son senales blandas de costo/
resultado que esta vista no necesita): fees, `funding_paid_usdt`,
slippage, `best_price`/`effective_stop`. Las rutas que lo usan NUNCA
declaran `response_model=list[ShadowTrade]` (`ShadowTrade` si declara
`pnl_net_usdt` como campo real -- el filtrado de `response_model` de
FastAPI no protegeria nada ahi).

**3 rutas nuevas bajo `/api/`, todas protegidas con `require_auth` a nivel
de router** (prefijo -> ninguna ruta nueva que alguien agregue despues
puede quedar sin auth por accidente):
- `GET /api/measurement/status` -- `compute_status_async` + `is_
  measurement_gate_open` ya resuelto, sin ninguna logica de velado extra
  (`MeasurementStatus` nunca tuvo PnL por diseno del script original).
- `GET /api/llm/decisions/recent?limit=&fase=` -- reusa `app.llm.schemas.
  parse_decision` (el parser tolerante ya existente, sin reimplementar el
  reparo de JSON) para extraer `confianza`/`razonamiento` de las filas
  `status='OK'`; join liviano a `shadow_trades` para `symbol`/`side` de
  contexto; nunca devuelve `prompt` completo ni `response_raw` crudo. Sin
  regla de velado: la decision del LLM + su razonamiento no es un
  "resultado de la operacion" bajo la regla del usuario.
- `GET /api/shadow-trades/recent?limit=` -- el endpoint critico: calcula el
  gate con `compute_status_async`/`is_measurement_gate_open` en cada
  request (misma fuente de verdad que el endpoint de medicion, nunca un
  segundo calculo paralelo) y pasa cada sombra por el choke point.

**29 tests nuevos**: 6 en `tests/unit/test_serializers.py` (campos AUSENTES
no `None` con el gate cerrado, presentes con el gate abierto, los siempre-
visibles sin importar el gate, los de costo/trailing nunca incluidos, una
sombra abierta serializa limpio, y que un campo nuevo de `ShadowTrade`
jamas aparece sin agregarlo a mano); 8 en `tests/unit/test_
readonly_api_routes.py` (las 3 rutas exigen auth, forma y valores de cada
respuesta, `limit` respetado, razonamiento ausente en filas no-OK, y el
gate abierto SI revela PnL -- forzado bajando `MIN_EFFECTIVE_N` a 1 via
`monkeypatch` en vez de esperar 300 conglomerados reales). Suite completa:
**643 passed, 2 skipped**; `ruff check .` limpio.

**Smoke de nivel 2** (`scripts/run_dev_server.py` contra un respaldo online
fresco de `data/minerva_estabilidad.db`, puerto 8001): las 3 rutas nuevas
devuelven 401 sin login, confirmando que quedaron protegidas. **Hallazgo**:
el login con credenciales de prueba devolvio 401 "Usuario o contrasena
incorrectos" (no 503) -- a diferencia de la Subfase B, esto confirma que
`AUTH_USERNAME`/`AUTH_PASSWORD_HASH`/`JWT_SECRET` YA estan configurados en
el `.env` real (el usuario completo el "Pendiente del usuario" de la
Subfase B entre medio). No hice login con credenciales reales (nunca las
pido ni las conozco) -- el camino feliz completo ya esta cubierto por los
tests con `TestClient`. Confirmado antes y despues que el bot real del
puerto 8000 seguia en `status: ok`, con los mismos conteos (ninguna llamada
real al LLM se disparo desde la copia).

- Sin push (pedido explicito). Se detiene aqui a esperar aprobacion antes
  de la Subfase E (`StaticFiles`) y F (dashboard React), segun el plan
  aprobado.

## Continuidad operativa: arranque/parada de un solo comando (2026-10-14)

El PC actual es de pruebas y se apaga de tanto en tanto; esto deja arrancar
y detener el bot real con un solo comando, de forma segura, y documenta
como mudarlo a un PC 24/7 sin perder la medicion en curso.

**`scripts/start_bot.ps1`**: arranca con las variables fijas de hoy
(`AUTO_OPEN_WITHOUT_LLM=false`, `LOG_LEVEL=INFO`,
`LLM_LIVE_LABELING_ENABLED=true`), REHUSA arrancar si ya hay algo
escuchando en el puerto (evita dos procesos escribiendo la misma base), y
espera a que `/health` responda (hasta 180s -- verificado que un arranque
en frio con backfill de velas puede tardar mas de un minuto) antes de
mostrar su estado. Sin consola visible: el proceso real (`pythonw.exe`) se
envuelve en `cmd.exe /c "... >> archivo 2>&1"` porque PowerShell 5.1 no deja
que `Start-Process` redirija stdout Y stderr al mismo archivo directo
(verificado: error explicito). **Bug real encontrado y corregido durante la
verificacion**: un quirk conocido de `cmd /c` -- si el argumento ya empieza
con una ruta citada, cmd.exe lo parsea mal (sale con `ExitCode 1` sin
siquiera crear el archivo de salida) salvo que TODO el comando quede
envuelto en una capa extra de comillas; confirmado con pruebas aisladas
antes de aplicar el fix. Dos logs con proposito distinto:
`logs\medicion_out.log` (stdout+stderr crudos del proceso) y
`logs\medicion.log` (diario operativo de este script, no del bot -- cuando
se intento arrancar, con que PID, que respondio `/health`; el log interno
detallado del bot sigue en `logs\minerva.log`, sin cambios).

**`scripts/stop_bot.ps1`**: detiene SOLO el proceso que escucha en el
puerto y, si todavia esta vivo, su lanzador (`cmd.exe`) -- nunca otros
procesos python del sistema (confirmado con un proceso de Ollama,
completamente ajeno, corriendo en paralelo durante las pruebas: nunca se
tocó). Limite documentado explicitamente: Windows no da una señal de
apagado cooperativo real para un proceso sin consola -- esto es un
`TerminateProcess`, no un apagado prolijo de
`ShadowLiveLabeler.shutdown()`. Aceptable porque SQLite en modo WAL (el que
usa este proyecto) tolera esto por diseño (cada escritura ya hace *commit*
de inmediato).

**Tarea de arranque automatico**: comandos `Register-ScheduledTask`/
`Unregister-ScheduledTask` verificados (se corrigio un nombre de parametro
real: `-AllowStartIfOnBatteries`, no `-AllowStartIfOnBattery`) en
`docs/MIGRACION_PC.md`. El registro en si dio "Acceso denegado" en el
entorno de esta sesion (probablemente una restriccion de este sandbox, no
necesariamente de una sesion interactiva normal) -- la sintaxis quedo
verificada, pero el usuario deberia confirmar que el registro de verdad
funciona en su propia terminal (con PowerShell como Administrador si hace
falta).

**`docs/MIGRACION_PC.md`** (nuevo): guia corta para mudar el bot a otro PC
sin perder la medicion -- respaldo consistente con la API de backup de
sqlite (nunca copiar el `.db` directo, por el WAL), mismo commit del repo
ANTES de mover los datos (para que `PROMPT_SHA256` siga coincidiendo y
`frozen_hash_ok` salga `true`), recrear el venv, copiar `.env` a mano,
recrear las dos tareas (alerta + arranque), y verificar con
`measurement_status.py` sobre una copia (nunca la base de trabajo).

**Tests**: `tests/integration/test_start_stop_bot_scripts.py` (nuevo,
marcado `slow` + gateado por `MINERVA_RUN_LIVE_TESTS=1`, mismo patron que
`test_bitunix_live.py` -- corre el lifespan real completo, lento y contra
la red real, nunca en CI por defecto): rehusa una segunda instancia,
arranca, confirma `/health`, detiene, confirma el puerto libre; y que
detener sin nada corriendo es un no-op limpio. **Corrido de verdad** (no
solo smoke manual): 2 passed en ~3 min. Suite rapida completa: 643 passed,
4 skipped; `ruff check .` limpio.

**Smoke manual exhaustivo contra el puerto 8001** (nunca el 8000): arranque
+ rechazo de doble instancia + parada + verificacion de que no queda nada
huerfano, repetido varias veces durante la depuracion del bug de `cmd /c`
de arriba. Confirmado en cada paso que el bot real del puerto 8000 seguia
en `status: ok` con `cost_today_usd` sin cambios (ninguna llamada real al
LLM se disparo desde la copia, a pesar de que esta prueba corre con
`LLM_LIVE_LABELING_ENABLED=true` real -- a diferencia de
`run_dev_server.py`, que lo fuerza a `false` a proposito: este script existe
para arrancar el bot REAL con sus settings REALES, asi que la copia de
prueba comparte ese riesgo pequeño y aceptado durante los segundos que esta
arriba).

- Sin push (pedido explicito). Se detiene aqui a esperar aprobacion. La
  Subfase D (dashboard React) se propone por separado, en Plan Mode, sin
  implementar todavia.

## Subfase D: dashboard React (2026-10-14)

Plan propuesto y aprobado en Plan Mode (`C:\Users\Renzo\.claude\plans\
radiant-twirling-waterfall.md`), implementado de corrido segun la nueva
seccion "Autonomia" de CLAUDE.md. Reemplaza `frontend/src/App.tsx` (el
frontend minimo de Fase 1, sin auth ni estilo) por un dashboard que consume
los 3 endpoints nuevos de la Subfase C mas `/health`.

**Verificado antes de empezar**: Node v24.19.0 / npm 11.17.0 ya instalados en
este PC -- sin guia de instalacion. Consultado el skill `dataviz` del
proyecto para la pregunta de que libreria de graficos usar: los 4 visuales
pedidos (barras hacia 300, indicador de banda 15-85%, desglose de causas,
costo/ETA) son del tipo "stat tile/meter", no series de tiempo -- **sin
libreria de graficos**, componentes propios en HTML+Tailwind.

**Dependencias nuevas**: `tailwindcss`/`@tailwindcss/vite` v4 (setup moderno:
un plugin de Vite + `@import "tailwindcss"` en CSS, sin `tailwind.config.js`/
`postcss.config.js` -- v4 ya no los necesita para uso basico);
`@tanstack/react-query` v5; `vitest`/`@testing-library/react`/`jsdom` --
**fijados en `vitest@3.2.4`, no la ultima (5.x)**, porque `vitest@5` exige
`vite@^6.4.0 || ^7.0.0 || ^8.0.0` y el proyecto sigue en `vite@5.4.x` (no se
toco esa dependencia existente solo para agregar un test runner).
**Vulnerabilidades de dev conocidas y aceptadas** (`npm audit`): 2 moderadas +
1 alta + 2 criticas, TODAS en herramientas de test/dev (`@vitest/mocker`,
`tinypool`, el dev server de `esbuild`/`vite`) -- nunca se empaquetan en el
build de produccion ni quedan expuestas (proyecto local, de un solo
desarrollador, sin servidor de desarrollo publico). `npm audit fix` sin
`--force` no alcanza a resolverlas sin forzar `vitest@5` (mismo conflicto de
arriba); no se aplico `--force`.

**Estructura**: `App.tsx` (banner "MODO PAPER TRADING" permanente, fuera del
gate de auth; gate de sesion via `/api/auth/me`; boton de modo oscuro/claro
con `prefers-color-scheme` + `localStorage`) -> `LoginView` o
`DashboardView`. `DashboardView` en 5 tarjetas (Salud, Costo del LLM, Avance
de la medicion con `ProgressBar`/`BandIndicator` reusables, Decisiones
recientes del LLM, Sombras recientes). `api.ts`: un solo punto de paso
(`apiFetch`) para `credentials: "include"` y la reaccion a un 401 (vuelve a
`LoginView` sin perder la cookie -- la maneja el navegador solo, nunca se lee
desde JS).

**La regla de integridad en el cliente**: la tarjeta de sombras recientes
nunca calcula ni infiere PnL -- las columnas "PnL neto"/"Motivo de cierre"
se renderizan unicamente si esos campos *estan presentes* en la respuesta
(`"pnl_net_usdt" in row`), reflejando tal cual lo que decide el choke point
del backend (`shadow_trade_to_public_dict`). El cliente queda agnostico del
gate por construccion.

**9 tests nuevos** (Vitest + React Testing Library): el gate de login
(sin sesion -> `LoginView`; con sesion -> `DashboardView`; el banner de modo
paper se ve siempre, incluso sin sesion); que las columnas de PnL/cierre
estan AUSENTES cuando el mock de `/api/shadow-trades/recent` no las manda y
PRESENTES cuando si (la version del lado del cliente del mismo principio del
choke point); `ProgressBar` calcula el ancho correctamente para 0/150/450 de
300 (nunca mas del 100% aunque el valor supere el objetivo) y siempre
muestra el numero exacto como texto. `npm --prefix frontend run typecheck`
(`tsc --noEmit`) y `npm --prefix frontend run build` limpios.

**`app/main.py`**: mount condicional de `StaticFiles` en `/` (al final, tras
todos los routers) sirviendo `frontend/dist` si existe -- **codigo escrito y
probado, NO desplegado al bot real**: el unico restart de todo el plan
pre-Fase 5 sigue pendiente, es una decision aparte que no se toma como parte
de esta subfase (regla de Autonomia: "nunca... reiniciar el proceso del bot
real en el puerto 8000"). Suite de Python: 643 passed, 4 skipped; `ruff
check .` limpio (sin cambios de comportamiento para los tests existentes,
que nunca pegan a `GET /`).

**Smoke manual de punta a punta** (puerto 8001 con una copia fresca de la
base real + `npm --prefix frontend run dev` en 5173, con el proxy de Vite
actualizado a 8001): confirmado por HTTP (`curl`) que la pagina raiz y
`/src/main.tsx` cargan, y que `/health` + los 3 endpoints nuevos responden
correctamente a traves del proxy (401 sin sesion, forma esperada). **No pude
verificar visualmente en un navegador real** -- esta sesion no tiene una
herramienta de navegador disponible; la verificacion de que el dashboard se
VE y FUNCIONA bien (login real, las 5 tarjetas con datos, modo oscuro,
viewport de telefono) queda pendiente de que el usuario la haga a mano.
**Incidente durante el smoke**: dos procesos de prueba anteriores
(`run_dev_server.py` en 8001 de un smoke previo, y el propio `npm run dev`
de este) quedaron corriendo de fondo despues de `TaskStop` -- confirmados
como propios (via `Get-CimInstance`, nunca del usuario ni del bot real) y
detenidos a mano con `Stop-Process`; el puerto 8000 se verifico intacto antes
y despues de cada paso.

**Pendiente del usuario**: abrir el dashboard de verdad en un navegador
(`scripts/run_dev_server.py` + `npm --prefix frontend run dev`) para
confirmar que se ve y funciona como se espera antes de decidir cuando hacer
el unico restart real que lo pondria en produccion.

- Commits pequenos, sin push (regla de Autonomia). Con esto se completa el
  plan pre-Fase 5 completo (Subfases A, B, C, D) a nivel de codigo -- solo
  falta el despliegue/restart real, que sigue siendo una decision aparte del
  usuario.

## Revision de 940eb2d (2026-10-14): correcciones post-Subfase D

- **Bug de sesion en App.tsx (reproducido por el usuario)**: tras "Cerrar sesion" y volver a entrar en la misma pestana (o tras vencer la sesion de 24h), `/api/auth/me` respondia 200 pero la UI quedaba en LoginView. Causa: `forcedLoggedOut` solo se limpiaba cuando cambiaba `me.data?.username`, y React Query conserva el ultimo `data` ("renzo") durante un error, asi que el valor nunca cambiaba. Decision: eliminar `forcedLoggedOut` y hacer que la cache de `["me"]` sea la unica fuente de verdad. Un 401 de cualquier query (o del propio `/me`) llama a `clearSession`, que escribe `null` en `["me"]` y borra el resto de las queries cacheadas; logout hace lo mismo. Escribir la cache no provoca refetch, asi que no hay bucle; ademas `useMe` ya no hace polling (los 401 se detectan por las queries de datos). Login sigue invalidando `["me"]`.
- Pruebas Vitest nuevas (3, backend simulado con estado): (a) cerrar sesion y reingresar muestra el dashboard; (b) un 401 en una query de datos lleva a LoginView y reingresar muestra el dashboard; (c) un 401 de `/me` no provoca refetchs (1 sola llamada tras 300 ms). Verificado que (a) y (b) FALLAN con el codigo anterior (stash temporal del arreglo) y pasan con el nuevo.
- `frontend/package.json`: `build` ahora es `tsc --noEmit && vite build`.
- `docs/MIGRACION_PC.md`: paso 4 aclara que `start_bot.ps1` ignora `DATABASE_PATH` del `.env` (fija el suyo en el entorno del proceso, que tiene prioridad) y que el respaldo debe quedar como `data\minerva_estabilidad.db`; todos los comandos usan `.\.venv\Scripts\python.exe` y los `.ps1` se invocan con `powershell -NoProfile -ExecutionPolicy Bypass -File`.
- `scripts/hash_password.py`: nueva `build_hash_line` rechaza contrasenas de mas de 72 bytes en UTF-8 (cuenta bytes, no caracteres) con mensaje claro en vez de la excepcion de bcrypt; 6 tests nuevos.
- `scripts/telegram_alert.py`: el payload de `sendMessage` incluye `"disable_web_page_preview": true`; 1 test nuevo.
- Verificacion: pytest 650 passed / 4 skipped, ruff limpio, Vitest 12/12, `tsc --noEmit` y `npm run build` limpios. Commits separados, sin push.

## Herramientas del veredicto de valor de la IA (2026-10-15)

Plan aprobado en Plan Mode (tras 5 commits de pre-Fase 5 ya cerrados en e6d3527). La medicion en vivo NO se toco: ningun cambio a `ai_value.py`, `prompts.py`, `shadow_live_labeler.py`, `data/` ni `.env`.

- **`app/trading/ai_value_report.py` + `scripts/ai_value_report.py`** (solo lectura, sobre una COPIA, `mode=ro`, assert de ruta distinta de `settings.database_path`). Guardia del gate estructural: `build_report` llama primero a `compute_status` (sin PnL) y con el gate cerrado devuelve solo contadores SIN haber ejecutado la consulta que lee resultados (test: el cargador de resultados se parchea para lanzar). Guardia estricta (D1): si el gate abre pero el N efectivo LIMPIO (sin RECONCILE_FAILED ni piloto) es < 300 en algun lado, no se muestra ni Δ ni IC ni desglose (`ai_value_verdict` los devuelve aun con N insuficiente; no se modifico). Hallazgo: el gate de `measurement_status` no excluye las no confiables y el veredicto si; el informe muestra ambos N.
- **Veredicto** = `ai_value_verdict` con hash congelado EXPLICITO (no "el mas reciente", para coincidir con el gate), `MIN_EFFECTIVE_N=300`, SIN_LLM <= 10 % sobre grupos de la medicion, δ=3 %. **Seccion (m)**: particion de todos los grupos (medicion / piloto / otro hash / omitidos sin fila ANTERIORES y POSTERIORES a la activacion / no confiables), omitidos fuera del veredicto (test: veredicto identico con y sin ellos; `omitidos posteriores == measurement_status.omitted_without_row`), SIN_LLM por causa, franja de 4 h y tercil de atr_pct. **Sensibilidad** sin filas `json_repaired` (ROBUSTO/SENSIBLE; orientativa si N < 300). **Desglose** por estrategia representativa y simbolo solo con APORTA_VALOR (exploratorio, cubos < 30 conglomerados sin IC).
- **Bandera sintetica**: `--allow-synthetic-for-tests` solo se acepta si TODAS las filas de `llm_logs` tienen `model == "synthetic-test"` y ninguna el hash real; sobre la copia real se rechaza (verificado, codigo 4). `--out` guarda el registro oficial en UTF-8, solo con veredicto y sin sobrescribir (D2: una sola mirada; el veredicto usa todos los datos al correr, no trunca al instante de los 300).
- **Bug real encontrado al medir**: imprimir "δ" revienta en la consola cp1252 de Windows; el CLI fuerza UTF-8 en stdout (cubierto por pruebas por subproceso).
- **`scripts/shadow_report.py` endurecido** (hallazgo del plan): antes imprimia PnL por estrategia y el criterio de la IA sin mirar el gate, y no exigia una copia. Ahora exige copia (assert) y se niega con el gate cerrado (codigo 3).
- `row_to_shadow` (ex `_row_to_shadow`) pasa a ser publico en `shadow_repo`. Doc: `docs/VEREDICTO_3_6.md` (procedimiento: placebo -> copia consistente -> una sola corrida con `--out`) y nota en `docs/FASE3_6_LLM.md` seccion (m).
- Pruebas: 32 nuevas (suite 682 passed / 4 skipped, ruff limpio). Rendimiento a escala real (300/300 sinteticos, informe completo con sensibilidad y desglose): ~2,3 s.
- **Corrida contra datos reales** (copia consistente por `Connection.backup` al scratchpad, no a `data/`): `ai_value_report.py` -> GATE CERRADO, N efectivo 1/300 y 0/300, sin ningun resultado; `shadow_report.py` -> REPORTE NO MOSTRADO; la bandera sintetica sobre la copia real -> RECHAZADO.
- **Aviso**: al verificar, el puerto 8000 NO estaba escuchando y la copia termina el 2026-10-14 16:00 UTC (14 filas MEDICION, 0,080 USD): el bot real esta caido. No se toco ni se arranco (regla de Autonomia); arrancarlo es decision tuya (`scripts/start_bot.ps1`). Mientras este caido la medicion no avanza.
- Pendiente previo al veredicto real: correr y registrar `scripts/placebo_check.py` (seccion f). Commits pequenos, sin push.

## Placebo listo antes del gate (2026-10-17)

- **`scripts/placebo_check.py` reescrito** (logica en `app/trading/ai_value_placebo.py`): lee la copia en solo lectura (`mode=ro`), assert de ruta distinta de la base de trabajo, y usa EXACTAMENTE los filtros del veredicto: `load_verdict_inputs` (nuevo, extraido de `build_report` en `ai_value_report.py`; reutiliza `_verdict_inputs`) -> hash congelado explicito, sin piloto, sin sombras no confiables. Antes usaba el hash "mas reciente" y NO excluia RECONCILE_FAILED. El calculo sigue siendo `ai_value.placebo_calibration`, sin tocar.
- **Hallazgo de calibracion vacia**: una repeticion solo puede dar APORTA_VALOR si el azar deja >= 300 conglomerados por lado; con ~600 conglomerados y 50 por ciento de aprobacion casi ninguna llega (solo X = 300 exacto) y "Calibrado: SI" no prueba nada. El script calcula la probabilidad exacta (binomial) de llegar al punto de analisis, la tasa estimada contando solo esas repeticiones y avisa si es < 50 por ciento. Corrida real sobre la copia actual: 1 conglomerado cerrado, probabilidad ~0, advertencia mostrada.
- Bloque final listo para pegar en `docs/FASE3_6_LLM.md` y `--out` (UTF-8, no sobrescribe, no se crea sin resultado). Codigos de salida: 0 calibrado, 3 nada que calibrar, 4 bandera sintetica rechazada, 5 `--out` existe, 6 NO calibrado.
- **Tiempo medido** (sintetico, 640 conglomerados, 1.000 repeticiones): 694 s = 11,6 min (0,69 s/rep); 25/1000 por azar (2,5 por ciento, Wilson [1,7; 3,7]) -> calibrado. Supera ligeramente los ~10 min: decision (Autonomia) = NO implementar paralelismo todavia; se documenta la propuesta que conserva el calculo exacto (pre-sortear etiquetas en serie con la misma semilla y evaluar los veredictos en un pool de procesos, con prueba de equivalencia contra `placebo_calibration`) y la recomendacion: 1.000 repeticiones una sola vez en segundo plano para el registro oficial; `--n-repeats` 100-300 para ensayos (con 300 el Wilson superior ya roza el 5 por ciento).
- Pruebas: 18 nuevas (a: sin efecto real con 310 por lado y constantes reales el placebo calibra; b: una sombra con reconciliacion fallida, una del piloto, una de otro hash y una omitida no entran, con resultado identico al de la base limpia; probabilidad binomial exacta; CLI por subproceso incl. salida por pipe cp1252, `--out`, codigos). Suite: 700 passed / 4 skipped, ruff limpio. Commits pequenos, sin push.

## Criterio condicional del placebo y regla de procedimiento (2026-10-17)

Ajuste de criterio decidido por Renzo ANTES de tener datos (revision de 51ee00f).

- **`app/trading/ai_value_placebo.py`** (no `ai_value.py`): "Calibrado" se decide con la tasa condicional (frecuencia de APORTA_VALOR por azar / probabilidad exacta de que una repeticion llegue al punto de analisis) < 5 %, y solo si esa probabilidad es >= 50 %. Si es menor: NO INFORMATIVO (ni SI ni NO), codigo de salida 7. `classify_calibration` es la unica funcion que decide; el `calibrated` de `PlaceboResult` (frecuencia bruta) ya no decide. La frecuencia bruta y su IC de Wilson se imprimen rotulados [informativo]. El bloque de registro lleva el resultado y la regla.
- Se guarda con `--out` tambien un resultado NO INFORMATIVO o NO calibrado (son resultados del procedimiento): el nombre debe llevar fecha porque `--out` no sobrescribe. Codigos: 0 CALIBRADO, 6 NO calibrado, 7 NO INFORMATIVO, 3 nada que calibrar, 4 bandera sintetica rechazada, 5 `--out` existe.
- **Regla de procedimiento** (docs/VEREDICTO_3_6.md y docs/FASE3_6_LLM.md seccion f): al abrirse el gate, (1) copia, (2) placebo oficial de 1.000 repeticiones, (3) NO INFORMATIVO -> NO correr el veredicto, esperar 7 dias mas de datos y repetir desde (1), (4) CALIBRADO -> `ai_value_report.py` UNA vez con `--out` sobre la MISMA copia, (5) NO calibrado -> no se usa el criterio, se revisa el diseno. La espera depende solo de conteos, nunca de resultados. El texto final de `ai_value_report.py` repite este orden al imprimir un veredicto (no en los estados de gate cerrado o punto no alcanzado).
- Nota de implementacion: la regla se aplica a la copia que se use; el informe NO comprueba que el placebo exista ni que sea de la misma copia (solo lo recuerda en el texto). Con ~600 conglomerados (300 por lado) y 50 % de aprobacion casi nadie llega al punto de analisis, asi que el primer placebo oficial saldra casi seguro NO INFORMATIVO y la regla pedira esperar 7 dias.
- Pruebas nuevas/actualizadas: caso CALIBRADO con alta probabilidad de llegar (310 por lado, constantes reales); frecuencia bruta 4,8 % con probabilidad 80 % -> NO calibrado (tasa condicional 6 %); probabilidad < 50 % -> NO INFORMATIVO (y el 50 % exacto ya es informativo, el 5 % exacto no es < 5 %); codigos de salida 0/6/7 y `--out`; el recordatorio de procedimiento aparece solo con veredicto. Suite 709 passed / 4 skipped, ruff limpio. Smoke contra la copia real: NO INFORMATIVO (1 conglomerado cerrado), codigo 7. Commits pequenos; push pedido explicitamente por el usuario en este mensaje.

## Resumen diario por Telegram (2026-10-17)

- **`scripts/telegram_daily.py`** (hermano de `telegram_alert.py`, que avisa de caidas; este manda UN mensaje al dia): estado de /health (ok/stale, frozen_hash_ok, etiquetado activo), etiquetadas y fallidas de las ultimas 24 h, costo del LLM en 24 h y acumulado de la medicion, N efectivo por lado (x/300) y gate. Nunca PnL: solo consulta `llm_logs` y `compute_status` (sin PnL); test con trace de SQL que ninguna sentencia menciona pnl/exit_price/close_reason, y que un PnL inconfundible sembrado no aparece. Si el bot no responde, el mensaje lo dice y sigue informando la base; si la base no se puede leer, tambien lo dice; solo un envio fallido a Telegram da codigo de salida 1.
- **Decision de fuente de datos**: NO se usan los endpoints autenticados. El `.env` guarda solo el HASH de la contrasena (no se puede hacer login), y `/health` da contadores "de hoy" (desde la medianoche local), no de 24 h. Se usa la alternativa permitida: copia de solo lectura con la API de backup de sqlite (fuente `mode=ro`) en un directorio temporal del sistema (assert: nunca dentro de `data/`), borrada al terminar; `/health` en vivo para saber si el bot responde. Se refactoriza `check_health.py` (nuevo `fetch_health`, mismo comportamiento) para reutilizar la consulta.
- **Hallazgo**: la base por defecto del script es `data/minerva_estabilidad.db` (la que fija `start_bot.ps1`), NO `settings.database_path`: el default de `app/config.py` es `data/minerva.db` (sin escrituras desde el 12-oct) y el `.env` podria apuntar a ella; el resumen habria informado una medicion inexistente. Hay un test que ata ese default al valor de `start_bot.ps1`.
- **Tarea "MinervaDailySummary"** (docs/MIGRACION_PC.md): `Register-ScheduledTask` diario 09:15. Hallazgo verificado: `New-ScheduledTaskTrigger -Daily -At` guarda la hora en UTC ("...12:15:00Z") y Chile cambia el horario de verano, asi que la hora se corre una hora tras el cambio; se fija `StartBoundary` en LOCAL sin "Z" (verificado construyendo los objetos sin registrar; el registro real exige privilegios y no se hizo). `-StartWhenAvailable` la corre si el PC estaba apagado a las 09:15. Supone el PC en la zona de Chile (verificado: "Pacific SA Standard Time"). El checklist de migracion pasa a tres tareas.
- Verificacion contra datos reales: copia consistente de la base viva al scratchpad (no a `data/`) y `telegram_daily.py --dry-run --db <copia>` termino limpio: bot OK (ya esta corriendo de nuevo), 0 etiquetadas en 24 h, 14 filas y 0,0803 USD acumulados, 3/300 y 4/300, gate cerrado. Observacion: la ultima fila de la medicion es del 2026-10-14 16:00 UTC (el bot estuvo caido ~2,8 dias); el resumen diario existe justamente para verlo. No se toco el bot, `data/`, `.env` ni el prompt congelado.
- Pruebas: 20 nuevas (suite 729 passed / 4 skipped, ruff limpio). Commits pequenos, sin push.

## Diagnostico del hueco de la medicion tras el 14-oct 16:00 UTC (2026-10-17)

Pregunta: la ultima fila de `llm_logs` MEDICION es del 2026-10-14 16:00 UTC, pero el bot siguio vivo hasta ~00:45 UTC del 15-oct (cierres de 20:00 y 00:00 UTC). Hubo sombras sin fila? **Resultado: NO. No hay bug y no se perdio ninguna fila; simplemente no hubo senales accionables.** Metodo: copia consistente de la base viva (API de backup, fuente `mode=ro`) al scratchpad (no a `data/`) y lectura de solo lectura de `logs/minerva.log`; consultas sin PnL.

- **Senales (tabla `signals`)**: del cierre de las 17:00 UTC del 14-oct al de las 00:00 UTC del 15-oct hubo 8 cierres de vela y 190 evaluaciones (17:00 10, 18:00 10, 19:00 10, 20:00 60, 21:00 10, 22:00 10, 23:00 10, 00:00 70), **las 190 HOLD, 0 accionables**. Desde el 14-oct 16:00 UTC no existe ninguna senal LONG/SHORT en toda la base (0 filas).
- **Sombras**: la ultima se abrio en el cierre de las 16:00 UTC (8 sombras, las 8 con fila OK en `llm_logs`; el log lo confirma: ultimas lineas "Operacion sombra abierta" a las 13:00:19 hora local = 16:00 UTC, y ninguna despues). Sombras abiertas desde el 14-oct 16:00 UTC: 0. **Sombras posteriores a la activacion sin fila en `llm_logs`: 0** (omitidas por demora, por error de la API, por hash o por reinicio: ninguna). Las sombras sin fila que existen son todas ANTERIORES a la activacion (4-oct: 3, 11-oct: 18, 12-oct: 25, 13-oct: 26, 14-oct antes de las 12:00 UTC: 2), excluidas del veredicto por diseno.
- **Por que se ve una "caida" de filas**: la medicion avanza solo cuando hay senales; en esas 8 horas el mercado no dio ninguna. No es el etiquetado el que se detuvo. (Las horas del log estan en hora local de Chile, UTC-3.)
- **Cronologia del proceso (logs)**: arranque 14-oct 08:33 local (11:33 UTC); ultimo heartbeat 21:36:25 local y ultimo `GET /health` 21:39:38 local (00:39 UTC del 15-oct); **despues no hay NINGUNA linea hasta el arranque del 17-oct 07:42 local (10:42 UTC)**, ~58 h despues, sin traza de apagado ordenado ("Shutting down"/"Finished"), es decir terminacion abrupta (PC apagado/suspendido o proceso matado; coherente con que el PC de pruebas se apaga cada cierto tiempo; la causa exacta no queda en los logs). La tabla `signals` lo confirma: hueco de 58 h entre los cierres del 15-oct 00:00 y el 17-oct 08:00/10:00 UTC.
- **Despues del reinicio** (17-oct): el log avisa "Universo obsoleto o ausente (> 48h): no se generan senales hasta el proximo refresco" y 5 s despues "Universo refrescado: 10 simbolos incluidos" (comportamiento esperado, sin efecto en cierres). A las 10:48-10:49 UTC el generador evaluo de una vez las velas pendientes (1d 00:00, 4h 08:00, 1h 10:00) y a las 11:00 UTC la vela puntual: todas HOLD, 0 sombras. `GET /health` y el resumen diario muestran `frozen_hash_ok` verdadero y etiquetado activo.
- **Observacion de diseno (no es un bug, sin cambios de codigo)**: tras una caida larga el generador evalua en bloque las velas atrasadas al arrancar; si alguna de esas evaluaciones abriera una sombra, su demora seria > 1800 s y quedaria SIN_LLM sin fila (omitida posterior a la activacion, seccion (m)), tal como esta documentado. Esta vez no ocurrio porque todas fueron HOLD. Cuantas mas caidas largas haya, mas omitidos posteriores: el punto de analisis se retrasa pero el sesgo queda declarado aparte.
- **Consecuencia practica**: el N efectivo actual (3/300 y 4/300) y el ritmo de ~6 conglomerados efectivos por dia se calcularon con muy poca historia; las caidas del PC (como la de 58 h) y los periodos sin senales alargan la medicion. No se modifico codigo.

## Subfase 3.7: bot de Telegram por long-polling (2026-10-17)

Plan aprobado en Plan Mode (con la decision del usuario: /metrics muestra SOLO AVANCE, con el gate cerrado y tambien abierto). Detalle de diseno, reglas de seguridad y pasos de despliegue en `docs/FASE3_7_TELEGRAM.md`. **Pendiente del usuario: reiniciar el bot real (stop_bot.ps1 / start_bot.ps1 entre dos cierres de vela) y el smoke manual con el token real.** No se toco el bot del puerto 8000, `data/`, `.env` ni el prompt congelado.

- **Hallazgo previo**: la pausa del scheduler NO existia (`bot_status` solo estaba en un docstring) y el kill switch del motor de riesgo solo bloquea entradas REALES. Se construyo `app/trading/control_state.py` (`system_state.scheduler_paused`, persistente) y `Scheduler.run_cycle` sale al inicio si esta pausado (sin senales, sombras ni etiquetado = SIN medicion; los cierres del monitor no pasan por ahi). `GET /health` gana el bloque aditivo `control: {paused, kill_switch_active}`; `telegram_daily.py` avisa de una pausa deliberada.
- **`app/telegram/`**: `client.py` (httpx async; el token nunca sale: las excepciones se convierten en `TelegramApiError` con solo el tipo y `from None`), `bot.py` (allowlist estricta: chat PRIVADO con id exacto, el resto ignorado en silencio; descarta lo pendiente al arrancar y mensajes de mas de 120 s; backoff 1 s a 5 min; `except Exception` por iteracion), `commands.py`, `confirmation.py`, `runtime.py` (arranque aislado, apagado por defecto).
- **Comandos**: `/status`, `/positions`, `/balance` (cuenta real simulada, PnL flotante con los marks del monitor, sin red), `/metrics` (solo avance + cuenta real; nunca PnL ni la comparacion APROBADA/RECHAZADA; el modulo no importa shadow_report/ai_value*/shadow_repo, con test estructural y traza de SQL), `/pause`, `/kill` (kill switch + pausa), `/resume` (levanta pausa y kill switch; no el stop por drawdown). Los tres de control piden `SI` en 60 s del MISMO comando, una sola solicitud pendiente, se consume al usarla, y advierten que detienen la medicion; cada accion queda en el log y en `telegram_last_control_action`.
- **Aislamiento**: flag `TELEGRAM_COMMANDS_ENABLED` (default false); `start_bot.ps1` lo fija en true SOLO con -Port 8000; `run_dev_server.py` lo fuerza false y vacia el token (un token admite un solo consumidor de getUpdates). Test con el cliente fallando de todas las formas (401, 409, 429, red, JSON, excepcion): un `Scheduler.run_forever` real, el etiquetado y un monitor (cierre por SL) siguen avanzando y el event loop no se bloquea; un bug que escape de `run_forever` no propaga; construirlo no puede impedir el arranque.
- **Telegram verificado en la doc oficial** (getUpdates offset/limit/timeout/allowed_updates, campos de Update/Message/Chat, sendMessage, <=1 mensaje/s por chat y 429). NO verificado, tratado de forma defensiva: codigo exacto ante dos consumidores (409), `retry_after`, `link_preview_options` vs `disable_web_page_preview`.
- **Bug de prueba encontrado**: el E2E con `httpx.MockTransport` colgaba el event loop porque las respuestas vacias no ceden el control (con red real cada llamada espera); el simulador ahora espera como un long-poll. Un `TaskStop` de pytest no dejo procesos huerfanos (verificado). Efecto colateral anotado: `PaperBackend.unrealized_pnl_usdt` (refactor sin cambio de comportamiento, `get_equity` lo usa) y `risk_engine` gana accesores publicos (`is_kill_switch_active`, `is_drawdown_stop_active`, `get_active_circuit_breaker_until`).
- Pruebas nuevas: 94 (pausa 8, bot 29, comandos de lectura 15, metrics 7, control 19, runtime/aislamiento 13, E2E 1, resumen diario 1, y los E2E de arranque). Suite 823 passed / 4 skipped (antes 729), ruff limpio.

## Diagnostico del hueco de la medicion: nota adicional

- El diagnostico del 17-oct (sin senales, sin filas perdidas) esta arriba; la causa de los ~58 h sin ciclos (cierre abrupto del proceso, sin traza de apagado) no se puede determinar con los logs. La pausa persistente de la 3.7 NO es la causa (no existia).

## Resultados solo con registro oficial del veredicto + stop_bot.ps1 (2026-10-17)

Alinea la API/dashboard con la regla de procedimiento: abrir el gate NO basta para ver resultados (primero el placebo oficial y el veredicto; si el placebo sale NO INFORMATIVO se esperan 7 dias sin ver resultados). Estos cambios llegan al bot real en el proximo reinicio PLANIFICADO (yo no lo reinicie; el puerto 8000 solo se miro en modo lectura). No se toco `data/`, `.env` ni el prompt congelado.

- **Condicion unica `is_outcome_reveal_allowed`** (`app/trading/measurement_status.py`) = gate abierto Y un registro oficial en `docs/VEREDICTO_3_6_REGISTRO*.txt` con la linea `Veredicto: APORTA_VALOR|NO_APORTA_VALOR|INCONCLUSO` (`app/trading/verdict_record.py`; falla cerrado: archivo ausente, vacio, ilegible, > 1 MB, directorio, sin la linea o con las palabras sueltas NO cuenta; el `docs/` es el del repo, no el cwd; se lee en cada peticion, asi que desbloquea sin reiniciar). La usan `GET /api/shadow-trades/recent` (serializers: los campos de resultado), `GET /api/measurement/status` (nuevo `outcome_reveal_allowed`, ademas de `gate_open`) y `scripts/shadow_report.py` (que antes imprimia PnL y el veredicto apenas el gate abria: otra fuga). Telegram no muestra resultados en ningun caso.
- **Dashboard**: con el gate abierto y sin registro muestra "Gate abierto: seguir docs/VEREDICTO_3_6.md" y no PnL; ademas FALLA CERRADO: solo muestra las columnas de resultado si `outcome_reveal_allowed === true`, aunque el backend las mandara (o sea un backend viejo sin el campo tampoco las muestra). Vitest 16/16, `tsc --noEmit` + `vite build` limpios.
- **Nombres** (docs/VEREDICTO_3_6.md): el `--out` del informe debe cumplir `docs/VEREDICTO_3_6_REGISTRO_<fecha>.txt` (es lo que desbloquea); el placebo usa `docs/PLACEBO_<fecha>.txt`, que NO cumple el patron. Hay un test que enlaza el informe real con el detector (el `--out` con nombre de placebo no desbloquea; con el del registro si).
- **Tests** (condicion unica, matriz pedida): gate cerrado -> nada (tambien con registro valido); abierto sin registro -> nada; abierto con registro sin veredicto valido -> nada; abierto con registro valido -> se revela; mas el desbloqueo sin reiniciar y los casos de fallo cerrado.
- **`stop_bot.ps1`**: la cadena real (verificada en 8001) es `cmd.exe` -> `.venv\Scripts\pythonw.exe` (launcher del venv, trampolin) -> `python.exe` del interprete base (el que escucha). Antes el padre del listener era el launcher y decia "no parece ser el lanzador -- no se toca". Ahora reconoce la cadena completa (y la corta cmd -> real), verifica ANTES de tocar un padre: que haya empezado antes que su hijo (descarta PID reciclados), que el ejecutable del launcher sea EXACTAMENTE el `.venv\Scripts\pythonw.exe` de este repo (ruta completa, sin distinguir mayusculas), y que el cmd.exe lance ese pythonw con `--port <puerto>`; detiene real -> launcher -> cmd y CONFIRMA que no sobrevive ninguno (mismo PID y misma hora de creacion), saliendo con 1 si alguno sigue. Probado en el puerto 8001: cadena de 3 reconocida y detenida, `Get-Process` posterior sin los 3 PIDs, puerto libre; prueba negativa con un listener cuyo padre es `.venv\Scripts\python.exe` (no pythonw): detiene el listener y deja intacto al padre con aviso. Test de integracion gated (`MINERVA_RUN_LIVE_TESTS=1`) ampliado: exige la cadena de 3 y que los 3 PIDs mueran.
- **Hallazgos de paso**: (1) `start_bot.ps1` fijaba `LLM_LIVE_LABELING_ENABLED=true` tambien fuera del puerto 8000 -- una instancia de prueba sobre una copia habria llamado a la API de Anthropic (gasto real) al abrir una sombra; ahora es true SOLO con 8000 (igual que el flag de Telegram). (2) El test de integracion copiaba `settings.database_path` (base vieja, backfill de dias, /health tardaba > 180 s); ahora copia `data/minerva_estabilidad.db`, la que usa start_bot.ps1 (2 passed en 14 s). (3) Al mirar el 8000 en modo lectura: el bot real corre desde las 09:32 de hoy con la misma forma de cadena; sus rutas no se pudieron leer sin permisos, asi que si tu consola no tiene los mismos privilegios, stop_bot.ps1 no verificara el padre y lo dejara intacto (seguro, no lo rompe) o no podra detenerlo. (4) `ai_value_report.py --out` no impide crear un SEGUNDO registro con otra fecha (la regla es "una sola mirada"); queda como observacion.
- Verificacion: pytest 859 passed / 4 skipped, ruff limpio, Vitest 16/16, build limpio. Commits pequenos y push al terminar (regla de Autonomia vigente).

## Un unico registro oficial del veredicto (2026-10-17)

- `scripts/ai_value_report.py --out` ahora se niega (codigo 5, antes de abrir la base) si ya existe CUALQUIER `VEREDICTO_3_6_REGISTRO*.txt` con un veredicto valido (`app.trading.verdict_record.find_verdict_record`), en el `docs/` del repo o en la carpeta del `--out`, no solo si existe el mismo nombre; no crea nada. Un borrador sin la linea `Veredicto: <X>` no cuenta. Reemplazar el registro es una accion manual deliberada. Tests: registro previo de otra fecha (en la carpeta del `--out` y en el `docs/` del repo) -> rechazado y sin archivos nuevos; borrador no valido -> no bloquea. Doc actualizada (docs/VEREDICTO_3_6.md).

## Subfase 3.8: reporte de paso a dinero real (2026-10-17)

Plan aprobado por ti el mismo dia (Plan Mode). **Solo recomienda: v1 sigue 100 % paper**, el reporte nunca habilita ejecucion real. Detalle y reconciliacion completa en `docs/PASO_DINERO_REAL.md`; fila 3.8 y punto 8 de `docs/FASE3_PLAN.md` actualizados.

- **`scripts/real_money_report.py`** (logica en `app/trading/real_money_report.py`): mismo patron que `ai_value_report.py` -- `--db` obligatorio con `assert` de ruta distinta de `settings.database_path`, `mode=ro`, UTF-8. Codigos: 0 informe, 2 error de uso, 3 reveal no permitido, 4 bandera de pruebas rechazada, 5 `--out` ya existe.
- **Orden estricto**: (1) `is_outcome_reveal_allowed` (gate abierto + registro oficial); si es falso solo dice QUE FALTA y **no lee `trades` ni ningun resultado** (test con los cargadores parcheados para fallar); (2) el registro oficial es el insumo: si no dice `APORTA_VALOR` concluye "NO PASAR A DINERO REAL" y no evalua nada mas; (3) solo entonces evalua el brazo ejecutado, los riesgos operativos medidos y la transferencia a las elegibles (exploratoria, no es criterio). El veredicto se CITA del registro (nuevo `parse_verdict_record` / `load_official_record`), no se recalcula.
- **Decisiones (anotadas segun Autonomia)**: (a) se RETIRAN los regimenes <30 / 30-100 / >=100 por lado de la fila 3.8: la 3.6 fija un unico punto de analisis (300 conglomerados, delta 3 %) y mostrar cifras antes contradice la regla de una sola mirada; (b) PF > 1,3 **y** limite inferior del IC 95 % del PF (bootstrap por conglomerados, mismas constantes que `ai_value.py`) > 1,0 -- tu eleccion; (c) cada criterio es PASA / NO PASA / SIN DATOS y la falta de datos NUNCA pasa; (d) drawdown = contador - linea base `real_arm_drawdown_baseline`; (e) los CRITICAL de reconciliacion solo existen en el log: sustitutos en la base (`fill_source='TICK_UNRECONCILED'`, `close_reason='RECONCILE_FAILED'`, `reconciliation_failed=1`) mas `--log-file` opcional; (f) las cerradas sin PnL no entran a la rentabilidad pero marcan la reconciliacion; (g) `--out` solo guarda informes con conclusion y NO puede llamarse como el registro oficial (error de uso), para que este reporte no pueda fabricarlo.
- **Dependencia: subfase 3.9 (sin planificar)**. El brazo ejecutado no existe: `real_open_allowed` no esta conectada, `AUTO_OPEN_WITHOUT_LLM=false` y `trades` esta vacia. Hasta entonces los criterios de la cuenta ejecutada salen `SIN DATOS`. 3.9 debe conectar `real_open_allowed`, marcar `decision_source='LLM_APROBADA'`, persistir `signal_group_key` y fijar `system_state.real_arm_started_at` y `real_arm_drawdown_baseline`. La planifico aparte cuando lo pidas.
- **Riesgos operativos** (tabla en `docs/PASO_DINERO_REAL.md`, marcada [V] verificado / [D] medido / [S] supuesto / [NV] no verificado): el informe calcula lo medible -- retrasos de decision frente a `LLM_REAL_MAX_DELAY_SECONDS`, simbolos no ejecutables con margen x apalancamiento segun `contract_specs_cache`, huecos de disponibilidad (horas con evaluaciones en `signals`), PF estresado con comisiones x2 y slippage x2/x3, comisiones y funding. **No verificado contra la documentacion oficial** (queda sin cerrar): latencia de ordenes, limites de tasa, modo de posicion, mecanica de cobro del funding, base de cobro y tipo (maker/taker) de la orden a mercado, minimo en USDT, semantica de `priceProtectScope`, ejecucion del SL/TP en el exchange en saltos de precio.
- **Garantia estructural**: tests por AST de que ni el modulo ni el script importan ningun backend de ejecucion ni cliente de red, y un test en subproceso de que tras importar el script no queda cargado `anthropic` ni `app.execution.{bitunix,paper,base}_backend`. De rebote entran el cliente PUBLICO de mercado (`app.market.bitunix_rest`, por la constante del hash congelado) y `app.execution.pnl` (matematica pura de PnL); no se llama a nada.
- **Corrida real** (regla de CLAUDE.md): copia consistente de `data/minerva_estabilidad.db` en el scratchpad (origen abierto `mode=ro`, `data/` intacto) -> `scripts/real_money_report.py` termina con codigo 3: "gate CERRADO: APROBADA 3/300, RECHAZADA 4/300" y "no existe el registro oficial", sin ningun resultado, mas los contadores. Como esa corrida solo recorre la rama "no permitido", ejecute ademas los cargadores de la rama evaluada contra la misma copia (solo conteos): 0 operaciones del brazo ejecutado, 17 filas de retraso (2 > 60 s), 24 horas con evaluaciones y un hueco de 58 h, universo de 10 simbolos: 8 ejecutables, `TRXUSDT` no ejecutable (148 < 150 de minimo) y `XMRUSDT` sin precio en cache. Sin errores con el esquema y las fechas reales.
- **Cambio de paso**: `trades_repo._row_to_trade` pasa a ser publica (`row_to_trade`) para reusarla sin duplicar el mapeo fila -> `Trade`.
- Verificacion: pytest 925 passed / 4 skipped (antes 862), ruff limpio. Frontend sin cambios. Commits pequenos y push al terminar (regla de Autonomia vigente).
- **Observacion de la medicion** (de la misma copia, no del reporte): el ritmo de los ultimos 3,2 dias da ~318 dias para llegar a 300 conglomerados APROBADA y ~238 para RECHAZADA; es una estimacion con una ventana muy corta y el hueco de 58 h incluido, pero conviene vigilarla porque el gate (y todo lo que cuelga de el, incluido este reporte) no abrira en semanas al ritmo actual.

## Ritmo de la medicion sobre tiempo EN MARCHA (2026-10-17)

El ritmo de calendario dividia por todos los dias de la ventana, incluidas las ~58 h con el bot apagado, y subestimaba lo que hace el bot cuando corre. Ahora hay DOS ritmos, rotulados, en todas las salidas. Sin PnL; solo marcas de tiempo.

- **Definicion** (`app/trading/measurement_status.py`): "en marcha" = tiempo en que el bot estuvo evaluando senales, medido con las marcas `signals.evaluated_at` (una fila por (simbolo, estrategia, timeframe, vela); como `mean_reversion` es de 1 h, hay al menos una marca por hora con el bot evaluando -- en la copia real el espaciado normal es de 1 h). Un hueco de MAS de `OFFLINE_GAP_HOURS = 2` entre marcas consecutivas cuenta ENTERO como apagado (un hueco de exactamente 2 h aun cuenta en marcha); el extremo final de la ventana funciona como una marca mas (si la ultima evaluacion fue hace <= 2 h, el bot sigue en marcha); el tramo anterior a la primera marca leida cuenta apagado. Funcion pura `running_seconds`, probada con huecos sinteticos.
- **Calculo**: mismo numerador que el ritmo de calendario (conglomerados efectivos etiquetados dentro de la ventana de 7 d o desde la primera fila) dividido por los dias EN MARCHA de esa misma ventana. Campos nuevos de `MeasurementStatus`: `running_window_days`, `uptime_pct_window`, `uptime_pct_total` (desde el inicio de la medicion), `rate_*_running_per_day` y `days_needed_running(side)`. Sin ninguna marca en la base (anterior a la 3.3, o sintetica) el ritmo en marcha es `None` ("sin marcas de evaluacion"), nunca 0 %.
- **Lectura barata**: `SELECT DISTINCT substr(evaluated_at,1,16)` desde el inicio de la medicion menos 2 h (una marca anterior ya dejaria un hueco > 2 h hasta la primera leida, que cuenta apagado). `signals` tiene ~190 filas/dia; la consulta tarda < 1 ms en la copia real, sin indice nuevo ni cambio de esquema.
- **Salidas**: texto comun `pace_lines` (rotulos "Ritmo por CALENDARIO ... INCLUYE el tiempo con el bot apagado" y "Ritmo EN MARCHA ... un hueco de mas de 2 h sin ciclos cuenta como apagado", mas "Tiempo en marcha: X % de la ventana (N dias en marcha); Y % desde el inicio" y un aviso si hay < 1 dia en marcha) en `scripts/measurement_status.py`, `/metrics` de Telegram y el resumen diario (que antes no traia ritmo). API `/api/measurement/status` con los campos nuevos (opcionales en el frontend: un backend viejo sigue funcionando) y dashboard con una tabla calendario / en marcha por lado mas dos fichas de tiempo en marcha. Los dias EN MARCHA equivalen a dias de calendario solo si el bot no vuelve a apagarse (queda escrito en cada salida).
- **Cifras en la copia real** (copia fresca de `data/minerva_estabilidad.db`, ultima fila de la medicion 2026-10-17 17:01 UTC; N efectivo APROBADA 4/300, RECHAZADA 4/300): ritmo de calendario 1,25 conglomerados/dia por lado -> ~237,5 dias de calendario; ritmo en marcha 5,27 conglomerados/dia en marcha por lado -> ~56,2 dias EN MARCHA; tiempo en marcha 23,6 % (0,76 de 3,2 dias de la ventana; igual desde el inicio de la medicion). Verificado con un calculo manual independiente (suma de tramos con marcas menos los huecos > 2 h): 0,759 dias, 23,6 %. Huecos > 2 h en toda la historia de `signals`: 134,4 h (4-10 oct, antes de la medicion), 4,0 h (11 oct), 25,6 h (13-14 oct) y 58,8 h (15-17 oct). **Advertencia**: la estimacion en marcha descansa en 4 conglomerados en 0,76 dias en marcha, asi que es muy volatil (el propio aviso lo dice); sirve para ver el orden de magnitud (~2 meses en marcha frente a ~8 meses de calendario), no como prediccion.
- **Hallazgo de paso**: al probar el resumen diario contra la copia aparecio una instancia viva en el puerto 8001 (PID 5744, desde las 09:50, cadena `cmd -> pythonw -> python` de la corrida FALLIDA de la prueba de integracion de start/stop de esta manana, con una copia temporal de pytest y sin etiquetado). Era mia (se filtro antes de arreglar `stop_bot.ps1`) y la detuve con `scripts/stop_bot.ps1 -Port 8001` (cadena de 3 reconocida y confirmada). El bot real del 8000 (PID 8136, 09:32:47) ni se toco; solo lo mire en modo lectura.
- Verificacion: pytest 949 passed / 4 skipped (antes 925), ruff limpio, Vitest 18/18 (antes 16), `tsc --noEmit` + `vite build` limpios.

## Subfase 3.9: brazo ejecutado de la cuenta paper (2026-10-17)

Plan aprobado por ti (Plan Mode) el mismo dia. **100 % paper**: solo `PaperBackend`; ninguna ruta a `BitunixBackend` (prueba por AST sobre todo `app/`). **El codigo esta en `master`, pero el bot real (8000) sigue corriendo SIN el brazo hasta que lo reinicies tu**; yo no lo toque (solo lo mire en modo lectura). Detalle y plan de despliegue: `docs/FASE3_9_BRAZO_EJECUTADO.md`.

- **Que hace** (`app/trading/paper_execution.py`, `PaperExecutionArm`): despues de que el labeler persiste la etiqueta, abre en la cuenta paper solo las `APROBADA` con `decision_delay_s <= 60 s` (`real_open_allowed`), via `PaperBackend.open_position(decision_source='LLM_APROBADA')`, o sea con el motor de riesgo de siempre (kill switch, drawdown, circuit breaker, perdida diaria, datos obsoletos, elegibilidad, cupos, tope de SL). Cada decision APROBADA considerada deja una fila en la tabla nueva `paper_execution_log` (`signal_group_key` unica = idempotente): `OPENED` con `trade_id` y detalles (retraso de entrada, precio de la sombra vs ejecutado, diferencia en pb) o `SKIPPED` con motivo consultable: `LATE_DECISION`, `SCHEDULER_PAUSED`, `POSITION_ALREADY_OPEN`, `NO_CONTRACT_SPEC`, `BELOW_MIN_ORDER`, `RISK_<codigo>`, `LEVELS_INVALID`, `OPEN_ERROR`, `ABORTED_RESTART`. Al activarse fija una sola vez `real_arm_started_at` y `real_arm_drawdown_baseline` (las claves que ya lee el reporte 3.8).
- **Interruptor propio** `PAPER_EXECUTION_ENABLED` (default false); `start_bot.ps1` lo pone en true solo con el puerto 8000 (queda en `medicion.log`), `run_dev_server.py` lo fuerza a false. Distinto de `AUTO_OPEN_WITHOUT_LLM` (sigue en false; si ambos fueran true el brazo se niega con CRITICAL). Divergencia documentada con la seccion (g) de `FASE3_6_LLM.md`.
- **Aislamiento de la medicion** (tests): la etiqueta esta persistida antes de que el brazo la vea; el brazo corre en su propia tarea y un fallo o cuelgue suyo no afecta al labeler ni a `Scheduler.run_cycle`; `shutdown` lo cancela; **la medicion es identica con el brazo encendido y apagado** (toda la fila de cada sombra, `llm_logs` y el avance); el brazo solo escribe en `paper_execution_log`, `trades`, `system_state` y `risk_rejections` (nunca `shadow_trades`/`llm_logs`: por eso `executed_in_real_account` queda en 0 y el vinculo vive en el log). No se toco `app/llm/prompts.py` ni `FROZEN_PROMPT_SHA256` (el diff del labeler es solo el gancho; un test fija el hash literal y que `PROMPT_SHA256` lo iguala).
- **Regla de tamano minimo (decision tomada, anotada)**: omitir y registrar, sin cambiar `DEFAULT_MARGIN_USDT`. La qty se redondea hacia abajo a `basePrecision` (como el exchange) y se compara con `minTradeVolume`; es mas estricto que el chequeo de `PaperBackend`, que no redondea. Con las specs reales de la copia, `TRXUSDT` a 0,336 da 148,8 -> 148 < 150: `BELOW_MIN_ORDER`. Alternativa para decidir con datos (no implementada): subir el margen lo justo con un tope (TRX ~5,03 USDT). No se redondea la qty del trade (cambiaria `compute_open_fill`, compartido con la sombra).
- **Resultados ocultos hasta el registro oficial** (tu decision de hoy): `GET /trades` y `GET /positions` (sin login) ya no usan `response_model=list[Trade]`, devuelven una lista blanca (sin PnL, costos, trailing ni motivo de cierre sin `is_outcome_reveal_allowed`); Telegram `/status` sin balance ni equity, `/balance` solo capital inicial y posiciones, `/positions` sin PnL flotante, `/metrics` solo el conteo de cerradas. **Residual**: los logs INFO (`Monitor cerro ... motivo=`, `Posicion cerrada ... pnl_neto=`) siguen mostrando motivo y PnL (ya pasaba con las sombras; no se tocan).
- **Visibilidad sin PnL**: `/health.paper_execution`, linea del brazo en `/status`, `scripts/paper_execution_status.py --db <copia>` (conteos por resultado y motivo, calidad de la entrada, no ejecutables, ultimas decisiones) y `real_money_report.py` (con el reveal permitido) ahora muestra omitidas por motivo y entrada ejecutada vs sombra, que decia "requiere 3.9".
- **Cambios en archivos existentes (minimos)**: `shadow_live_labeler.py` (+parametro `on_decision` y `_dispatch_decision`), `scheduler.py` (pasa el candidato), `main.py` (construye y activa el brazo; una excepcion al activar jamas impide arrancar), `database.py` (+tabla, aditiva), `health.py`, `deps.py`, `commands.py`, `serializers.py`, `trades.py`, `positions.py`, `config.py`, `start_bot.ps1`, `run_dev_server.py`, `.env.example`. Se actualizaron 3 dobles de labeler de tests (`maybe_label(trade, candidate=None)`) y los tests de Telegram que afirmaban balance/PnL.
- **Pruebas con datos reales** (regla de CLAUDE.md): (a) copia fresca de `data/minerva_estabilidad.db` (origen `mode=ro`): `Database(copia).connect()` dos veces solo agrega `paper_execution_log`, ninguna otra tabla cambia y `PRAGMA integrity_check` = ok (tambien como test); (b) humo del brazo sobre otra copia, con REST falso y las specs/precios reales de la cache: BTC (elegible) `OPENED`, TRX `BELOW_MIN_ORDER`, ETH con donchian `RISK_STRATEGY_NOT_ELIGIBLE`, SOL con decision a 74,1 s `LATE_DECISION`; solo cambiaron `trades` (+1), `system_state` (+2 claves), `risk_rejections` (+1) y la tabla nueva; `paper_execution_status.py` y `real_money_report.py` corren sin errores sobre esa copia (el segundo sigue respondiendo gate cerrado / falta el registro, sin mostrar nada); (c) arranque REAL en el 8001 con `start_bot.ps1` sobre una copia temporal (prueba de integracion ampliada): `/health.paper_execution.enabled=false`, el diario dice `PAPER_EXECUTION_ENABLED=false`, parada confirmada de la cadena de 3 procesos y puerto libre.
- **Despliegue (lo haces tu, un solo reinicio)**: en :10-:40 de una hora que no sea de cierre de 4 h (hora de Chile, UTC-3: 10, 11, 12, 14, 15, 16, 18, 19, 20 h); todas las filas de medicion caen en :00/:01 y las velas de 4 h cierran a las 00/04/08/12/16/20 UTC. Verificacion y criterios de reversa en el doc. Estimacion (orden de magnitud, muestra chica): 2-3 aperturas por dia en marcha; 100 cerradas ~ 35-50 dias en marcha.
- Verificacion: pytest 1051 passed / 4 skipped (antes 949), ruff limpio; frontend sin cambios. Commits pequenos y push al terminar (regla de Autonomia vigente).

## Fase 4: noticias y memoria de lecciones -- PLAN (2026-10-17)

Plan completo en `docs/FASE4_PLAN.md` (escrito antes de implementar, como pediste; en esta fase no uso Plan Mode porque lo ordenaste asi). Reglas por la medicion en curso: **todo apagado por defecto con su propio interruptor** (`NEWS_COLLECTION_ENABLED`, `LESSONS_MEMORY_ENABLED`) y **nada entra al prompt congelado** (`prompts.py`, `FROZEN_PROMPT_SHA256 = 0fa0dd75...` intactos; lo nuevo va a una version aparte `signal_review_v5` / `features_v3` preparada y sin usar, con tests que fijan el hash, el texto y el grafo de imports). La memoria solo usa operaciones cerradas y nunca muestra PnL antes de `is_outcome_reveal_allowed`; la recoleccion de noticias es una tarea aislada, determinista y sin costo de Anthropic.

- **Fuentes verificadas (2026-10-17)**: INTEGRADAS **GDELT DOC 2.0** (licencia "unlimited and unrestricted use ... commercial" con cita al proyecto; sin key; limite real "one every 5 seconds"), **Fed, comunicados de politica monetaria** (RSS, dominio publico citando a la Junta; trae BOM) y **Alternative.me Fear & Greed** (JSON; atribucion visible junto al dato). EXCLUIDAS: **Cointelegraph** (sus terminos prohiben el scraping automatizado y el uso para sistemas de IA/LLM sin consentimiento escrito), **CoinDesk** (su robots.txt bloquea a ClaudeBot/anthropic-ai; terminos 404) y **Decrypt** (prohibe robots que minen el contenido; solo no comercial). No integradas: CryptoPanic (key; terminos no verificados), SEC (exige identificar al bot), anuncios de Bitunix (sin feed oficial verificado).
- **No pude verificar**: el formato real de los articulos de GDELT y su limite efectivo desde este PC (respondio 429 en 6 de 7 llamadas, incluso con 20 s de pausa: el limite por IP es mas estricto que el documentado), la politica de acceso automatizado de la Fed, el limite de Alternative.me, los terminos de CoinDesk (404) y los de CryptoPanic. Lo que depende de GDELT se escribe defensivo y con degradacion (`news_context = null` con motivo).
- **Decision tomada (anotada)**: `start_bot.ps1` activara `NEWS_COLLECTION_ENABLED` solo con el puerto 8000 (gratis, aislada, y el historial de noticias no se puede reconstruir despues); `LESSONS_MEMORY_ENABLED` no se activa en ningun puerto. Para dejar tambien las noticias apagadas en el bot real basta ponerlo en `false` en `start_bot.ps1`.
- Subfases 4.1-4.7 y pruebas: ver el plan. Se registra el avance de cada una mas abajo.

## Fase 4: noticias y memoria de lecciones -- IMPLEMENTADA (2026-10-18)

Subfases 4.1 a 4.7 hechas y en `master` (commits `0d0218c`, `4a9a368`, `61adc75`, `cc0399a`, `a326aa7`, `8d0ebcf`, `0d2505e`, `e5364a1`, `e6e87c7`, `8c0d62e` y el de documentacion). **Todo apagado por defecto y nada conectado al prompt de la medicion.** Detalle, tabla de fuentes y procedimiento de activacion en `docs/FASE4_PLAN.md`. **El bot real (8000) sigue sin esta fase hasta que lo reinicies tu**; yo no lo toque.

- **4.1 Base**: `NEWS_COLLECTION_ENABLED`, `LESSONS_MEMORY_ENABLED` (+ `NEWS_SOURCES`, `NEWS_RETENTION_DAYS`, `LESSONS_MIN_SAMPLE`...) en `Settings` y `.env.example`; `start_bot.ps1` enciende solo las noticias y solo con el puerto 8000, `run_dev_server.py` fuerza ambos a false; 7 tablas aditivas (`news_items`, `news_fetch_state`, `fear_greed`, `memory_entries`, `lessons`, `lesson_applications`, `lessons_reports`) y sus repositorios.
- **4.2-4.3 Noticias** (`app/news/`): saneado de titulares (texto no confiable), URL canonica, hashes, casi duplicados, activo afectado SOLO desde el titular, sentimiento por lexico determinista (`LEXICON_V1`, sin LLM ni costo), parsers de GDELT / RSS de la Fed (rechaza DTD y entidades, tolera BOM) / Fear & Greed, cliente HTTP (sin redirecciones, tope de tamano), colector con intervalo minimo por fuente, GET condicional, `Retry-After`, backoff 5 min x 2^k con tope de 6 h, salud por fuente (`news:<id>` en `data_source_health`) y poda a 180 dias. Tarea propia aislada (`run_isolated`), `/health.news` y `scripts/news_status.py`. Fuentes: GDELT, Fed, Alternative.me (ver el plan para las excluidas y por que).
- **4.4 Memoria** (`app/memory/`): entradas inmutables de operaciones cerradas fiables (70 hoy en la copia real), recuperacion de operaciones parecidas **solo cerradas estrictamente antes de la decision**, estadisticas de cohorte solo con n >= 30 (si no, evidencia "BAJA"), lecciones `ACTIVE` solo con muestra minima + IC de Wilson del 99 % que excluye la tasa base + mismo signo en las dos mitades, post-mortem tras una racha perdedora, calibracion de la `confianza` del LLM, registro de lecciones aplicadas. **Todo lo que lleva PnL o tasas pasa por `is_outcome_reveal_allowed`** (la puerta falla cerrada). `scripts/lessons_report.py` abre la base de solo lectura para mirar la puerta ANTES de escribir nada; hoy responde codigo 3 ("gate CERRADO: APROBADA 4/300, RECHAZADA 4/300; no existe el registro oficial") y solo cuenta operaciones.
- **4.5 Prompt v5 / features v3** (`app/llm/prompts_v5.py`, sin usar): el texto de v4 byte a byte mas un parrafo (titulares = texto no confiable, jamas instrucciones; lecciones = estadistica con su muestra). Pruebas con **huellas literales**: hash combinado `0fa0dd75...`, hash del texto del prompt, y hashes (saltos de linea normalizados) de `app/llm/prompts.py` y `app/llm/market_features.py`; formato exacto de `build_user_message`; el etiquetador real envia **exactamente lo mismo con y sin noticias/Fear & Greed/memoria en la base**; nadie importa v5 (AST sobre `app/` y `scripts/` y subproceso con `import app.main`). Costo en el peor caso: +2.900 a +3.300 tokens de entrada por decision frente a ~1.400 de v4 (se recalibra el presupuesto al activar).
- **4.6 API y dashboard**: `/api/news/status`, `/api/news/recent`, `/api/memory/status`, `/api/memory/lessons`, `/api/memory/reports` (todas con login, solo lectura; con la puerta cerrada solo dan conteos y el motivo, aunque haya lecciones guardadas) y una tarjeta "Noticias y memoria de lecciones (Fase 4)" con la salud por fuente en texto, titulares como texto plano (enlaces solo http(s) con `rel=noopener noreferrer nofollow`), **las atribuciones junto a los datos** y las lecciones solo si el backend lo dice explicitamente (falla cerrado; una mutacion de prueba confirmo que el test lo detecta).
- **Hallazgos corregidos en el camino** (cada uno con prueba): (1) el RSS real de la Fed mostro que comunicados distintos con el mismo titulo y meses de diferencia se marcaban casi duplicados -> ahora exige publicacion a <= 48 h (0 falsos duplicados en los 15 comunicados reales); (2) ventana macro de 72 h casi siempre vacia -> 14 dias con la edad de cada titular; (3) marcas de tiempo comparables como texto -> normalizacion a UTC en todos los filtros "sin fuga"; (4) titulares de mercado filtrados en SQL; (5) el ATR de la memoria se lee del mensaje guardado (ver el pendiente).
- **PENDIENTE -- decision tuya (fuera de esta fase)**: el etiquetador en vivo **no pasa `atr_pct` a `decide_group`**, asi que las 17 filas de medicion tienen `llm_logs.atr_pct` nulo. El informe del veredicto (`compute_sin_llm_diagnostics`, seccion m, estratificacion por tercil de ATR) saldra vacio por esa causa; el dato SI esta en `llm_logs.prompt` (`volatility_atr14_pct`). Opciones: (a) que `ai_value_report` lo lea de ahi cuando la columna es nula (solo lectura, cubre filas pasadas y futuras, no toca la medicion); (b) que el etiquetador lo pase al registrar (cambia lo que se escribe desde el proximo reinicio). Recomiendo (a). No toque ninguna de las dos porque tocan el pipeline o el codigo del veredicto.
- **Verificacion**: pytest 1333 passed / 4 skipped (antes 1051), ruff limpio, Vitest 29/29 (antes 18), `tsc --noEmit` + `vite build` limpios. **Humo con copias frescas de la base real** (backup en solo lectura, ruta distinta de `settings.database_path` verificada): `connect()` solo crea las 7 tablas nuevas, `shadow_trades` (91), `llm_logs` (17), `signals`, `trades` (0) y `system_state` quedan identicas, `integrity_check` ok; la memoria construye 70 entradas en 0,2 s (idempotente), 47 filas de lecciones, 14 entradas con ATR recuperado; `lessons_report.py` y `news_status.py` corren sin errores. **Humo de punta a punta con red real** (base temporal): Fed 15 titulares y Fear & Greed 2 puntos llegan a la base, el contexto de noticias sale `OK` y las rutas de la API responden con atribuciones; **GDELT respondio 429 desde este PC** (registrado en la salud de la fuente, con backoff; el resto sigue).
- **No pude verificar**: formato real de los articulos de GDELT y su limite efectivo desde este PC (429 persistente); politica de acceso automatizado de la Fed; limite de Alternative.me; terminos de CoinDesk (404) y de CryptoPanic; feed de anuncios de Bitunix.
- **Para activarlo**: un solo reinicio en :10-:40 de una hora que no sea de cierre de 4 h (como la 3.9); `NEWS_COLLECTION_ENABLED` se enciende solo con el puerto 8000 (para dejarlo apagado: `$newsCollection = "false"` en `start_bot.ps1`); la memoria de lecciones y el prompt v5 NO se activan sin ti y sin el registro oficial del veredicto.

## Revision de b62d261: Fase 4 aprobada y sus tres decisiones (2026-10-18)

- **(1) ATR del veredicto -- hecho**: `ai_value_report._load_log_metas` ya recupera `volatility_atr14_pct` del mensaje guardado en `llm_logs.prompt` cuando `atr_pct` es nulo (la columna manda si esta). **Solo lectura**: no cambia lo que se escribe, ni el prompt congelado, ni las etiquetas, ni `measurement_keys`, ni el veredicto (una prueba compara el informe COMPLETO con el ATR en la columna y en el prompt: texto, diagnosticos y veredicto idénticos, salvo que antes faltaba la seccion por tercil). Helper puro y compartido `app/persistence/llm_log_fields.py` (finito y no negativo, nunca lanza); `app/memory/entries.py` lo reutiliza. Solo se trae el prompt de las filas con la columna nula. Con una copia fresca de la base real: 22 de 22 filas recuperan su ATR (0,155 a 2,097 %), la seccion m tiene sus tres estratos y `scripts/ai_value_report.py` sigue con el gate CERRADO (5/300 y 6/300) sin mostrar nada. Una mutacion de prueba (volver a leer solo la columna) hace fallar 2 pruebas.
- **(2) Noticias encendidas en el bot real**: confirmado, queda como esta (`start_bot.ps1` las enciende con el puerto 8000); el reinicio lo haces tu.
- **(3) Copias del scratchpad**: las borras tu (el `rm` fue denegado por permisos; no busque rodeos).

## Fase 5: dashboard completo y Telegram enriquecido -- PLAN (2026-10-18)

Plan completo en `docs/FASE5_PLAN.md` (escrito antes de implementar, como pediste). Reglas de tu revision de b62d261: **nada toca el prompt congelado, las sombras, las etiquetas ni `measurement_keys`**; la cuenta paper **no muestra PnL, precio de salida ni motivo de cierre mientras `is_outcome_reveal_allowed` sea falso** (la puerta falla cerrada); los controles del dashboard (pausar, reanudar, kill switch) llevan **confirmacion explicita, la misma advertencia que Telegram y registro de quien/cuando**; el push de Telegram es **aislado, con interruptor propio y encendido solo en el puerto 8000**.

- **Verificado en la fuente oficial (2026-10-18)**: la guia de atribucion de CoinGecko acepta literalmente "Data provided by CoinGecko" con enlace a coingecko.com y colocacion visible cerca de los datos (los terminos de la API piden letra legible de al menos 10 px): va bajo la tabla del universo y en el pie. Telegram: maximo ~1 mensaje por segundo por chat, errores 429 con `retry_after`, texto de 1 a 4096 caracteres: el notificador separa los mensajes 1,2 s y respeta `retry_after`. **No pude verificar** que tu chat real reciba los mensajes (necesita el token de `.env`, que no leo): el notificador manda un mensaje de arranque para que lo compruebes.
- **Decisiones de diseno anotadas**: (1) el push **sondea la base** y compara con el ultimo estado notificado en vez de engancharse al nucleo (cero cambios en `PaperBackend`, monitor, brazo o motor de riesgo; robusto a cierres retroactivos de la reconciliacion; al primer arranque toma el estado actual como linea base y NO notifica el historial); (2) `closed_at` si se muestra con el reveal cerrado porque pediste "horas"; los detalles del registro del brazo pasan por una lista blanca porque `MARGIN_UNAVAILABLE` guarda el `equity`; (3) los POST de control exigen login, mismo origen, JSON y la palabra de confirmacion exacta, y se apagan con `DASHBOARD_CONTROLS_ENABLED=false`; (4) sin cierre manual de posiciones ni edicion de parametros de riesgo (no pedidos) y sin graficos de equity (son resultados; para despues del veredicto).
- **Subfases**: 5.1 base (ajustes, tabla `control_audit`, puerta y acciones de control compartidas), 5.2 API de lectura, 5.3 API de controles, 5.4 notificador push, 5.5 frontend, 5.6 documentacion, verificacion y push. El avance de cada una se registra mas abajo.
- **Reinicio**: todo lo de backend necesita el reinicio del bot real (un solo reinicio en :10-:40 de una hora UTC que no sea de cierre de 4 h); el frontend compilado en `frontend/dist` lo sirve el 8000 en caliente, asi que las pestanas nuevas diran "no disponible" hasta ese reinicio. La lista definitiva se confirma al terminar.

## Fase 5: dashboard completo y Telegram enriquecido -- IMPLEMENTADA (2026-10-18)

Subfases 5.1 a 5.6 hechas y en `master` (commits `efa9e94`, `aab88f9`, `cebf301`, `c5c5111`, `a456961` y el de documentacion). Todo el alcance del plan, sin recortes; **no se toco el prompt congelado, las sombras, las etiquetas ni `measurement_keys`** (pruebas AST: las rutas de lectura no importan `anthropic`, los prompts, el cliente ni el servicio de decision del LLM, el etiquetador, `ShadowBook`, la ejecucion, noticias ni memoria -- solo `app.llm.budget` para la fecha local, que ya usan `/health` y Telegram --; el notificador no importa `app.llm`, el scheduler, el etiquetador, `ShadowBook`, el brazo, el monitor ni la ejecucion, ningun modulo del nucleo lo importa y solo escribe la clave `push_state`). Detalle, ajustes respecto al plan, verificacion y procedimiento de activacion en `docs/FASE5_PLAN.md` ("Implementacion"). **El bot real (8000) sigue sin el backend de esta fase hasta que lo reinicies tu; yo no lo toque.**

- **5.1 Base** (`efa9e94`): `TELEGRAM_PUSH_ENABLED` (apagado por defecto; `start_bot.ps1` lo enciende solo con el puerto 8000, `run_dev_server.py` lo fuerza a false), `TELEGRAM_PUSH_POLL_SECONDS`, `DASHBOARD_CONTROLS_ENABLED` (encendido por defecto); tabla aditiva `control_audit`; puerta compartida `app/trading/reveal.py::reveal_allowed` (**falla cerrada** ante cualquier error); `app/trading/control_actions.py` (pausar/reanudar/kill con candado, auditoria y la misma advertencia `STOPS_MEASUREMENT` que Telegram, que ahora usa ese codigo).
- **5.2 API de lectura** (`aab88f9`): `/api/account`, `/api/arm`, `/api/risk`, `/api/universe`, `/api/backtest/verdicts`, todas con login. **Con la puerta cerrada no sale PnL, precio de salida, motivo de cierre, comisiones, equity ni saldo**: lista blanca clave por clave (una columna nueva no sale por olvido), los detalles del brazo por `ARM_DETAIL_KEYS` (el rechazo por margen guarda el `equity`), el PnL ni siquiera se consulta. `closed_at` si se ve (pediste "horas").
- **5.3 Controles** (`cebf301`): `POST /api/control/{pause|resume|kill}` con login, controles habilitados (403), mismo origen por esquema+host y `Sec-Fetch-Site` (403), solo JSON (415) y la palabra exacta `PAUSAR`/`REANUDAR`/`KILL` (422), como dependencias de la ruta (FastAPI valida el cuerpo antes del endpoint). Cada peticion valida queda en `control_audit` (canal, quien, antes/despues), tambien si no cambio nada; `GET /api/control/info` da las advertencias y `GET /api/control/audit` el registro. Prueba por OpenAPI: las unicas rutas de escritura de la API son login, logout y estos tres.
- **5.4 Push de Telegram** (`c5c5111`): sondea la base cada 10 s y compara con `system_state.push_state` (cero ganchos en el nucleo); avisa posicion abierta, posicion cerrada (**sin PnL ni motivo mientras el reveal este cerrado**: solo "posicion cerrada"), cambio de universo y alertas de riesgo (kill switch, pausa, breaker, stop por drawdown, perdida diaria) en ambos sentidos. Linea base al primer arranque sin notificar el historial; 1,2 s entre mensajes, `retry_after`, 3 intentos, descarte de errores permanentes, maximo 10 por sondeo; mensaje de arranque para que compruebes el canal; `/health.telegram_push` sin token. Tarea aislada: un Telegram hostil no frena al scheduler, el etiquetado ni el monitor (prueba).
- **5.5 Frontend** (`a456961`): pestanas Resumen (todo lo anterior + estado del push), Cuenta paper, Brazo ejecutado, Riesgo y controles (tres botones con dialogo accesible: advertencia del servidor tal cual, hay que escribir la palabra, el servidor la vuelve a exigir, muestra "por quien y cuando" y el registro), Universo y backtest; banner "LA MEDICION ESTA DETENIDA" en todas las pestanas; atribucion "Data provided by CoinGecko" enlazada bajo la tabla del universo y en el pie. Falla cerrado tambien en el navegador. Las pestanas piden sus datos solo al abrirse y, sin backend actualizado, dicen "no disponible todavia".
- **Hallazgo en el camino**: `/health.control` existe desde la 3.7, asi que el banner de pausa/kill **ya funciona con el bot actual sin reiniciar** (lectura de solo lectura al 8000); las noticias de la Fase 4 ya estan activas ahi (`/health.news` presente). Corregidos con prueba: el origen se comparaba sin el esquema, un `text/plain` daba 422 en vez de 415, empates de marca de tiempo en Windows (desempate `id DESC`), la prueba AST de las rutas de lectura prohibia todo `app.llm` aunque `risk.py` solo usa `app.llm.budget.local_day_start_utc` (se afino la lista), y un import de un modulo inexistente (`models_side`, reemplazado por `Side`).
- **Verificacion**: pytest 1552 passed / 4 skipped (antes 1359: las 193 de diferencia son las pruebas nuevas), ruff limpio, Vitest 106/106 (antes 29), `tsc --noEmit` + `vite build` limpios. **Humo con copias frescas de la base real** (backup en solo lectura, ruta distinta de `settings.database_path` verificada): `connect()` agrega solo `control_audit`, las otras 27 tablas con las mismas filas, `integrity_check` ok; las 5 rutas de lectura responden con la puerta REAL cerrada sin ningun campo de resultado (universo de 30 candidatos, 10 incluidos, 3 cambios; 6 veredictos de backtest; brazo con 2 omitidas y 0 abiertas); controles contra el esquema real (palabra mala 422, origen ajeno 403, pausar y reanudar auditados); con operaciones sinteticas centinela en la copia ningun valor de resultado sale. Humo del notificador sobre copia real con cliente falso: linea base silenciosa y un mensaje por evento sintetico. **Arranque real en el 8001** (`run_dev_server.py` sobre una copia): arranca completo, `/health.telegram_push.enabled` es false, `frozen_hash_ok` true, las rutas nuevas estan montadas y piden sesion (401) y las unicas rutas de escritura de la API son login, logout y `/api/control/{action}`; lo detuve al terminar y el 8000 siguio en `ok`.
- **No pude verificar**: que Telegram entregue en tu chat (necesita el token de `.env`, que no leo; el mensaje de arranque lo confirma a la primera); el aspecto visual real en un navegador (solo Vitest/jsdom, `tsc` y `vite build`); la mutacion de prueba del frontend (forzar `reveal = true` en `AccountTab`): el clasificador de permisos bloqueo el segundo cambio y la corrida, **revertí el primero al instante y lo verifique** (`reveal = data.reveal_allowed === true` intacto) y no lo reintente. Las mutaciones del backend (candado, serializador de cuenta, texto de cierre del push) si se hicieron y las pruebas las detectaron.
- **Riesgos residuales**: `closed_at` + niveles permiten inferir algo con muchas operaciones; un breaker activo implica una racha de perdidas aunque el aviso no traiga cifras; los logs INFO siguen mostrando motivo y PnL de cierre (ya ocurria); con los controles encendidos quien tenga sesion puede pausar la medicion (login + mismo origen + JSON + palabra + auditoria, servidor solo en 127.0.0.1; se apaga con `DASHBOARD_CONTROLS_ENABLED=false`).
- **Fuera de alcance (a proposito)**: cerrar posiciones y editar parametros de riesgo desde el dashboard, curvas de equity y PnL agrupado (son resultados), WebSocket, PWA.
- **Para activarlo**: un solo reinicio (`stop_bot.ps1` + `start_bot.ps1`) en :10-:40 de una hora UTC que no sea de cierre de 4 h (en Chile, UTC-3: 10, 11, 12, 14, 15, 16, 18, 19 o 20 h). Requiere reinicio **todo el backend**: rutas nuevas, tabla `control_audit`, notificador y `/health.telegram_push`, cambios de `/pause` `/resume` `/kill` de Telegram. No requiere reinicio: el frontend compilado (ya servido en caliente) ni el banner. Verificacion posterior y criterios de reversa en `docs/FASE5_PLAN.md`.
- **Para ti**: borrar las copias de la base real del scratchpad (`fase4_copy_a..e.db`, `fase5_copy_a..d.db`, ~160 MB cada una; el `rm` esta denegado por permisos y no busque rodeos). El gate de la medicion sigue cerrado (la puerta REAL respondio "cerrada" en el humo de hoy): las cifras de resultado seguiran ocultas en dashboard y Telegram hasta que se abra y exista el registro oficial (`docs/VEREDICTO_3_6.md`).

## Revision de 6be3d2f: Fase 5 aprobada (2026-10-18)

Aceptados los riesgos declarados de la Fase 5. Siguiente y ultima: Fase 6 (Docker, pruebas de punta a punta, informe final y limpieza).

## Fase 6: Docker, E2E, documentacion final y limpieza -- PLAN (2026-10-18)

Plan completo en `docs/FASE6_PLAN.md` (escrito antes de implementar). Reglas de tu revision: **Docker NO reemplaza `start_bot.ps1`** (el bot real sigue en el 8000 hasta la migracion al PC 24/7); no se toca el prompt congelado, la medicion ni el bot del 8000; las pruebas usan base temporal, el puerto 8001 y todo simulado (cero gasto).

- **Estado verificado**: Docker CLI 29.6.2 y Compose v5.3.1 instalados, **demonio apagado** (Docker Desktop). No lo enciendo por mi cuenta (servicio pesado en el PC del bot real); valido todo lo que no necesita demonio y te digo que falta probar.
- **Hallazgos que condicionan el diseno**: `app/main.py` y `verdict_record.py` resuelven `frontend/dist` y `docs/` relativo al paquete (la imagen se ejecuta desde un arbol `/app`, no instala `app` en site-packages); `/health` da `stale` tambien con una pausa deliberada (el healthcheck la acepta si `control.paused`); el esqueleto de la Fase 1 corre como root y sin frontend ni healthcheck; sigue pendiente el certificado raiz de inspeccion TLS en el contenedor (nota de la Fase 1).
- **Decisiones**: volumenes con nombre (SQLite WAL no es fiable sobre carpetas compartidas de Docker Desktop, y asi `docker compose up` aqui nunca abre la base real de `data/`); `restart: no` por defecto (en el PC 24/7 `unless-stopped`); puerto 8000 por defecto en loopback (si alguien lo levanta por error, choca con el bot real antes de arrancar el proceso); versiones exactas en `docker/requirements.lock` tomadas del conjunto que paso 1.552 pruebas; `MINERVA_REAL_BOT=false` apaga los 5 interruptores que `start_bot.ps1` enciende solo con el 8000.
- **Subfases**: 6.1 Docker, 6.2 arnes E2E y ciclo completo, 6.3 E2E de trailing / liquidacion / breaker / pausa y kill / reinicio / resultados ocultos, 6.4 documentacion (README, MANUAL_OPERACION, INFORME_FINAL, ESQUEMA_BD) y scripts de respaldo y restauracion, 6.5 limpieza, 6.6 verificacion y push. El avance de cada una se registra mas abajo.

## Fase 6: Docker, E2E, documentacion final y limpieza -- IMPLEMENTADA (2026-10-18)

Subfases 6.1 a 6.6 hechas y en `master` (commits `eebf2ad` plan, `b9bfa11`, `d7f91bf`, `6877449`, `85ee8e9`, `2092d23`, `37820f2`, `713d377`, `4ce4456` y el de la documentacion final). Todo el alcance pedido, mas tres adiciones nacidas de hallazgos. Detalle, tablas y el procedimiento para probar Docker en `docs/FASE6_PLAN.md` ("Implementacion"); informe final en `docs/INFORME_FINAL.md`; operacion en `docs/MANUAL_OPERACION.md`. **No se toco el prompt congelado, la medicion ni el bot del 8000** (solo comprobe que seguia escuchando). **Docker Desktop estaba apagado y no lo encendi.**

- **6.1 Docker**: `Dockerfile` multietapa (frontend con Node 24, dependencias con versiones EXACTAS de `docker/requirements.lock` instaladas con `--only-binary`, runtime sin privilegios uid 10001), `.dockerignore` (sin `.env`, `data/`, `logs/`, `tests/`, `docs/`), `docker-compose.yml` (puerto solo en `127.0.0.1`, volumenes con nombre `minerva_data` y `minerva_logs`, `./docs` solo lectura, `cap_drop: ALL`, `no-new-privileges`, `restart: no` por defecto, los MISMOS interruptores que `start_bot.ps1` con `MINERVA_REAL_BOT=false` para una instancia de prueba, healthcheck contra `/health` que acepta una pausa deliberada y rechaza `frozen_hash_ok` falso), certificados raiz opcionales en `docker/certs/`. No reemplaza `start_bot.ps1`. 90 pruebas estaticas, `docker compose config`, resolucion de dependencias para Linux y humo del arbol de la imagen.
- **6.2-6.3 Pruebas de punta a punta** (`tests/e2e/`, 51 pruebas, ~18 s): el sistema real sobre una base temporal y solo el exterior simulado (Anthropic, Bitunix REST y WebSocket, Telegram) con un candado de red: ciclo de vela 4 h LONG y SHORT, TP, SL, trailing, liquidacion, circuit breaker con post-mortem, pausa y kill desde Telegram y dashboard, reinicio con reconciliacion y barrido de que con la puerta cerrada ningun canal muestra resultados (con prueba de sensibilidad: con la puerta abierta el mismo barrido SI los encuentra). **No encontraron fallos del producto**; los problemas estaban en el arnes.
- **6.4 Herramientas y documentacion**: `scripts/backup_db.py` y `scripts/restore_db.py` (consistentes, verificados, reversibles), `scripts/resume_drawdown_stop.py`, `scripts/export_schema_doc.py` (genera `docs/ESQUEMA_BD.md`: 28 tablas, 13 indices explicitos, 1 disparador; una prueba falla si se desincroniza), README reescrito, `docs/MANUAL_OPERACION.md`, `docs/INFORME_FINAL.md` y un aviso en `docs/MIGRACION_PC.md`.
- **6.5 Limpieza**: 7 campos de `Settings` sin ninguna referencia retirados (`TRADING_SYMBOLS`/`symbols`, `KLINE_INTERVAL`, `STRATEGY_HISTORY_BARS`, `ACTIVE_STRATEGY`, `MARGIN_MODE`, `ANTHROPIC_HAIKU_MODEL`, `CONFLUENCE_SCORE_THRESHOLD`) y 3 funciones sin uso (`get_strategy`, `count_distinct_combos`, `get_covered_funding_times`); notas obsoletas corregidas; sin TODO/FIXME en el codigo. **`.env.example` no tenia 31 variables** de `Settings`: agregadas, con prueba de que estan todas, las credenciales vacias y la plantilla dando exactamente los defaults del codigo. Prueba de higiene sobre los archivos que git versionaria (patrones de claves de Anthropic, tokens de Telegram, hashes bcrypt, claves privadas, tokens de GitHub/AWS, asignaciones a variables de credencial) y de que `.gitignore` cubre lo que nunca debe subirse (reforzado: `.env.*`, `logs/`, `*.sqlite*`). Revision unica del historial (solo conteos): 0 coincidencias y 0 archivos prohibidos entre los 374 que alguna vez se agregaron.
- **Hallazgos**: (a) el stop por drawdown duro era irreversible en la practica: los avisos decian "se levanta aparte (`resume_drawdown_stop`)" y no habia CLI, API ni comando -> `scripts/resume_drawdown_stop.py` (informativo por defecto, exige `--confirm`, no imprime el equity con el reveal cerrado, no reinicia el contador del criterio de dinero real); (b) el hash congelado cubre el prompt y las features pero NO el modelo: un cambio de `ANTHROPIC_SONNET_MODEL` contaminaria la medicion sin avisar -> `scripts/measurement_identity.py` (solo lectura, solo conteos y fechas; sobre la copia real: una sola combinacion `claude-sonnet-5-5` / `signal_review_v4` / `features_v2` / hash congelado, 28 filas de MEDICION); una guarda automatica dentro del bot queda para despues del veredicto porque tocaria el etiquetador; (c) `/health` da `stale` tambien con una pausa deliberada: el healthcheck de Docker lo acepta si `control.paused`; (d) el push no puede anunciar una pausa seguida de una reanudacion dentro del mismo sondeo (compara estados; la auditoria si las registra); (e) las rutas relativas al paquete dictan el diseno de la imagen; (f) las redes con inspeccion TLS exigen el certificado raiz tambien en el contenedor; (g) vulture no ve decoradores ni nombres repetidos (`symbols`): cada hallazgo se contrasto a mano.
- **Decisiones (Autonomia)**: `.env.example` = todas las variables de `Settings`, credenciales vacias y el resto igual al codigo; se conservo lo que es interfaz exigida por la especificacion (`BitunixBackend`, `maker_fee_pct`) o vocabulario documentado (`PILOTO`, `FILL_REST`, `VALID_VERDICTS`); los documentos historicos (`PROGRESS.md`, `FASE*_PLAN.md`) no se reescriben y las pruebas de documentacion solo revisan los vivos; los respaldos de la base viven en `data/backups/` (dentro de `data/`, no versionado) y el manual exige guardar uno semanal fuera del PC.
- **Verificacion**: pytest 1796 passed / 4 skipped en 5 min 24 s (antes 1552; 1800 recogidas: 1745 unitarias, 4 de integracion con red real y 51 E2E); `ruff check .` limpio; Vitest 106/106; `tsc --noEmit` + `vite build` limpios; `export_schema_doc.py --check` y `export_docker_requirements.py --check` al dia. **Humos con copias frescas de la base real** (ruta distinta verificada): respaldo y restauracion con las mismas filas en las 28 tablas y negativa a pisar un destino (codigo 5); `resume_drawdown_stop.py` (informativo sin escribir, negativa con el stop inactivo, levantamiento sobre la copia con el contador intacto y sin imprimir el equity); `measurement_identity.py`. **Arranque real en el 8001** tras la limpieza (`run_dev_server.py` sobre una copia, sondeo de solo lectura, lo detuvo el script que lo lanzo): `frozen_hash_ok` verdadero, push/etiquetado/brazo apagados, 22 rutas con solo login/logout/`/api/control/{action}` de escritura, 401 sin sesion, frontend servido; el 8000 siguio escuchando.
- **No pude verificar**: `docker compose build`, que el contenedor arranque y quede `healthy`, la herencia de dueno del volumen, el montaje de `docs/` y el flujo de certificados (sin demonio); la entrega real de Telegram (necesita el token de `.env`, que no leo); el aspecto visual del dashboard en un navegador real; el formato real y el limite efectivo de GDELT (429 desde este PC) y los terminos de CoinDesk/CryptoPanic; todo lo de la ejecucion real en Bitunix (`docs/PASO_DINERO_REAL.md`); la revision a mano de un cierre por SL/TP/trailing con ticks reales (recordatorio de la 3.4; implica mirar resultados, asi que espera al registro oficial); la mutacion de prueba del frontend (la bloqueo el clasificador; revertida y comprobada, no reintentada). Procedimiento para probar Docker sin riesgo en `docs/FASE6_PLAN.md`.
- **Pendientes anteriores**: resueltos -> certificado raiz de inspeccion TLS en el contenedor (nota de la Fase 1), README de la Fase 1, nota "backtest vs paper trading del LLM" para el informe final, "Docker final multi-stage y E2E". Siguen abiertos (fuera de esta fase) -> el informe de metricas de paper trading (no producible con la medicion ciega y el gate cerrado: lo generaran `ai_value_report.py` y `real_money_report.py` tras el registro oficial), la revision a mano del primer cierre real por SL/TP/trailing (3.4), la verificacion visual del dashboard y el smoke manual de Telegram con el token real.
- **Requiere reinicio del bot real**: Docker, pruebas, documentacion y scripts nuevos, **no**. Los 9 archivos de `app/` que cambio la fase (docstrings y retirada de codigo sin uso) **se cargan con el reinicio ya pendiente de la Fase 5** y no cambian el comportamiento. Backend de la Fase 5: **un solo reinicio** (`stop_bot.ps1` + `start_bot.ps1`) en :10-:40 de una hora UTC que no sea de cierre de 4 h (en Chile, UTC-3: 10, 11, 12, 14, 15, 16, 18, 19 o 20 h).
- **Para ti**: borrar las copias de la base real del scratchpad (8 archivos de ~160 MB: `arm_copy`, `daily_verif`, `diag_gap`, `fase6_copy_a`, `real_money_copy`, `stop_test`, `uptime_copy`, `verdict_verif`, mas `smoke_backup_out/` y `smoke_restore_out/`) y `%TEMP%\minerva_dev_server_74_mau4o` (el `rm` esta denegado por permisos y no busque rodeos); probar Docker con el procedimiento de `docs/FASE6_PLAN.md`; un respaldo semanal fuera del PC. El gate de la medicion sigue cerrado (5/300 y 6/300 el 2026-10-18).

## Mudanza del bot real a Docker (tarea posterior a la Fase 6; en los documentos, "Fase 7") -- IMPLEMENTADA (2026-10-18)

Pedido: "mudar el bot real a Docker para que arranque con un solo comando", en dos etapas. **Etapa 1** (la ejecute yo, con prueba real): construir, levantar en el 8001 con una COPIA de la base en un volumen de prueba y verificar; **Etapa 2** (preparar, NO ejecutar): scripts de mudanza y de vuelta atras, scripts que lean la base desde Docker, `restart: unless-stopped`, documentacion de Docker Desktop con Windows y actualizacion de manual / MIGRACION_PC / README. Guia completa en `docs/MUDANZA_DOCKER.md`. **No se toco el bot del 8000 (mismo PID de principio a fin), el prompt congelado ni la medicion, y `app/` no cambio.** Una excepcion declarada mas abajo (dos respaldos de mas en `data/backups/`).

- **Etapa 1 (Docker Desktop estaba encendido)**: `docker compose build` (3 etapas, ~1 min en frio, imagen `minerva:local` de 503 MB), contenedor en el 8001 con una copia de la base real (API de backup, origen en solo lectura), con etiquetado / Telegram (comandos y push) / brazo / noticias APAGADOS y credenciales de mentira. Verificado: `healthy`, `/health` `status ok` en ~60 s, `frozen_hash_ok true`, dashboard y login, el volumen persiste tras `docker compose restart` (apagado ordenado ~4 s), proceso uid 10001 con `cap_drop: ALL` que solo escribe en `/app/data`, `/app/logs` y `/tmp`, conectividad a Bitunix, respaldo y `docker compose cp` con el servicio corriendo y detenido, resumen diario dentro del contenedor. Luego se bajo y se borro el volumen de prueba. **Encontro 5 defectos que las pruebas estaticas no veian**: (1) `restore_db.py` no abria un respaldo con WAL en la cabecera montado `:ro` -> los respaldos se guardan en modo clasico y `restore_db` lee tambien los antiguos (`immutable`); (2) con `env_file:` Compose interpolaba los `$` y mutilaba el hash bcrypt (56 caracteres en vez de 60; login 401) -> el `.env` se monta de solo lectura en `/app/.env`; (3) el codigo de la imagen era escribible por el proceso -> de root y solo lectura; (4) si falta el `.env`, Docker Desktop crea una CARPETA (`create_host_path: false` no se respeta) -> los scripts comprueban que sea un archivo; (5) Docker Desktop no inicia con Windows en este PC (`AutoStart` false y entrada deshabilitada en Inicio) -> documentado y avisado. Commits `9ca9c8d` y `707eea6`.
- **Etapa 2, scripts nuevos**: `migrate_to_docker.ps1` (ventana segura :10-:40 de una hora UTC no de cierre de 4 h, `-DryRun`, respaldo, build, tarea deshabilitada, parada con `stop_bot.ps1`, copia al volumen con la API de backup, verificacion de integridad y de las filas de las 28 tablas, marca, `up -d` en 127.0.0.1:8000 con los mismos interruptores que `start_bot.ps1`, espera de `/health`; cada paso que cambia algo registra su inverso ANTES de ejecutarse y un fallo o Ctrl+C los aplica en orden inverso; codigos 0/1/2/3), `rollback_from_docker.ps1` (el camino de vuelta; la base nativa vieja queda como `*.previo_*`), `docker_common.ps1`, `docker_db.py` (`backup` / `export` / `run <informe>`), `docker_support.py`, `db_fingerprint.py`, `docker/compose.vars`. **Cambios en lo existente**: marca `data/MIGRADO_A_DOCKER.txt` (la escribe la mudanza, la quita el rollback) que hace que `start_bot.ps1`, `backup_db.py`, `restore_db.py` y `resume_drawdown_stop.py` se nieguen a usar la base nativa vieja (con `--db` explicito si operan) y que `telegram_daily.py` saque su copia del contenedor; `restart: unless-stopped` por defecto; `hash_password.py` imprime el hash entre comillas simples. Docs: `docs/MUDANZA_DOCKER.md` (nueva), `docs/MANUAL_OPERACION.md` (rehecho en lo de Docker), `docs/MIGRACION_PC.md`, `README.md`, `docs/INFORME_FINAL.md`, nota en `docs/FASE6_PLAN.md`.
- **Hallazgos de la Etapa 2**: (a) un `docker compose ...` a secas escribe TROZOS DEL HASH de la contrasena en pantalla (Compose lee el `.env` de la raiz en cada comando y toma los `$` sin comillas simples por variables; reproducido con un `.env` de mentira) -> el hash va entre comillas simples (`hash_password.py`, `.env.example`), los scripts usan `--env-file docker/compose.vars` y la mudanza avisa (solo el NOMBRE de la variable) si el `.env` tiene un `$` sin comillas simples: **si generaste el hash con la version anterior, tu `.env` probablemente lo tiene sin comillas** (no lo abri); (b) ninguna prueba cubria la configuracion real (bot nativo y contenedor en el MISMO puerto 8000) -> pruebas nuevas y repeticion del ensayo real con el mismo puerto; (c) `Compare-Fingerprint` tenia una variable local que pisaba al parametro (PowerShell no distingue mayusculas): con dos tablas distintas la segunda se informaba mal -> corregido con prueba; (d) **en este PC solo existe la tarea `MinervaHealthAlert`: `MinervaStartBot` y `MinervaDailySummary` NO estan registradas** (el resumen de las 09:15 no se esta enviando); no cree ninguna; (e) un contenedor `unhealthy` no se reinicia solo (lo avisa `MinervaHealthAlert`); (f) la copia del volumen se hace con `restore_db.py` dentro de un contenedor de un solo uso y se verifica con la huella ANTES de levantar el bot.
- **Ensayo general con Docker real (aislado)**: copia fresca de la base real en el scratchpad; bot nativo de prueba con el `start_bot.ps1` real en el 8001 (interruptores apagados) y Docker en el 8002 y luego en el 8001, proyecto `minerva-rehearsal`, credenciales de mentira, tarea ficticia para probar `schtasks /Change`, hora fingida dentro de la ventana segura y envolturas de `stop_bot.ps1`/`start_bot.ps1` que solo aceptaban el 8001. Resultados (detalle en `docs/MUDANZA_DOCKER.md` §13): `-DryRun` ok; mudanza completa en 69 s (pausa de la medicion ~58 s) con las mismas filas en las 28 tablas; `start_bot.ps1` rechazado por la marca; `docker_db.py backup/export/run` (y `shadow_report.py` se nego a mostrar resultados: la puerta sigue cerrada por el camino de Docker); la restauracion del manual (§5.5) probada de verdad; vuelta atras completa; **fallo forzado** (espera de `/health` de 3 s) que se deshizo solo con codigo 2; reintento con `-ReplaceVolume`; **mismo puerto** (mudanza y vuelta atras con el contenedor detenido); y, desde un clon aislado del repo con la marca en su `data/`, `backup_db.py` / `restore_db.py` / `resume_drawdown_stop.py` / `start_bot.ps1` rechazados y `telegram_daily.py --dry-run` leyendo la base DEL CONTENEDOR (corriendo y detenido). Limpieza: bot de prueba detenido, proyecto bajado con `down -v`, tarea ficticia borrada; el 8000 siguio escuchando con el mismo PID.
- **Incidente declarado (mi error)**: dos veces lance `scripts/backup_db.py` sin `--db` creyendo que apuntaba a la carpeta del ensayo (la segunda, a un clon cuyo directorio de trabajo seguia siendo el repo real). `backup_db.py` resuelve sus rutas por defecto contra el directorio de trabajo, asi que creo `data/backups/minerva_20261019_002121.db` y `data/backups/minerva_20261019_021843.db` (~160 MB cada uno): **copias consistentes de la base viva** (API de backup con el origen en solo lectura, integridad verificada). No se modifico, movio ni borro ningun archivo existente de `data/` y el bot no se entero; `rm` esta denegado y la regla es no tocar `data/`, asi que **quedan ahi** (puedes borrarlos o conservarlos). `resume_drawdown_stop.py` (sin `--confirm`) y `telegram_daily.py --dry-run` leyeron tambien la base real (solo lectura / copia temporal) por la misma causa. Corregido el arnes (directorio de trabajo siempre dentro del scratchpad, clon aislado para las pruebas de rutas por defecto) y guardada la leccion en la memoria.
- **Decisiones (Autonomia)**: el hash va entre comillas simples en vez de cambiar la interpolacion de Compose (no se puede apagar la lectura del `.env` de la raiz desde el compose); las comprobaciones de la mudanza solo AVISAN del hash sin comillas y del autoarranque de Docker (el bot funciona igual) en vez de fallar; `-ReplaceVolume` guarda siempre una copia del volumen antes de borrarlo; si el contenedor llego a arrancar tras un fallo, el volumen se CONSERVA (tiene datos del bot); la marca vive junto a la base nativa y las herramientas la respetan salvo `--db` explicito; no cree ninguna tarea programada real; las rutas relativas por defecto de `backup_db.py` / `restore_db.py` se dejaron como estaban (se documenta correr desde la raiz del repositorio).
- **No pude verificar**: la mudanza real (puerto 8000, `.env` real, Telegram y Anthropic con credenciales de verdad; con ellos el contenedor puede tardar mas en llegar a `/health` sano, y si pasan los 300 s el script lo deshace); que el login funcione con tu contrasena real; que Docker Desktop inicie con Windows y el bot vuelva solo tras reiniciar el PC; suspension o hibernacion de Windows y el reloj de la VM de Docker; Ctrl+C a mitad de la mudanza (el `finally` no tiene prueba automatica); semanas de operacion continua; `schtasks` real salvo `/Change` sobre una tarea ficticia; una mudanza con ambos en el puerto 8000 contra Docker real (se probo con el 8001 y por pruebas automaticas).
- **Verificacion**: pytest **1921 passed / 4 skipped en 12 min 26 s** (1925 recogidas: 1870 unitarias, 4 de integracion con red real y 51 E2E; antes 1796 passed de 1800; +125 pruebas desde la Fase 6: 102 en archivos nuevos (22 de `docker_support`, 23 de `docker_db`/`db_fingerprint`, 12 de las protecciones de la marca y 45 de los scripts de mudanza y vuelta atras, que ejecutan PowerShell de verdad contra `docker`/`schtasks`/bot nativo falsos) y el resto en respaldo/restauracion en modo clasico, compose/Dockerfile, `hash_password` y documentacion); `ruff check .` limpio (63 avisos E501 en los archivos nuevos corregidos antes de commitear); Vitest 106/106; `tsc --noEmit` + `vite build` limpios; `export_schema_doc.py --check` y `export_docker_requirements.py --check` al dia; `git diff --stat cbccf5e HEAD -- app` vacio (**`app/` no cambio**). Mas el ensayo general con Docker real descrito arriba.
- **Requiere reinicio del bot real**: no por el codigo (los scripts se leen al ejecutarse y `app/` no cambio); **la mudanza en si ES el reinicio**: detiene el bot nativo y lo arranca en Docker con el codigo actual del arbol de trabajo, asi que SUSTITUYE al reinicio pendiente de la Fase 5 (hazla en lugar de, no ademas de).
- **Para ti**: (1) poner el hash del `.env` entre comillas simples (`AUTH_PASSWORD_HASH='...'`) y activar "Start Docker Desktop when you sign in"; (2) `migrate_to_docker.ps1 -DryRun` y luego el script real, dentro de la ventana segura, escribiendo MIGRAR (la vuelta atras es `rollback_from_docker.ps1`, escribiendo VOLVER); (3) registrar `MinervaDailySummary` y programar `docker_db.py backup` diario; (4) borrar a gusto los dos respaldos de mas de `data/backups/` y las copias de la base real del scratchpad (el `rm` esta denegado): `etapa1/`, `rehearsal/` (~3,6 GB con sus respaldos), `rehearsal_repo/`, `compose_default/`, `compose_check/` y las de tareas anteriores.

## Consolas de las pruebas y Programador de tareas real (2026-10-19, cierre 2026-10-22)

Pedido: pytest de `test_docker_migration_scripts.py` abria ventanas de consola visibles (`fake_health_server.py` y `python -c ...PROMPT_SHA256...`). Corregirlo con `creationflags=CREATE_NO_WINDOW` en los subprocesos de pruebas y scripts y `Start-Process -WindowStyle Hidden` en PowerShell, y confirmar que ninguna prueba toca el Programador de tareas real (`MinervaStartBot`, `MinervaHealthAlert`, `MinervaDailySummary`). **`app/` no cambio.**

- **Medicion antes de tocar nada** (monitor de ventanas propio, `scratchpad/winmon/win_monitor.py`: sondea `EnumWindows` cada 5 ms y registra cada consola visible nueva con la linea de comandos de su dueno; validado con un control positivo que abre una consola a proposito): **10 consolas visibles en solo 12 pruebas, TODAS de `fake_health_server.py`** (el bot nativo falso y el `docker compose up -d` falso), lanzadas con `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`. **Causa raiz**: el `.venv` es de uv y su `python.exe` **y su `pythonw.exe`** son el mismo trampolin de CONSOLA (subsistema 3, 45 KB) que lanza el interprete real como hijo; sin consola propia (`DETACHED_PROCESS`, un padre sin consola, el Programador de tareas) ese hijo abre una consola VISIBLE; con `CREATE_NO_WINDOW` el trampolin recibe una consola oculta y su hijo la hereda. El `python -c ...PROMPT_SHA256...` de la mudanza **no** abria ventana en la medicion (iba con `-NoNewWindow`, que equivale a `CREATE_NO_WINDOW`); se cambio igual a `-WindowStyle Hidden` como se pidio.
- **Cambios**: `tests/support/winproc.py` (`NO_WINDOW`) y `creationflags=NO_WINDOW` en las 34 llamadas a `subprocess.run/Popen` de 21 archivos (19 de pruebas y los falsos `fake_docker.py` y `fakes.py`: los servidores falsos pasan de `DETACHED_PROCESS` a `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, y el `taskkill`); `scripts/docker_support.py` ya lo usaba para `docker` y ahora ofrece `run_streaming` (sin ventana, reenvia stdout y stderr en vivo por tuberias y cierra el arbol de procesos con Ctrl+C: sin consola propia el hijo ya no recibe el Ctrl+C ni escribe en la nuestra) que usa `docker_db.py run` en lugar de `subprocess.run`; `docker_common.ps1`: `Invoke-Native` (y su `taskkill`) con `-WindowStyle Hidden` en vez de `-NoNewWindow`; ademas `Get-HealthOrNull` admite reintentos y la sonda previa del bot nativo hace 2 intentos (una sola sonda lenta ya no basta para declarar que no responde).
- **Guardianes** (para que no vuelva): `tests/unit/test_no_console_windows.py` — (1) AST: toda llamada a `subprocess.*`/`asyncio.create_subprocess_*` de `app/`, `scripts/` y `tests/` lleva `creationflags` con `NO_WINDOW`, nada usa `DETACHED_PROCESS`/`CREATE_NEW_CONSOLE`/`os.system`/`os.popen`/`os.startfile` (con pruebas de que el guardian detecta cada forma de abrir una consola); (2) todo `Start-Process` de `scripts/*.ps1` y de los .ps1 que generan las pruebas lleva `-WindowStyle Hidden` (nunca `-NoNewWindow`); (3) **empirica**: arranca de verdad el bot nativo falso y el contenedor falso y comprueba con la API de ventanas (`tests/support/window_probe.py`) que no queda ninguna consola visible de `fake_health_server.py` (el sondeo se valido contra el lanzamiento antiguo: ve 1 consola con `DETACHED_PROCESS` y 0 con `CREATE_NO_WINDOW`).
- **Verificacion en el peor caso**: suite completa con el proceso de pytest SIN CONSOLA (`FreeConsole()` antes de empezar: cualquier hijo lanzado sin `CREATE_NO_WINDOW` habria abierto una consola nueva) mientras el monitor vigilaba: **0 consolas atribuibles a las pruebas** en los ~830 s de la corrida; las unicas ventanas registradas en las 6,5 h que el monitor siguio vivo (40) son las de la tarea real `MinervaHealthAlert`.
- **Programador de tareas real: auditoria y garantia.** Hallazgo: **ninguna prueba lo tocaba** (solo `tests/support/fake_schtasks.py` y su cableado nombran a `schtasks`; la unica puerta de los scripts es `$env:MINERVA_SCHTASKS_EXE` en `docker_common.ps1`, y las pruebas de la mudanza la sobrescriben con su falso, de tarea `MinervaStartBotTest`). Ahora ademas esta **garantizado**: `tests/conftest.py` apunta esa puerta a una **trampa** (`schtasks` que no toca nada, registra la llamada y sale con 99) durante toda la sesion y la sesion FALLA al terminar si la trampa llego a usarse; `tests/unit/test_no_real_task_scheduler.py` vigila estaticamente que ningun otro codigo (.ps1, .py de `app/`, `scripts/`, `tests/`) nombre a `schtasks` ni a los cmdlets `*-ScheduledTask` y prueba de forma funcional que `docker_common.ps1`, cargado con los nombres de las tareas REALES, llega a la trampa y jamas al `schtasks` real. **Comprobacion empirica**: la lista de tareas `Minerva*` (solo existe `MinervaHealthAlert`) y la huella SHA-256 de su definicion XML son identicas antes y despues de todas las corridas.
- **Hallazgo aparte (el origen mas frecuente de ventanas, no es de las pruebas)**: la tarea real `MinervaHealthAlert` (accion `D:\...\.venv\Scripts\pythonw.exe scripts\telegram_alert.py`, cada 10 min) **abre una consola visible cada vez** (40 apariciones en 6,5 h): su `pythonw.exe` es el trampolin de consola de uv. Medido con tareas FICTICIAS en el Programador real: `pythonw.exe` del venv abre consola; `powershell.exe -WindowStyle Hidden` solo parpadea; **`conhost.exe --headless <programa> ...` no abre nada** y la tarea funciona igual (tambien cuando un script lanzador deja un hijo oculto de larga vida: sobrevive). El cambio exacto para la tarea real se probo con una tarea ficticia identica (`Set-ScheduledTask` conserva el "Iniciar en") y esta en `docs/MIGRACION_PC.md`; **NO lo aplique a la tarea real** (no estaba pedido y es tu red de seguridad): lo aplicas tu o me lo pides. Documentos corregidos para que las tareas nuevas no abran ventana: `MIGRACION_PC.md` (MinervaStartBot y MinervaDailySummary), manual (MinervaBackup, §3.5, §11.5), docstrings de `telegram_alert.py` y `telegram_daily.py`. Se crearon y borraron 6 tareas ficticias `MinervaPopupProbe*` (ninguna real tocada).
- **Verificacion final** (2026-10-22): pytest **1952 passed / 4 skipped en 11 min 21 s** (1956 recogidas: 1901 unitarias, 4 de integracion con red real y 51 E2E; antes 1921 de 1925); `ruff check .` limpio; la corrida estricta con monitor dio 2 fallos de tiempo (la sonda de `/health` de 8 s y una espera de 8 s, con la maquina cargada por el propio monitor) que pasan en aislado y en escenario estricto sin monitor: se endurecieron (reintento en la sonda y 20 s en la prueba que exige el motivo exacto) y la suite completa quedo en verde. Vitest y el build del frontend no se tocaron.
- **Para ti**: aplicar (o pedirme que aplique) el cambio de `MinervaHealthAlert` a `conhost.exe --headless` de `docs/MIGRACION_PC.md` (es lo que mas ventanas abre hoy); las copias del scratchpad siguen pendientes de borrar (`rm` denegado).
