# FASE 0 — Evaluación, verificación de APIs y diseño preliminar

> Estado: **pendiente de tu aprobación**. No se ha escrito código de producto (solo investigación). Fuentes citadas en cada sección.

---

## 1. Evaluación crítica de `docs/openspec.md`

`openspec.md` es un diseño previo **nunca probado**, centrado en un solo activo (BTC/USDT, 4H) con el LLM como *gatekeeper* binario. `docs/SPEC.md` (la especificación vigente) pide algo más amplio: universo dinámico top-10, multi-timeframe, noticias, memoria/aprendizaje, backtesting obligatorio y el LLM como motor de decisión completo (no solo validador). Evalúo componente por componente:

| Componente de openspec.md | Reutilizar | Descartar / modificar | Por qué |
|---|---|---|---|
| Stack (asyncio, httpx, websockets, aiosqlite, Docker Compose) | ✅ Sí, completo | — | Coincide exactamente con las decisiones ya tomadas en SPEC.md. |
| `pandas-ta` para indicadores | ✅ Sí, como base | Vigilar: el paquete original está poco mantenido; si falla en Python 3.11+/3.12 usar el fork `pandas_ta-classic` o calcular EMAs/RSI manualmente | Riesgo de mantenimiento conocido en el ecosistema, no bloquea la fase 0. |
| Cliente Bitunix REST/WS (firma HMAC, reconexión con backoff) | ✅ Sí, como **diseño documentado** de `BitunixBackend` futura | ❌ No activar ni llamar endpoints autenticados (Tarea 1.3 de openspec fija apalancamiento/margen y coloca órdenes reales) | SPEC.md prohíbe explícitamente credenciales y órdenes reales en v1. En v1 solo se usan endpoints públicos de mercado (sin `api-key`). |
| `BaseStrategy` + cruces EMA50 / EMA9-21 | ✅ Sí, como **2 de las 3-5 estrategias candidatas** a backtestear | ❌ Descartar la idea de que sean "las" estrategias definitivas sin evidencia | openspec no incluye backtesting: SPEC exige backtest con winrate/profit factor/drawdown por estrategia y descartar las que no muestren ventaja. Hay que construir el motor de backtesting desde cero. |
| SL fijo -20% ROI, TP escalonado 30/30/40% en +20/+40/+60% ROI, trailing fijo al 20% callback | ❌ Descartar como valores **hardcodeados** en el motor | Reutilizar solo el **mecanismo** (cierre parcial, activación de trailing tras TP1) como opción de implementación | SPEC dice que SL/TP/trailing vienen del **output de Claude por operación** (`sl`, `tp`, `trailing`), configurables, no constantes fijas del sistema. |
| Margen fijo $10 USDT, solo BTC/USDT | ✅ Reutilizar $10 como **valor por defecto configurable** | ❌ Descartar restricción a un solo activo | SPEC pide universo dinámico (top 10 CoinGecko con perpetuo USDT en Bitunix) y tamaño "10 USDT o más, configurable". |
| LLM Rol 1 (APPROVE/REJECT) y Rol 2 (post-mortem tras 3 pérdidas) | ✅ Reutilizar el **patrón** (validación + post-mortem) | ❌ Expandir: Claude debe emitir el esquema completo (`accion`, `activo`, `confianza`, `porcentaje_exito_estimado`, `razonamiento`, `factores_clave`, `riesgos`, `sl`, `tp`, `trailing`), no solo aprobar/rechazar una señal ya calculada | SPEC define un motor de decisión de confluencia (reglas + LLM) con salida estructurada rica, memoria de operaciones similares y calibración del % de éxito — nada de esto existe en openspec. |
| Circuit breaker a 3 pérdidas consecutivas | ✅ Reutilizar como **valor por defecto** | Hacerlo configurable, no fijo | SPEC: "N pérdidas consecutivas (configurable)". |
| Esquema DB (`trades`, `llm_logs`, `system_state`) | ✅ Reutilizar como **esqueleto inicial** | Ampliar sustancialmente (ver sección 4) | Faltan tablas para noticias, memoria/lecciones, universo de activos, specs de contrato, calibración, reconciliación, backtests — todas exigidas por SPEC. |
| Telegram: `/status /balance /positions /pause /resume /llm_report` | ✅ Reutilizar base | Añadir `/metrics` y `/kill` | SPEC exige explícitamente `/metrics` y un kill switch por Telegram. |
| Ausencia de: noticias, CoinGecko/universo, memoria/aprendizaje, riesgo por correlación, límite de pérdida diaria, presupuesto de API, reconciliación al reiniciar, calibración del % de éxito, frontend | — | Diseñar todo desde cero | openspec no cubre ninguno de estos puntos; son requisitos centrales de SPEC.md. |
| Fases 1-6 de openspec | Parcialmente reutilizable como *checklist* de bajo nivel dentro de las fases de SPEC | Reordenar bajo las 6 fases de SPEC.md (que ya gobiernan este proyecto) | Las fases de SPEC.md son las vigentes; las de openspec no incluyen backtesting, noticias, memoria ni frontend. |

**Conclusión:** openspec.md aporta buenas ideas de bajo nivel (firma HMAC, reconexión WS, mecánica de TP parcial/trailing, patrón validación+post-mortem) pero su alcance es mucho más estrecho que SPEC.md y varias decisiones (SL/TP fijos, un solo activo, LLM binario) contradicen requisitos explícitos de SPEC. Lo trato como **referencia de implementación parcial**, no como plan a seguir.

---

## 2. Verificación de la API oficial de Bitunix

Fuentes verificadas directamente (no inventado): [www.bitunix.com/api-docs/futures/common/introduction.html](https://www.bitunix.com/api-docs/futures/common/introduction.html), endpoints individuales bajo `www.bitunix.com/api-docs/futures/market/*` y `.../position/*`, y [github.com/BitunixOfficial/open-api](https://github.com/BitunixOfficial/open-api). Nota: `openapidoc.bitunix.com` redirige (301) a `www.bitunix.com/api-docs/...`, que es el dominio canónico actual.

### 2.1 Repo `open-api` (GitHub)
Contiene solo `Demo/` (ejemplos de código) y `README.md`; remite explícitamente a la documentación oficial. Útil como referencia de ejemplo para la **futura** firma HMAC de `BitunixBackend`, no aporta endpoints propios que no estén ya en la doc oficial. No es necesario para v1 (solo usamos endpoints públicos, sin auth).

### 2.2 Host y autenticación
- Base REST: `https://fapi.bitunix.com`
- WebSocket: `wss://fapi.bitunix.com/public/` (público) y `wss://fapi.bitunix.com/private/` (privado, requiere credenciales — **no se usa en v1**).
- Auth (solo relevante para la futura `BitunixBackend`, documentada y desactivada): headers `api-key`, `nonce` (string aleatorio), `timestamp` (ms), `sign` (HMAC-SHA256), `Content-Type: application/json`.

### 2.3 Endpoints públicos de mercado (sin autenticación, límite 10 req/s/IP) que usará v1

| Endpoint | Método | Uso en Minerva |
|---|---|---|
| `/api/v1/futures/market/trading_pairs` | GET | Specs de contrato: `symbol`, `minTradeVolume`, `basePrecision`, `quotePrecision`, `minLeverage`, `maxLeverage`, `defaultLeverage`, `defaultMarginMode`, `symbolStatus`, `isApiSupported`. **También** sirve para saber qué símbolos tienen perpetuo USDT activo (filtro del universo). |
| `/api/v1/futures/market/tickers` | GET | `lastPrice`, `markPrice`, `open/high/low`, `baseVol`/`quoteVol` 24h — precios en tiempo real (fallback REST) del top 10. |
| `/api/v1/futures/market/kline` | GET | Velas OHLCV. Parámetros: `symbol`, `interval` (1m…1M, incluye 4h), `startTime`, `endTime`, `limit` (máx. 200), `type=LAST_PRICE\|MARK_PRICE`. Base para indicadores y backtesting. |
| `/api/v1/futures/market/depth` | GET | Order book público — útil para estimar slippage realista. |
| `/api/v1/futures/market/funding_rate/batch` | GET | `fundingRate`, **`fundingInterval` (horas, variable por símbolo, no asumir 8h fijo)**, `nextFundingTime`, `indexPrice`, `max/minFundingRate`. Necesario para simular funding real. |
| `/api/v1/futures/position/get_position_tiers` | GET | Tiers de margen por notional: `level`, `startValue`/`endValue`, `leverage`, `maintenanceMarginRate` — imprescindible para simular **liquidación realista** en margen aislado. |

WebSocket público: canales `depth`, `market_kline_<intervalo>` / `mark_kline_<intervalo>` (push cada 500ms), tickers/mark price — todos sin autenticación. Se usarán para evaluar SL/TP/trailing a nivel de tick, como exige SPEC.

### 2.4 Comisiones (fees)
**No existe un campo de fee en los endpoints públicos** (`trading_pairs`, `tickers`, `funding_rate/batch`) — la tasa de comisión real depende del tier VIP de la cuenta y solo se puede consultar autenticado (fuera de alcance v1, sin credenciales). La tabla de tarifas oficial ([bitunix.com/service/handling-fee](https://www.bitunix.com/service/handling-fee)) publica la tarifa **VIP 0 (base)**: **maker 0.0200% / taker 0.0600%** para futuros. Propongo usarla como **valor por defecto configurable** en el simulador (`.env`: `MAKER_FEE_PCT`, `TAKER_FEE_PCT`), documentando que es la tarifa base pública y no la de una cuenta específica.

### 2.5 Open Interest
Revisé `tickers`, `funding_rate/batch` y `trading_pairs`: **ninguno expone Open Interest**. No encontré un endpoint público de OI en la documentación oficial. SPEC lo pide "si está disponible" → **no está disponible públicamente sin autenticación**; propongo omitirlo en v1 (dejarlo como `null`/no aplica en los indicadores) salvo que decidamos integrar una fuente externa (ver pregunta 7).

---

## 3. Evaluación de CoinGecko

Fuentes: [docs.coingecko.com](https://docs.coingecko.com/reference/introduction), [coingecko.com/en/api/pricing](https://www.coingecko.com/en/api/pricing), guía de autenticación Demo.

**¿Aporta valor?** Sí: nos ahorra construir un ranking de capitalización propio y ofrece filtrado por categoría (`stablecoins`, `wrapped-tokens`) para excluirlos del universo, tal como exige SPEC. Nuestro uso real es mínimo: 1 refresco cada 24h del top N por market cap + datos globales ≈ 30-60 llamadas/mes.

**Plan gratuito (Demo):**
- Requiere **registro gratuito y API key** (header `x-cg-demo-api-key`) — no es anónimo como asumía una primera lectura superficial; sí hay que generarla en el dashboard de CoinGecko.
- Límite: **100 llamadas/min, 10.000 llamadas/mes**.
- Licencia: uso no comercial con **atribución obligatoria** ("Data provided by CoinGecko").
- Datos: ranking por market cap, market data de cada moneda, categorías, datos globales; histórico diario hasta 1 año.
- Nuestro volumen de uso (decenas de llamadas/mes) está muy por debajo del límite — plan gratuito es suficiente para v1.

**Decisión propuesta:** usar `/coins/markets` (orden por `market_cap_desc`) + filtrar por categorías `stablecoins`/`wrapped-tokens`, cruzando el resultado contra `trading_pairs` de Bitunix para quedarnos solo con los que tengan perpetuo USDT. La API key de CoinGecko se trata como credencial: va en `.env` (nunca en `.env.example` con valor real), igual que exige CLAUDE.md para cualquier clave.

---

## 4. Estructura de carpetas, esquema de base de datos y frontend

### 4.1 Estructura de carpetas propuesta

```
minerva/
  app/
    config.py                 # pydantic-settings (.env)
    main.py                   # FastAPI app + tareas asyncio (lifespan)
    core/
      logging.py
      scheduler.py             # refresco universo 24h, noticias, snapshots equity
    market/
      bitunix_rest.py          # cliente REST público (market data)
      bitunix_ws.py            # cliente WS público + reconexión backoff
      coingecko_client.py
      universe.py              # top10, exclusión stable/wrapped, intersección con Bitunix
      contract_specs.py        # cache trading_pairs / position_tiers / funding
    indicators/
      engine.py                # OHLCV multi-timeframe -> indicadores
    strategies/
      base.py                  # BaseStrategy
      ema_cross_50.py / ema_cross_9_21.py / ... (3-5 candidatas)
      registry.py
    backtesting/
      engine.py                # simulación histórica con fees+funding
      metrics.py                # winrate, profit factor, drawdown
    news/
      sources.py / dedup.py / sentiment.py
    risk/
      limits.py                # max posiciones, pérdida diaria, correlación
      circuit_breaker.py
    execution/
      backend_base.py          # interfaz ExecutionBackend
      paper_backend.py         # activa en v1
      bitunix_backend.py       # SOLO diseño/documentación, no se instancia
      simulator.py             # fills, slippage, liquidación, SL/TP/trailing a tick
      reconciliation.py        # recálculo tras downtime
    decision/
      llm_client.py             # cliente async Anthropic, JSON validado
      schemas.py                 # pydantic: esquema de decisión
      confluence.py               # score de reglas + fusión con LLM
      memory.py                   # recuperación de trades similares, lecciones
      calibration.py              # % éxito estimado vs. real
    notifications/
      telegram_bot.py
    persistence/
      database.py / models.py / repositories/
    api/
      routes/ (positions, trades, risk, metrics, news, auth, ws)
      security.py               # auth local del frontend
  frontend/                     # ver 4.3
  tests/
    unit/ integration/
  docker/
    Dockerfile.backend  Dockerfile.frontend
  docs/  (SPEC.md, openspec.md, FASE0.md, PROGRESS.md)
  docker-compose.yml
  .env.example
  README.md
  PROGRESS.md
  pyproject.toml
```

### 4.2 Esquema de base de datos (propuesta, DDL definitivo en Fase 1/5)

- **trades** — ciclo de vida completo: símbolo, lado, estrategia origen, apalancamiento, margen, notional, entry/exit price, qty, `sl`/`tp`/`trailing` (JSON, vienen de la decisión), confianza, `porcentaje_exito_estimado`, `sample_size_similar_trades`, pnl bruto/neto, fees, funding, `close_reason`, timestamps, **`decision_json`** (payload íntegro devuelto por Claude, para auditoría).
- **llm_logs** — `trade_id` (nullable), `call_type` (DECISION/POST_MORTEM/NEWS_SENTIMENT/CALIBRATION), modelo usado, prompt completo, respuesta cruda y parseada, tokens, costo estimado, latencia.
- **system_state** — estado RUNNING/PAUSED, pérdidas consecutivas, pnl diario, config de riesgo activa, último refresco de universo/reconciliación, gasto de API del día.
- **news_items** / **news_asset_link** — fuente, url, fecha, hash para deduplicar, sentimiento, activo(s) relacionado(s) (N:M).
- **lessons_learned** — texto de la lección, trades considerados, tamaño de muestra mínimo cumplido (bool), veces usada en prompts.
- **asset_universe** — snapshot por refresco: símbolo, rank CoinGecko, market cap, exclusión por stable/wrapped, si tiene perpetuo en Bitunix, incluido/motivo.
- **contract_specs_cache** — specs por símbolo (precisión, min trade volume, leverage, fee por defecto, funding interval, tiers de margen) refrescado periódicamente.
- **risk_config_history** — configuración de riesgo vigente a lo largo del tiempo (quién/cuándo la cambió).
- **equity_snapshots** — balance, PnL no realizado/realizado acumulado, nº posiciones abiertas, drawdown — para graficar la curva de equity sin recomputar.
- **calibration_stats** — por rango de % de éxito estimado: nº operaciones, winrate real (para el gráfico de calibración del dashboard).
- **reconciliation_log** — eventos detectados al reiniciar tras downtime (SL/TP/liquidación retroactivos).
- **backtest_runs** — estrategia, símbolo, timeframe, periodo, winrate, profit factor, drawdown máx., si se descartó y por qué.

### 4.3 Elección de frontend

**Propuesta: React 18 + Vite + TypeScript + TailwindCSS**, servido como build estático (Nginx o el propio FastAPI) dentro de Docker Compose, con:
- **Datos en tiempo real:** WebSocket nativo del navegador hacia un endpoint FastAPI que reemite precios/posiciones/decisiones; TanStack Query para REST (historial, métricas) con revalidación.
- **Gráficos:** `lightweight-charts` (TradingView) para curva de equity y precios; para el resto de estadísticas (winrate, drawdown, distribución) seguiré la guía de la skill `dataviz` para mantener consistencia visual y accesibilidad de color.
- **Auth local:** un usuario/contraseña única (hash bcrypt, verificado en backend) + sesión JWT en cookie `httpOnly`; simple pero cumple el requisito de SPEC de "autenticación aunque sea local" sin sobre-ingeniería para un bot mono-usuario.
- **PWA:** `vite-plugin-pwa` desde ahora (manifest + service worker básico) para que el dashboard sea instalable; el empaquetado a APK vía Capacitor se deja para después de v1, tal como indica SPEC ("para convertir a APK después"), evitando esa complejidad ahora.

**Por qué React+Vite y no otra opción:**
- *Next.js* añade SSR/routing de servidor innecesario para un dashboard 100% local que solo habla con nuestra propia API — más complejidad de build/Docker sin beneficio (no hay SEO que optimizar).
- *Svelte/Vue* tienen buen soporte de gráficos también, pero React tiene el ecosistema más maduro para `lightweight-charts` y para el eventual empaquetado Capacitor→APK que SPEC prevé a futuro, y es la opción que SPEC.md ya sugiere explícitamente.
- Vite (en vez de CRA) da un dev server y build mucho más rápidos y una imagen Docker de frontend más simple (build estático).

---

## 5. Preguntas (máximo 10, agrupadas)

**A. Parámetros de riesgo (SPEC pide que yo proponga valores; los propongo, confírmalos o ajústalos):**
1. Drawdown máximo antes de considerar pasar a dinero real: propongo **20% sobre el capital inicial simulado**. ¿Lo confirmas o prefieres otro umbral?
2. Nº mínimo de operaciones cerradas para evaluar los criterios de paso a real: propongo **≥50 operaciones**. ¿Te parece razonable?
3. Circuit breaker: ¿confirmamos **3 pérdidas consecutivas** como valor por defecto (configurable), igual que en openspec.md?
4. Límite de pérdida diaria y máximo de posiciones simultáneas / exposición por activo: ¿tienes valores en mente o propongo defaults (p. ej. -5% diario, máx. 3 posiciones simultáneas, máx. 40% del capital en un solo activo) para que los ajustes luego desde el frontend?

**B. LLM y costos:**
5. ¿Qué modelo usar para cada rol? Propongo **Haiku 4.5** para clasificación de noticias/sentimiento (alto volumen, bajo costo) y **Sonnet 5** para la decisión de trading y post-mortem (razonamiento más profundo). ¿Apruebas esta asignación o prefieres Opus 5 para las decisiones? ¿Cuál es tu presupuesto diario tolerable en USD para la API de Claude?
6. ¿Qué fuentes de noticias específicas (RSS/APIs) quieres que use? Puedo proponer una lista (CoinDesk, CoinTelegraph, The Block RSS, etc.) si no tienes preferencia — ¿investigo y propongo, o ya tienes fuentes de confianza?

**C. Datos de mercado (hallazgos de la verificación):**
7. Open Interest **no está disponible** en los endpoints públicos de Bitunix verificados. ¿Lo omitimos en v1, o investigo una fuente externa alternativa (p. ej. Coinalyze/Coinglass) aunque agregue una dependencia más?
8. Las comisiones no son consultables públicamente; usaré la tarifa **VIP 0 oficial (maker 0.02% / taker 0.06%)** como default configurable del simulador. ¿Confirmas, o tu cuenta real de Bitunix tiene otro tier que debamos reflejar para que el paper trading sea más fiel a tu caso?

**D. Frontend / infraestructura:**
9. Autenticación del frontend: ¿te sirve un **usuario/contraseña único** (sin multiusuario), o necesitas varias cuentas/roles desde v1?
10. ¿Confirmamos **UTC** y **USDT** como zona horaria y moneda de referencia para todos los reportes (diario/semanal/mensual/anual), o prefieres otra zona horaria para las notificaciones de Telegram?

---

*Quedo a la espera de tu aprobación y respuestas antes de iniciar la Fase 1.*
