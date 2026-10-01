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

- Fase 3: ver `docs/FASE3_PLAN.md` (plan propuesto, pendiente de tu
  aprobacion).
- Fase 3: simulador realista completo (SL/TP escalonado, trailing,
  liquidacion por tiers de `position_tiers`, funding periodico, slippage,
  reconciliacion tras downtime), motor de riesgo completo (circuit breaker
  con enfriamiento de 8h + pausa indefinida separada para drawdown/perdida
  diaria, limites de correlacion).
- Fase 4: noticias (RSS CoinDesk/Cointelegraph, sentimiento con
  `claude-haiku-4-5`), Claude como motor de decision completo
  (`claude-sonnet-5`) con memoria/lecciones y calibracion del % de exito,
  presupuesto diario de 1 USD con corte duro.
- Fase 5: autenticacion (un usuario/password), dashboard completo (tiempo
  real via WS, graficos, controles), API completa, Telegram.
- Fase 6: Docker final multi-stage, pruebas E2E, informe de metricas de
  paper trading con la nota sobre backtest-vs-paper-trading del LLM.
