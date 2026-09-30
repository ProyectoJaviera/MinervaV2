# Plan de Fase 2 — Universo dinámico, indicadores, estrategias candidatas y backtesting

> Estado: **pendiente de tu aprobación**. No se ha escrito código de Fase 2 (solo investigación empírica contra las APIs reales, documentada abajo).

## Contexto

Fase 1 (aprobada, con 4 ajustes ya aplicados: loopback en Docker, `truststore` centralizado en `app/core/http.py`, `requires-python` acotado a 3.11/3.12, `.gitignore` ampliado) entregó la vertical mínima: datos reales de Bitunix → una estrategia simple por reglas → `PaperBackend` → DB → API/frontend de solo lectura. Quedó documentado en `PROGRESS.md` que el universo era mono-símbolo (`BTCUSDT`) y que no había backtesting todavía.

Fase 2 construye lo que SPEC.md exige antes de arriesgar cualquier capital simulado en una estrategia: (1) el universo dinámico real (top-10 CoinGecko menos stablecoins/wrapped/staking, intersectado con los perpetuos USDT de Bitunix), y (2) un motor de backtesting riguroso que evalúe 3-5 estrategias de familias distintas y descarte las que no muestren ventaja — con la metodología explícita anti-sobreajuste que pediste (sin look-ahead, fuera de muestra, walk-forward, múltiples regímenes).

**Corrección crítica descubierta durante esta investigación** (no es parte del diseño nuevo, es un bug de Fase 1 que hay que arreglar al empezar Fase 2): `app/market/ohlcv_history.py::get_or_fetch` asume que `GET /market/kline` pagina **hacia adelante** desde `start_time` incrementando el cursor. Verifiqué empíricamente contra la API real que en realidad pagina **hacia atrás** desde `end_time` (devuelve las `limit` velas más recientes en o antes de `end_time`). Con el cursor actual, pedir un rango histórico amplio nunca avanza correctamente. Hay que reescribir la función para paginar hacia atrás (cursor = `end_time`, decrece en cada página al `open_time` más antiguo recibido menos 1, hasta cubrir el `start_time` deseado o agotar el historial). Esto es prerrequisito de todo lo demás en esta fase: sin datos históricos correctos no hay backtest válido.

## Hallazgos empíricos verificados en esta sesión (llamadas reales a las APIs, no solo documentación)

- **Profundidad de historia de Bitunix**: probado con `BTCUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT` en `1d` y `4h` — todos los símbolos probados alcanzan el mismo piso: **2022-04-17**, sin excepción (~4.5 años de historia, hasta hoy 2026-09-30). Cubre el bear market de 2022, la recuperación/lateralización de 2023, el ciclo alcista 2024-2025 y datos recientes de 2025-2026 — suficientes regímenes distintos para el walk-forward pedido. No se necesita una fuente externa de respaldo para OHLCV.
- **Profundidad de `get_funding_rate_history`**: para `BTCUSDT`, el historial real llega solo hasta aproximadamente **marzo-junio de 2024** (~2.3 años), sensiblemente menos que las velas. Para periodos anteriores a esa fecha el backtest debe aproximar el funding (ver sección A).
- **CoinGecko `/coins/markets`**: el parámetro `category` **solo filtra por inclusión** (no hay forma de excluir una categoría), y la respuesta **no incluye el campo de categoría** por moneda. Por lo tanto, excluir stablecoins/wrapped/staking no puede hacerse en una sola llamada; hay que cruzar dos conjuntos (ver sección D).
- **Category IDs verificados** (vía `GET /coins/categories/list`, que respondió 200 incluso sin API key en esta prueba): `stablecoins`, `wrapped-tokens`, `liquid-staking-tokens` — estos tres cubren exactamente los ejemplos que SPEC.md pide excluir (USDT/USDC, WBTC, stETH respectivamente). Se usan los tres como filtro de exclusión.

## A. Metodología de backtest

**Motor**: `app/backtesting/engine.py` (nuevo). Reutiliza las fórmulas de fees/PnL ya implementadas y testeadas en `app/execution/paper_backend.py` (mismas fórmulas documentadas con ejemplo numérico en `PROGRESS.md`) para que una estrategia se comporte igual en backtest y en paper trading en vivo (Fase 3).

- **Sin sesgo de anticipación**: la estrategia se evalúa sobre datos hasta la vela `i` (cerrada); si emite señal, la orden se llena al **open de la vela `i+1`** (más slippage), nunca al close de `i`. Regla del motor, no de cada estrategia.
- **Costos**: fee taker sobre entrada y salida (igual que `PaperBackend`, variable `TAKER_FEE_PCT`); slippage configurable en bps aplicado en contra del lado de la operación sobre el precio de llenado (default conservador, p. ej. 3-5 bps); funding aplicado en cada `fundingTime` cruzado mientras la posición está abierta, usando `get_funding_rate_history` real cuando exista dato para ese periodo/símbolo, y para el resto (anterior a ~2024) aproximando con la mediana del funding real observado para ese símbolo (fallback a una constante configurable si ni eso hay) — **cada trade de backtest se marca `funding_real` o `funding_aproximado`** para que el reporte final sea honesto sobre qué parte de la historia tiene datos reales.
- **Apalancamiento y liquidación**: 10x aislado; precio de liquidación calculado por vela usando el tramo de `maintenanceMarginRate` de `contract_specs_cache.margin_tiers_json` (ya cacheado desde Fase 1) según el notional de la posición. Si el rango [low, high] de una vela cruza el precio de liquidación, se liquida esa vela (evento independiente de SL/TP, y si compite con ellos se resuelve con la misma regla de peor caso de abajo).
- **SL/TP/trailing evaluados con high/low de cada vela** (no con el close): si en la misma vela el rango toca tanto el SL como el TP, **se asume que el SL se tocó primero** (regla explícita tuya, peor caso). Para el trailing stop, el motor procesa cada vela como una trayectoria supuesta `open → extremo adverso → extremo favorable → close`: primero se aplica el movimiento en contra (puede disparar el SL duro o mover el trailing en contra sin disparar nada nuevo), y solo después el movimiento a favor (puede subir el máximo de referencia del trailing o disparar el TP). Es una aproximación conservadora documentada — no hay datos de tick históricos para reconstruir la trayectoria real intra-vela; la evaluación a nivel de tick que exige SPEC.md es para el paper trading EN VIVO (Fase 3), no para este backtest histórico.
- **Validación fuera de muestra**: cada estrategia usa parámetros fijos de literatura (EMA 9/21/50, RSI 14, Bollinger 20/2, Donchian 20, ATR 14) — deliberadamente **sin optimizar parámetros** en esta fase, para no introducir sobreajuste desde el diseño. Aun así, cada combinación estrategia+símbolo se evalúa en dos particiones cronológicas:
  - **In-sample (70% más antiguo)**: métricas de referencia.
  - **Out-of-sample (30% más reciente)**: la que decide si la estrategia sobrevive (sección C) — si el rendimiento se degrada sustancialmente de IS a OOS, se trata como señal de sobreajuste/inestabilidad aunque los parámetros sean fijos (puede haber overfitting de SELECCIÓN de estrategia, no solo de parámetros).
  - **Walk-forward**: además, se particiona toda la historia en folds de 6 meses con paso de 2 meses (folds superpuestos) y se reporta la métrica por fold, para detectar si el resultado depende de un solo periodo favorable.
  - **Regímenes**: cada fold se etiqueta heurísticamente (alcista/bajista/lateral) comparando el retorno del símbolo en ese fold contra su propia media móvil de 200 periodos; el reporte agrega resultados por régimen.
- **Mínimo de operaciones**: una celda (estrategia, símbolo, fold) con menos de **30 operaciones cerradas** se marca `muestra_insuficiente` y no cuenta para el veredicto de descarte; una estrategia necesita **al menos 100 operaciones cerradas en total** (sumando todos los símbolos del universo) para que su veredicto agregado se considere válido — si no llega, se reporta como "evidencia insuficiente" (mismo lenguaje que SPEC.md usa para el % de éxito del LLM) en vez de aprobarla o descartarla.
- **Benchmark**: por cada símbolo, comprar y mantener con el mismo capital nocional (sin apalancamiento) durante el mismo periodo exacto que el backtest de la estrategia; se reporta la diferencia de retorno total y de drawdown máximo.
- **Reporte**: por estrategia × símbolo × fold/régimen, y agregado por estrategia: winrate, profit factor, drawdown máximo, expectativa por operación (PnL neto promedio), comisiones totales, funding total (con desglose real/aproximado), número de operaciones, retorno del benchmark y la diferencia contra él.

## B. Estrategias candidatas (5, familias distintas)

Se extiende `BaseStrategy` (`app/strategies/base.py`) con dos métodos opcionales con default `None` — `stop_distance(df) -> float | None` y `take_profit_distance(df) -> float | None` — para que cada estrategia pueda definir su propia lógica de riesgo (ATR, % fijo, etc.) sin romper la interfaz actual ni `EMACrossStrategy` (que puede no implementarlos y usar un default del motor de backtest).

1. **Tendencia con stop ATR** (`trend_atr_stop.py`): filtro de tendencia EMA(50) (solo LONG si precio > EMA50, solo SHORT si precio < EMA50) + disparo en cruce EMA(9/21) en la dirección del filtro; SL inicial a `entry − 2.5·ATR(14)` (o `+` para SHORT); trailing tipo *chandelier exit* que sube con el máximo favorable menos `2.5·ATR(14)`. Timeframe 4h. Por qué: se adapta a la volatilidad real del activo en vez de un % fijo, y solo opera a favor de la tendencia mayor.
2. **Reversión a la media en rango** (`mean_reversion_rsi_bb.py`): entra LONG si RSI(14) < 30 Y el cierre toca/cruza la banda de Bollinger(20,2) inferior; SHORT simétrico con RSI > 70 y banda superior; salida en la banda media (SMA20) o SL fijo si el precio sigue alejándose. Timeframe 1h. Por qué: familia complementaria a la de tendencia — debería funcionar mejor precisamente en los regímenes laterales donde (1) pierde, dando diversificación real, no solo otro cruce de medias.
3. **Ruptura de canal (Donchian)** (`donchian_breakout.py`): LONG si el cierre supera el máximo de las últimas 20 velas, SHORT si cae bajo el mínimo de las últimas 20; SL en el punto medio del canal o por ATR. Timeframe 4h o 1d (se prueban ambos). Por qué: lógica de disparo completamente distinta a un cruce de medias (rompimiento de rango vs. cruce), clásica en momentum sistemático.
4. **Cruce EMA 9/21** (`ema_cross.py`, YA IMPLEMENTADA en Fase 1): se mantiene como referencia/baseline para comparar las 4 nuevas contra algo ya construido y testeado, en igualdad de condiciones (mismo motor).
5. **Contrarian de funding rate** (`funding_contrarian.py`, EXPERIMENTAL): usa `funding_rate_history` como filtro contrarian (funding muy positivo sostenido → sesgo SHORT; muy negativo sostenido → sesgo LONG, opuesto al posicionamiento de la mayoría) combinado con un disparador de momentum simple para el timing de entrada. Por qué: única estrategia que no es puramente de precio, aporta diversificación de fuente de señal; se marca explícitamente como experimental porque su historia útil (~2.3 años, desde 2024) es más corta que las demás (~4.5 años), así que su muestra será menor y el veredicto puede quedar en "evidencia insuficiente" con más frecuencia — esto se documenta, no se oculta.

## C. Criterios de descarte y `backtest_runs`

Cada corrida (estrategia × símbolo × fold, IS y OOS) se guarda en `backtest_runs` (tabla ya propuesta en `docs/FASE0.md`, se crea ahora): `strategy_name, symbol, timeframe, period_start, period_end, is_oos ('IS'|'OOS'), regime_tag, winrate, profit_factor, max_drawdown_pct, expectancy_usdt, total_trades, fees_total_usdt, funding_total_usdt, funding_is_approximated (bool), benchmark_return_pct, discarded (bool), discard_reason, run_at`. Se guardan TODAS las corridas, incluidas las descartadas — es el registro auditable de que el proceso no fue "cherry-picking".

Una estrategia se **descarta** (no pasa a Fase 3) si, en su evaluación **OOS agregada** (nunca solo IS):
- `profit_factor <= 1.0` (sin ventaja neta tras costos), o
- el drawdown máximo agregado supera un techo de sensatez (**50%**, a un apalancamiento de 10x eso ya es una señal de riesgo de ruina), o
- peor retorno Y peor drawdown que el benchmark buy-and-hold simultáneamente (ninguna ventaja de riesgo/retorno), o
- más del **40% del PnL neto total** proviene de una sola operación o de un solo símbolo (concentración — el mismo criterio que aprobaste en Fase 0 para los criterios de paso a dinero real, aplicado aquí también a nivel de estrategia).

Si no llega a 100 operaciones totales, se marca `evidencia_insuficiente` (ni aprobada ni descartada) y se deja fuera de la Fase 3 hasta acumular más datos — no se fuerza una decisión con poca muestra.

## D. Universo dinámico (CoinGecko + Bitunix)

`app/market/coingecko_client.py` (nuevo), construido con `app/core/http.py::build_async_http_client` (regla ya establecida en el ajuste de Fase 1 — todo cliente HTTP nuevo debe usarla). Refresco cada 24h desde `core/scheduler.py`:

1. `GET /coins/markets?vs_currency=usd&order=market_cap_desc&per_page=30` (30 como colchón sobre el top 10 final, para absorber las exclusiones).
2. `GET /coins/markets?...&category=stablecoins`, `...&category=wrapped-tokens`, `...&category=liquid-staking-tokens` (los 3 category_id verificados arriba) — construye el conjunto de ids a excluir.
3. Filtra (1) quitando los ids de (2); toma los primeros 10 restantes por `market_cap_rank`.
4. Cruza cada símbolo contra `trading_pairs` de Bitunix (ya cacheado, Fase 1) para quedarse solo con los que tengan perpetuo USDT activo (`symbolStatus == OPEN`); si un candidato del top 10 no tiene perpetuo, se reemplaza por el siguiente en el ranking filtrado.
5. Persiste el snapshot en `asset_universe` (tabla de Fase 0, se crea ahora): rank, market_cap, motivo de exclusión si aplica, incluido/no.
6. **Activo que sale del universo con posición abierta** (regla que SPEC.md exige documentar): no se cierra por la fuerza — se marca `no_new_entries` para ese símbolo (bloquea nuevas aperturas) y la posición existente sigue su ciclo de vida normal (SL/TP/trailing/cierre manual). Evita cierres reactivos que solo generan más comisiones/slippage sin beneficio.

- **API key de CoinGecko**: el cliente envía `x-cg-demo-api-key` si `COINGECKO_API_KEY` está configurada; en esta investigación varias llamadas de solo lectura funcionaron incluso SIN key, pero el plan Demo oficial la exige, así que no hay que depender de ese comportamiento — se documenta en el README que conviene sacar la key gratuita.
- **Degradación si CoinGecko falla** (reintentos con backoff ya agotados): se conserva el ÚLTIMO snapshot válido de `asset_universe` (nunca se vacía el universo por un fallo de red); se registra `system_state.universe_last_refresh_ok_at` vs. el intento fallido. Si el snapshot vigente supera **48h** de antigüedad (2x el periodo de refresco), se bloquean nuevas aperturas hasta que un refresco tenga éxito — misma política que SPEC.md exige para datos de precio obsoletos, aplicada aquí por instrucción de CLAUDE.md ("bloqueo de nuevas operaciones si los datos críticos están obsoletos").
- **Atribución**: CLAUDE.md exige mostrar "Data provided by CoinGecko" en el frontend — anotado como pendiente de Fase 5 (el frontend mínimo de Fase 1 no lo necesita porque no consume CoinGecko todavía).

## E. Fuera de alcance de esta fase (y por qué)

- **Integrar el WS de Bitunix a la decisión en vivo**: el backtest solo necesita velas históricas cerradas; la evaluación a nivel de tick que exige SPEC.md es para el paper trading EN VIVO — Fase 3.
- **Aplicar SL/TP/trailing/liquidación dentro de `PaperBackend` en vivo**: esta fase los implementa SOLO dentro del motor de backtest (simulación histórica). Conectar esa misma lógica al loop en vivo de `PaperBackend`/`core/scheduler.py` es, explícitamente, Fase 3 — evita mezclar "motor de riesgo en producción" con "motor de backtest", que tienen necesidades de timing distintas (velas cerradas históricas vs. ticks en vivo). `PaperBackend` seguirá cerrando solo por señal contraria hasta Fase 3.
- **Límites de correlación/exposición entre posiciones simultáneas de distintos símbolos**: pertenece al motor de riesgo de cartera en vivo (Fase 3); el backtest de esta fase evalúa cada estrategia+símbolo de forma aislada.
- **Noticias, Claude como motor de decisión, memoria/lecciones, calibración del % de éxito**: Fase 4, sin cambios respecto al plan original.
- **Mostrar universo/resultados de backtest en el frontend, notificar cambios de universo por Telegram**: Fase 5. Los datos ya quedan en la base de datos, listos para que Fase 5 los muestre sin recalcular nada.
- **Optimizar/ajustar parámetros de las estrategias** (grid search, etc.): deliberadamente fuera de alcance para no introducir sobreajuste desde el arranque; si alguna estrategia queda en el límite del descarte, la decisión de afinar parámetros se toma explícitamente más adelante, nunca dentro de este primer backtest.

## Archivos nuevos/afectados (para la implementación, una vez aprobado este plan)

- **Corregir** `app/market/ohlcv_history.py` (paginación hacia atrás — prerrequisito, ver arriba).
- **Nuevo** `app/market/coingecko_client.py`, `app/market/universe.py` (orquesta los pasos 1-6 de la sección D).
- **Nuevo** `app/backtesting/engine.py`, `metrics.py`, `report.py`.
- **Nuevo** `app/strategies/trend_atr_stop.py`, `mean_reversion_rsi_bb.py`, `donchian_breakout.py`, `funding_contrarian.py`; **editar** `app/strategies/base.py` (métodos opcionales de riesgo) y `registry.py`.
- **Nuevo** `app/persistence/repositories/universe_repo.py`, `backtest_repo.py`; **editar** `app/persistence/database.py` (tablas `asset_universe`, `backtest_runs`; `contract_specs_cache` ya existe).
- **Editar** `.env.example`: `COINGECKO_*` ya existen; agregar `UNIVERSE_REFRESH_HOURS`, `UNIVERSE_STALENESS_HOURS`, `BACKTEST_SLIPPAGE_BPS`, `BACKTEST_MIN_TRADES_PER_CELL`, `BACKTEST_MIN_TRADES_TOTAL`.
- Sin cambios a `PaperBackend`/`core/scheduler.py` en esta fase (ver sección E).

## Verificación planeada de esta fase

1. `pytest` + `ruff check .` en verde, con tests nuevos por pieza: paginación corregida de `ohlcv_history` (con fixtures, sin red real), cada estrategia nueva (casos de cruce/ruptura/reversión con series sintéticas), el motor de backtest (caso SL+TP en la misma vela → confirma que gana el SL; caso liquidación; caso funding real vs. aproximado), la lógica de exclusión de universo (con respuestas CoinGecko fijas/mockeadas) y su degradación (simula fallo de red → universo previo se mantiene).
2. Corrida real (manual, con red) del backtest completo sobre el universo real de hoy y las 5 estrategias × timeframes candidatos — confirmar que produce filas en `backtest_runs` para cada combinación, que el reporte agregado por estrategia es legible, y que al menos una estrategia sobrevive el criterio de descarte (si ninguna sobrevive, se reporta honestamente y se discute contigo antes de Fase 3, no se relaja el criterio para forzar un "ganador").
3. Actualizar `PROGRESS.md` con el resultado del backtest (tabla resumen por estrategia), qué estrategias se descartan y por qué, y detenerse a esperar aprobación antes de Fase 3.

---

*Quedo a la espera de tu aprobación (o ajustes) antes de implementar cualquier código de Fase 2.*
