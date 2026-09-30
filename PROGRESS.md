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

**Estado: IMPLEMENTADA. Pendiente tu aprobacion para pasar a Fase 2.**

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
  exponencial, usa `truststore` (almacen de certificados del SO) para
  funcionar tambien en redes corporativas con inspeccion TLS.
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

### Pendientes explicitos para fases siguientes

- Fase 2: universo dinamico (CoinGecko + filtro Bitunix), 2-4 estrategias
  candidatas adicionales, motor de backtesting (winrate, profit factor,
  drawdown, con fees y funding incluidos), validar profundidad de funding
  historico.
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
