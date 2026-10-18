# Esquema de la base de datos

> **Generado** por `scripts/export_schema_doc.py` a partir de `app/persistence/database.py`
> (`SCHEMA` y migraciones de columnas). **No lo edites a mano**: corre
> `python scripts/export_schema_doc.py`. Una prueba (`tests/unit/test_schema_doc.py`) falla si este
> documento no coincide con el codigo.

Motor: SQLite en modo **WAL** (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`), una sola conexion
`aiosqlite` con un candado de escritura. Las fechas son texto ISO-8601 en UTC; los tiempos de velas,
enteros en milisegundos Unix. Para respaldar o mudar la base usa `scripts/backup_db.py` y
`scripts/restore_db.py` (nunca copies el archivo a mano: en modo WAL puede quedar inconsistente).

**28 tablas**, 13 indices explicitos y 1 disparador(es).

## Indice por tema

- **Cuenta paper, riesgo y control**: [`trades`](#trades), [`system_state`](#system_state), [`risk_rejections`](#risk_rejections), [`paper_execution_log`](#paper_execution_log), [`control_audit`](#control_audit)
- **Senales, sombras y medicion del LLM**: [`signals`](#signals), [`signals_discarded_by_sl_cap`](#signals_discarded_by_sl_cap), [`shadow_trades`](#shadow_trades), [`llm_logs`](#llm_logs)
- **Mercado, universo y salud de las fuentes**: [`ohlcv_cache`](#ohlcv_cache), [`ohlcv_floor`](#ohlcv_floor), [`ohlcv_series_complete`](#ohlcv_series_complete), [`ohlcv_unrepairable_gaps`](#ohlcv_unrepairable_gaps), [`funding_cache`](#funding_cache), [`contract_specs_cache`](#contract_specs_cache), [`asset_universe`](#asset_universe), [`data_source_health`](#data_source_health)
- **Backtest (Fase 2)**: [`backtest_runs`](#backtest_runs), [`backtest_trades`](#backtest_trades), [`backtest_skipped_entries`](#backtest_skipped_entries), [`backtest_verdicts`](#backtest_verdicts)
- **Noticias (Fase 4)**: [`news_items`](#news_items), [`news_fetch_state`](#news_fetch_state), [`fear_greed`](#fear_greed)
- **Memoria de lecciones (Fase 4)**: [`memory_entries`](#memory_entries), [`lessons`](#lessons), [`lesson_applications`](#lesson_applications), [`lessons_reports`](#lessons_reports)

## Cuenta paper, riesgo y control

Las operaciones de la cuenta simulada y todo lo que las rodea: el estado del sistema (pausa, kill switch, breaker), los rechazos del motor de riesgo, el registro del brazo ejecutado y la auditoria de los controles.

### trades

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `symbol` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `strategy` | TEXT | si |  |  |
| `status` | TEXT | no | 'OPEN' |  |
| `leverage` | INTEGER | no |  |  |
| `margin_usdt` | REAL | no |  |  |
| `notional_usdt` | REAL | no |  |  |
| `qty` | REAL | no |  |  |
| `entry_price` | REAL | no |  |  |
| `exit_price` | REAL | si |  |  |
| `fee_entry_usdt` | REAL | no | 0 |  |
| `fee_exit_usdt` | REAL | si |  |  |
| `funding_paid_usdt` | REAL | no | 0 |  |
| `pnl_gross_usdt` | REAL | si |  |  |
| `pnl_net_usdt` | REAL | si |  |  |
| `close_reason` | TEXT | si |  |  |
| `opened_at` | TEXT | no |  |  |
| `closed_at` | TEXT | si |  |  |
| `decision_json` | TEXT | si |  |  |
| `sl_price` | REAL | si |  |  |
| `tp_price` | REAL | si |  |  |
| `trailing_distance` | REAL | si |  |  |
| `effective_stop` | REAL | si |  |  |
| `best_price` | REAL | si |  |  |
| `liq_price` | REAL | si |  |  |
| `sl_margin_loss_pct` | REAL | si |  |  |
| `funding_is_approximated` | INTEGER | no | 0 |  |
| `funding_last_applied_ms` | INTEGER | si |  |  |
| `decision_source` | TEXT | si |  |  |
| `slippage_entry_usdt` | REAL | no | 0 |  |
| `slippage_exit_usdt` | REAL | no | 0 |  |
| `fill_source` | TEXT | si |  |  |
| `reconciliation_failed` | INTEGER | no | 0 |  |
| `reconciliation_attempts` | INTEGER | no | 0 |  |
| `reconciliation_window_start_ms` | INTEGER | si |  |  |
| `reconciliation_first_failed_at_ms` | INTEGER | si |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`
- `status IN ('OPEN', 'CLOSED')`

Indices:

- `idx_trades_symbol_status` (no unico): symbol, status

### system_state

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `key` | TEXT | no |  | PK1 |
| `value` | TEXT | no |  |  |
| `updated_at` | TEXT | no |  |  |

### risk_rejections

Cada rechazo del motor de riesgo en vivo (Fase 3, subfase 3.2) a una entrada nueva, con el motivo y los valores exactos involucrados, para poder auditarlo despues. Nunca se usa para cierres (SL/TP/liquidacion ni cierre manual) -- el motor de riesgo solo bloquea entradas nuevas.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `created_at` | TEXT | no |  |  |
| `symbol` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `strategy` | TEXT | si |  |  |
| `reason` | TEXT | no |  |  |
| `details_json` | TEXT | no |  |  |

Indices:

- `idx_risk_rejections_reason` (no unico): reason

### paper_execution_log

Brazo ejecutado de la cuenta PAPER (subfase 3.9, docs/FASE3_9_BRAZO_EJECUTADO.md): una fila por cada decision APROBADA que el brazo considero, abra o no. `signal_group_key` es UNICA (reclamo idempotente: un grupo nunca abre dos veces). `outcome` PENDING solo dura el instante entre el reclamo y la apertura (o hasta que el arranque lo resuelve); `reason` explica un SKIPPED (LATE_DECISION, SCHEDULER_PAUSED, POSITION_ALREADY_OPEN, NO_CONTRACT_SPEC, BELOW_MIN_ORDER, LEVELS_INVALID, RISK_<codigo>, OPEN_ERROR, ABORTED_RESTART). Sin PnL ni ningun resultado: solo la decision de ejecucion, con sus detalles en `details_json`.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `signal_group_key` | TEXT | no |  |  |
| `created_at` | TEXT | no |  |  |
| `resolved_at` | TEXT | si |  |  |
| `shadow_trade_id` | INTEGER | si |  |  |
| `symbol` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `strategy` | TEXT | si |  |  |
| `decision_delay_s` | REAL | no |  |  |
| `outcome` | TEXT | no |  |  |
| `reason` | TEXT | si |  |  |
| `details_json` | TEXT | si |  |  |
| `trade_id` | INTEGER | si |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`
- `outcome IN ('PENDING', 'OPENED', 'SKIPPED')`

Indices:

- `idx_paper_execution_log_outcome` (no unico): outcome, reason
- `sqlite_autoindex_paper_execution_log_1` (unico): signal_group_key

### control_audit

Fase 5 (docs/FASE5_PLAN.md): rastro auditable de cada accion de control (pausar, reanudar, kill switch) pedida por el dashboard o por Telegram: quien, cuando y el estado antes/despues. No guarda datos del chat de Telegram ni ningun resultado de operaciones.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `created_at` | TEXT | no |  |  |
| `channel` | TEXT | no |  |  |
| `actor` | TEXT | no |  |  |
| `action` | TEXT | no |  |  |
| `paused_before` | INTEGER | no |  |  |
| `kill_before` | INTEGER | no |  |  |
| `paused_after` | INTEGER | no |  |  |
| `kill_after` | INTEGER | no |  |  |

Restricciones `CHECK`:

- `channel IN ('DASHBOARD', 'TELEGRAM')`
- `action IN ('pause', 'resume', 'kill')`

Indices:

- `idx_control_audit_created` (no unico): created_at

## Senales, sombras y medicion del LLM

La medicion en vivo del valor del LLM: cada evaluacion de estrategia, las operaciones sombra que se simulan por cada senal y el registro completo de cada consulta al LLM.

### signals

Generador de senales en vivo (Fase 3, subfase 3.3) -- una fila por CADA evaluacion (simbolo, estrategia, timeframe, vela cerrada), HOLD incluido (registro auditable completo, ver `app/trading/signal_generator.py`). `UNIQUE` + `INSERT OR IGNORE` evita duplicar la fila si el scheduler vuelve a evaluar la misma vela ya cerrada en un poll posterior. `sl_margin_loss_pct` NO esta en la lista de columnas de docs/FASE3_PLAN.md punto 1, pero se agrega aqui para auditar el MISMO numero que decide `status`/`signals_discarded_by_sl_cap` sin tener que recalcularlo despues.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `symbol` | TEXT | no |  |  |
| `strategy` | TEXT | no |  |  |
| `is_experimental` | INTEGER | no | 0 |  |
| `timeframe` | TEXT | no |  |  |
| `candle_close_time` | TEXT | no |  |  |
| `evaluated_at` | TEXT | no |  |  |
| `signal` | TEXT | no |  |  |
| `price_at_eval` | REAL | no |  |  |
| `stop_price` | REAL | si |  |  |
| `take_profit_price` | REAL | si |  |  |
| `trailing_distance` | REAL | si |  |  |
| `funding_rate_pct` | REAL | si |  |  |
| `funding_is_approximated` | INTEGER | no | 0 |  |
| `sl_margin_loss_pct` | REAL | si |  |  |
| `indicators_json` | TEXT | si |  |  |
| `status` | TEXT | si |  |  |
| `reason` | TEXT | si |  |  |

Restricciones `CHECK`:

- `signal IN ('LONG', 'SHORT', 'HOLD')`

Indices:

- `idx_signals_actionable` (no unico): symbol, signal, candle_close_time
- `sqlite_autoindex_signals_1` (unico): symbol, strategy, timeframe, candle_close_time

### signals_discarded_by_sl_cap

Senal accionable (LONG/SHORT) cuyo `sl_margin_loss_pct` supero `LIVE_SL_MARGIN_CAP_PCT` -- se descarta ANTES de cualquier simulacion o decision del LLM (docs/FASE3_PLAN.md punto 2). `signal_id` referencia la fila ya existente en `signals` (que queda con `status='DISCARDED_SL_CAP'`, nunca se borra -- sigue siendo el registro auditable completo).

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `signal_id` | INTEGER | no |  |  |
| `symbol` | TEXT | no |  |  |
| `strategy` | TEXT | no |  |  |
| `timeframe` | TEXT | no |  |  |
| `candle_close_time` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `sl_margin_loss_pct` | REAL | no |  |  |
| `cap_pct` | REAL | no |  |  |
| `created_at` | TEXT | no |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`

### shadow_trades

Operaciones sombra (subfase 3.5, docs/FASE3_PLAN.md punto 2): una por senal agrupada (simbolo, direccion, vela), simuladas con margen ilimitado. `signal_group_key` es UNICA: la misma senal agrupada nunca genera dos operaciones sombra. `llm_decision` queda SIN_LLM hasta la subfase 3.6 (APROBADA/RECHAZADA cuando exista el LLM).

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `symbol` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `strategy` | TEXT | no |  |  |
| `contributing_strategies` | TEXT | no |  |  |
| `signal_group_key` | TEXT | no |  |  |
| `candle_close_time` | TEXT | no |  |  |
| `status` | TEXT | no | 'OPEN' |  |
| `leverage` | INTEGER | no |  |  |
| `margin_usdt` | REAL | no |  |  |
| `notional_usdt` | REAL | no |  |  |
| `qty` | REAL | no |  |  |
| `entry_price` | REAL | no |  |  |
| `exit_price` | REAL | si |  |  |
| `fee_entry_usdt` | REAL | no | 0 |  |
| `fee_exit_usdt` | REAL | si |  |  |
| `funding_paid_usdt` | REAL | no | 0 |  |
| `slippage_entry_usdt` | REAL | no | 0 |  |
| `slippage_exit_usdt` | REAL | no | 0 |  |
| `pnl_gross_usdt` | REAL | si |  |  |
| `pnl_net_usdt` | REAL | si |  |  |
| `close_reason` | TEXT | si |  |  |
| `fill_source` | TEXT | si |  |  |
| `opened_at` | TEXT | no |  |  |
| `closed_at` | TEXT | si |  |  |
| `sl_price` | REAL | si |  |  |
| `tp_price` | REAL | si |  |  |
| `trailing_distance` | REAL | si |  |  |
| `effective_stop` | REAL | si |  |  |
| `best_price` | REAL | si |  |  |
| `liq_price` | REAL | si |  |  |
| `sl_margin_loss_pct` | REAL | si |  |  |
| `funding_is_approximated` | INTEGER | no | 0 |  |
| `funding_last_applied_ms` | INTEGER | si |  |  |
| `llm_decision` | TEXT | no | 'SIN_LLM' |  |
| `executed_in_real_account` | INTEGER | no | 0 |  |
| `reconciliation_failed` | INTEGER | no | 0 |  |
| `reconciliation_attempts` | INTEGER | no | 0 |  |
| `reconciliation_window_start_ms` | INTEGER | si |  |  |
| `reconciliation_first_failed_at_ms` | INTEGER | si |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`
- `status IN ('OPEN', 'CLOSED')`
- `llm_decision IN ('APROBADA', 'RECHAZADA', 'SIN_LLM')`

Indices:

- `idx_shadow_trades_status` (no unico): status, symbol
- `sqlite_autoindex_shadow_trades_1` (unico): signal_group_key

### llm_logs

Decisiones del LLM sobre cada senal agrupada (subfase 3.6, docs/FASE3_6_LLM.md seccion c). Una fila por llamada, exitosa o no. `signal_group_key` es UNICA: una sola llamada por grupo, sin reintentos. `fase` separa el piloto (excluido del analisis) de la medicion.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `created_at` | TEXT | no |  |  |
| `signal_group_key` | TEXT | no |  |  |
| `shadow_trade_id` | INTEGER | si |  |  |
| `fase` | TEXT | no |  |  |
| `candle_close_time` | TEXT | no |  |  |
| `decision_delay_s` | REAL | no |  |  |
| `hour_utc` | INTEGER | no |  |  |
| `atr_pct` | REAL | si |  |  |
| `model` | TEXT | no |  |  |
| `prompt_version` | TEXT | no |  |  |
| `prompt_sha256` | TEXT | no |  |  |
| `prompt` | TEXT | no |  |  |
| `response_raw` | TEXT | si |  |  |
| `status` | TEXT | no |  |  |
| `error` | TEXT | si |  |  |
| `decision` | TEXT | si |  |  |
| `input_tokens` | INTEGER | si |  |  |
| `output_tokens` | INTEGER | si |  |  |
| `cost_usd` | REAL | no | 0 |  |
| `latency_ms` | INTEGER | si |  |  |
| `json_repaired` | INTEGER | no | 0 |  |
| `features_version` | TEXT | si |  |  |

Restricciones `CHECK`:

- `fase IN ('PILOTO', 'MEDICION')`
- `status IN ('OK', 'TIMEOUT', 'ERROR_HTTP', 'INVALID', 'BUDGET_EXCEEDED')`
- `decision IN ('APROBAR', 'RECHAZAR')`

Indices:

- `idx_llm_logs_created_at` (no unico): created_at
- `sqlite_autoindex_llm_logs_1` (unico): signal_group_key

## Mercado, universo y salud de las fuentes

Datos de mercado en cache (velas, funding, contratos), el universo dinamico de activos y la salud de cada fuente de datos.

### ohlcv_cache

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK1 |
| `interval` | TEXT | no |  | PK2 |
| `price_type` | TEXT | no |  | PK3 |
| `open_time` | INTEGER | no |  | PK4 |
| `open` | REAL | no |  |  |
| `high` | REAL | no |  |  |
| `low` | REAL | no |  |  |
| `close` | REAL | no |  |  |
| `base_vol` | REAL | si |  |  |
| `quote_vol` | REAL | si |  |  |

### ohlcv_floor

Recuerda que ya se alcanzo el piso real del historial de Bitunix para un (symbol, interval, price_type) -- evita redescargar todo cada vez que se pide un start_time anterior al dato mas antiguo disponible.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK1 |
| `interval` | TEXT | no |  | PK2 |
| `price_type` | TEXT | no |  | PK3 |
| `floor_open_time` | INTEGER | no |  |  |

### ohlcv_series_complete

Marca afirmativa de "esta serie se descargo completa" escrita por scripts/download_history.py al terminar cada serie SIN errores -- corrige un bug real donde `ohlcv_floor` podia no tener fila (p.ej. una descarga anterior interrumpida) aunque el rango pedido SI estuviera completo, causando un MissingHistoricalDataError evitable. Es una señal mas fuerte que inferir a partir de `ohlcv_floor` + el rango cacheado: solo existe si `download_history.py` llego al final de esa serie sin lanzar ninguna excepcion. `get_cached_or_raise` la usa para el extremo "cabeza" (dato antiguo) ademas del chequeo de piso existente, no lo reemplaza.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK1 |
| `interval` | TEXT | no |  | PK2 |
| `price_type` | TEXT | no |  | PK3 |
| `start_time` | INTEGER | no |  |  |
| `end_time` | INTEGER | no |  |  |
| `completed_at` | TEXT | no |  |  |

### ohlcv_unrepairable_gaps

Huecos puntuales que `fill_gaps` ya intento reparar con un pedido estrecho y SIGUEN sin aparecer (incidente de estabilidad 2026-10-10, "Añadido 0" -- ver docs/FASE2_INTEGRIDAD_VELAS.md). Evita que `_verify_and_repair` los reintente contra la API en cada ciclo del generador de señales: los vuelve a intentar solo despues de `GAP_RETRY_COOLDOWN_HOURS` (app/market/ ohlcv_history.py), y se borra la fila si un reintento SI lo recupera.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK1 |
| `interval` | TEXT | no |  | PK2 |
| `price_type` | TEXT | no |  | PK3 |
| `open_time` | INTEGER | no |  | PK4 |
| `first_seen_at` | TEXT | no |  |  |
| `last_attempted_at` | TEXT | no |  |  |
| `attempts` | INTEGER | no | 1 |  |

### funding_cache

Cache de funding_rate_history (igual proposito que ohlcv_cache): evita re-descargar en cada corrida/reintento del backtest. `floor_time` (en `ohlcv_floor` con interval='__funding__', price_type='__funding__' para reusar la misma tabla de piso) marca cuando se alcanzo el principio real del historial de funding de Bitunix (mas corto que el de velas, ver docs/FASE2_PLAN.md).

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK1 |
| `funding_time` | INTEGER | no |  | PK2 |
| `funding_rate` | REAL | no |  |  |

### contract_specs_cache

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK1 |
| `min_trade_volume` | REAL | si |  |  |
| `base_precision` | INTEGER | si |  |  |
| `quote_precision` | INTEGER | si |  |  |
| `min_leverage` | INTEGER | si |  |  |
| `max_leverage` | INTEGER | si |  |  |
| `default_margin_mode` | TEXT | si |  |  |
| `margin_tiers_json` | TEXT | si |  |  |
| `funding_rate` | REAL | si |  |  |
| `funding_interval_hours` | INTEGER | si |  |  |
| `next_funding_time` | INTEGER | si |  |  |
| `fetched_at` | TEXT | no |  |  |

### asset_universe

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `refreshed_at` | TEXT | no |  |  |
| `coingecko_id` | TEXT | no |  |  |
| `symbol` | TEXT | no |  |  |
| `coingecko_rank` | INTEGER | si |  |  |
| `market_cap_usd` | REAL | si |  |  |
| `excluded_category` | TEXT | si |  |  |
| `excluded_manual` | INTEGER | no | 0 |  |
| `has_bitunix_perp` | INTEGER | no | 0 |  |
| `price_sanity_ok` | INTEGER | si |  |  |
| `included` | INTEGER | no | 0 |  |

Indices:

- `idx_asset_universe_refreshed` (no unico): refreshed_at

### data_source_health

Salud de las fuentes de datos en vivo (subfase 3.4, punto 7 de docs/FASE3_PLAN.md): ultimo exito, ultimo error y fallos seguidos. Hoy la fuente `ws_feed` es el latido del WebSocket publico (cualquier mensaje del servidor, incluido el pong) -- si se queda sin latido, no se abren entradas.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `source` | TEXT | no |  | PK1 |
| `last_success_at` | TEXT | si |  |  |
| `last_error` | TEXT | si |  |  |
| `consecutive_failures` | INTEGER | no | 0 |  |

## Backtest (Fase 2)

Resultados historicos de las estrategias por reglas. No es la medicion en vivo del LLM.

### backtest_runs

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `strategy` | TEXT | no |  |  |
| `symbol` | TEXT | no |  |  |
| `timeframe` | TEXT | no |  |  |
| `segment` | TEXT | no |  |  |
| `winrate` | REAL | si |  |  |
| `profit_factor` | REAL | si |  |  |
| `pnl_gross_total_usdt` | REAL | no | 0 |  |
| `pnl_net_total_usdt` | REAL | no | 0 |  |
| `max_drawdown_pct` | REAL | si |  |  |
| `expectancy_usdt` | REAL | si |  |  |
| `total_trades` | INTEGER | no | 0 |  |
| `fees_total_usdt` | REAL | no | 0 |  |
| `funding_total_usdt` | REAL | no | 0 |  |
| `funding_real_trades` | INTEGER | no | 0 |  |
| `funding_approx_trades` | INTEGER | no | 0 |  |
| `benchmark_return_pct` | REAL | si |  |  |
| `benchmark_max_drawdown_pct` | REAL | si |  |  |
| `run_at` | TEXT | no |  |  |

Indices:

- `idx_backtest_runs_cell` (no unico): strategy, symbol, timeframe, segment

### backtest_trades

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `strategy` | TEXT | no |  |  |
| `symbol` | TEXT | no |  |  |
| `timeframe` | TEXT | no |  |  |
| `segment` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `entry_time` | TEXT | no |  |  |
| `exit_time` | TEXT | no |  |  |
| `entry_price` | REAL | no |  |  |
| `exit_price` | REAL | no |  |  |
| `qty` | REAL | no |  |  |
| `margin_usdt` | REAL | no |  |  |
| `leverage` | INTEGER | no |  |  |
| `fee_entry_usdt` | REAL | no |  |  |
| `fee_exit_usdt` | REAL | no |  |  |
| `slippage_cost_usdt` | REAL | no |  |  |
| `funding_paid_usdt` | REAL | no |  |  |
| `funding_is_approximated` | INTEGER | no |  |  |
| `pnl_gross_usdt` | REAL | no |  |  |
| `pnl_net_usdt` | REAL | no |  |  |
| `close_reason` | TEXT | no |  |  |
| `sl_margin_loss_pct` | REAL | si |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`

Indices:

- `idx_backtest_trades_cell` (no unico): strategy, symbol, timeframe, segment

### backtest_skipped_entries

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `strategy` | TEXT | no |  |  |
| `symbol` | TEXT | no |  |  |
| `timeframe` | TEXT | no |  |  |
| `segment` | TEXT | no |  |  |
| `ts` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `intended_sl_margin_loss_pct` | REAL | no |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`

### backtest_verdicts

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `strategy` | TEXT | no |  |  |
| `is_experimental` | INTEGER | no | 0 |  |
| `combos_tested` | INTEGER | no | 0 |  |
| `total_trades_all_segments` | INTEGER | no | 0 |  |
| `pf_oos_aggregate` | REAL | si |  |  |
| `pct_symbols_pf_gt1` | REAL | si |  |  |
| `pct_folds_positive` | REAL | si |  |  |
| `pf_stressed` | REAL | si |  |  |
| `pf_real_funding_only` | REAL | si |  |  |
| `pf_full_period_approx` | REAL | si |  |  |
| `max_drawdown_oos_pct` | REAL | si |  |  |
| `concentration_pct` | REAL | si |  |  |
| `pf_control_group` | REAL | si |  |  |
| `evidence_insufficient` | INTEGER | no | 0 |  |
| `discarded` | INTEGER | no | 0 |  |
| `discard_reasons_json` | TEXT | si |  |  |
| `pf_is_aggregate` | REAL | si |  |  |
| `is_trades_count` | INTEGER | no | 0 |  |
| `oos_trades_count` | INTEGER | no | 0 |  |
| `portfolio_simulation_runs` | INTEGER | no | 0 |  |
| `portfolio_final_capital_median` | REAL | si |  |  |
| `portfolio_final_capital_p10` | REAL | si |  |  |
| `portfolio_final_capital_p90` | REAL | si |  |  |
| `portfolio_max_drawdown_median` | REAL | si |  |  |
| `portfolio_max_drawdown_p10` | REAL | si |  |  |
| `portfolio_max_drawdown_p90` | REAL | si |  |  |
| `portfolio_mtm_max_drawdown_median` | REAL | si |  |  |
| `portfolio_mtm_max_drawdown_p10` | REAL | si |  |  |
| `portfolio_mtm_max_drawdown_p90` | REAL | si |  |  |
| `portfolio_concentration_pct_median` | REAL | si |  |  |
| `portfolio_trades_included_median` | REAL | si |  |  |
| `portfolio_trades_skipped_no_margin_median` | REAL | si |  |  |
| `portfolio_oos_simulation_runs` | INTEGER | no | 0 |  |
| `portfolio_oos_final_capital_median` | REAL | si |  |  |
| `portfolio_oos_final_capital_p10` | REAL | si |  |  |
| `portfolio_oos_final_capital_p90` | REAL | si |  |  |
| `portfolio_oos_max_drawdown_median` | REAL | si |  |  |
| `portfolio_oos_max_drawdown_p10` | REAL | si |  |  |
| `portfolio_oos_max_drawdown_p90` | REAL | si |  |  |
| `portfolio_oos_mtm_max_drawdown_median` | REAL | si |  |  |
| `portfolio_oos_mtm_max_drawdown_p10` | REAL | si |  |  |
| `portfolio_oos_mtm_max_drawdown_p90` | REAL | si |  |  |
| `portfolio_oos_concentration_pct_median` | REAL | si |  |  |
| `portfolio_oos_trades_included_median` | REAL | si |  |  |
| `portfolio_oos_trades_skipped_no_margin_median` | REAL | si |  |  |
| `run_at` | TEXT | no |  |  |
| `portfolio_max_drawdown_pct` | REAL | si |  |  |
| `portfolio_concentration_pct` | REAL | si |  |  |
| `portfolio_final_capital_usdt` | REAL | si |  |  |
| `portfolio_trades_included` | INTEGER | no | 0 |  |
| `portfolio_trades_skipped_no_margin` | INTEGER | no | 0 |  |

## Noticias (Fase 4)

Titulares recolectados (texto NO confiable), estado de cada fuente e indice Fear & Greed. Ninguna operacion depende de ellos.

### news_items

Fase 4 (docs/FASE4_PLAN.md), todo aditivo y fuera del prompt congelado de la medicion.  Noticias recolectadas (app/news/): SOLO titulo, URL, dominio y fecha -- nunca el cuerpo. `url_hash` es UNICO (la misma noticia no se guarda dos veces); un titular casi duplicado se guarda con `dup_of` y se excluye del contexto. `fetched_at` es cuando LO VIO el bot: el contexto para una decision exige `fetched_at <= instante` (sin mirar al futuro).

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `source` | TEXT | no |  |  |
| `fetched_at` | TEXT | no |  |  |
| `published_at` | TEXT | si |  |  |
| `domain` | TEXT | si |  |  |
| `title` | TEXT | no |  |  |
| `url` | TEXT | no |  |  |
| `url_hash` | TEXT | no |  |  |
| `title_hash` | TEXT | no |  |  |
| `language` | TEXT | si |  |  |
| `source_country` | TEXT | si |  |  |
| `assets_json` | TEXT | no | '[]' |  |
| `query_tag` | TEXT | si |  |  |
| `sentiment` | REAL | si |  |  |
| `sentiment_method` | TEXT | si |  |  |
| `dup_of` | INTEGER | si |  |  |

Indices:

- `idx_news_items_fetched` (no unico): fetched_at
- `idx_news_items_published` (no unico): published_at
- `sqlite_autoindex_news_items_1` (unico): url_hash

### news_fetch_state

Estado de la consulta condicional por fuente (ETag / Last-Modified) y cuando se puede volver a pedir (intervalo minimo, enfriamiento tras un 429). La salud (ultimo exito, fallos seguidos) vive en `data_source_health` con la fuente `news:<id>`.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `source` | TEXT | no |  | PK1 |
| `etag` | TEXT | si |  |  |
| `last_modified` | TEXT | si |  |  |
| `last_attempt_at` | TEXT | si |  |  |
| `next_allowed_at` | TEXT | si |  |  |
| `last_status` | INTEGER | si |  |  |
| `last_items` | INTEGER | si |  |  |

### fear_greed

Fear & Greed Index (alternative.me): dato de mercado diario. Atribucion obligatoria junto a cualquier muestra del dato.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `ts` | TEXT | no |  | PK1 |
| `value` | INTEGER | no |  |  |
| `classification` | TEXT | si |  |  |
| `fetched_at` | TEXT | no |  |  |

## Memoria de lecciones (Fase 4)

Operaciones cerradas convertidas en entradas de memoria, las lecciones estadisticas derivadas, su registro de aplicacion y los informes (post-mortem, calibracion). Todo lo que lleva resultados se muestra solo tras el registro oficial del veredicto.

### memory_entries

Memoria de lecciones (app/memory/): una fila INMUTABLE por operacion CERRADA y fiable (sombra cerrada con PnL), con lo que se sabia al decidir y el resultado. El PnL vive aqui pero nunca se muestra por API/Telegram/dashboard sin `is_outcome_reveal_allowed`.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `signal_group_key` | TEXT | no |  |  |
| `shadow_trade_id` | INTEGER | si |  |  |
| `built_at` | TEXT | no |  |  |
| `symbol` | TEXT | no |  |  |
| `side` | TEXT | no |  |  |
| `strategy_set` | TEXT | no |  |  |
| `timeframe` | TEXT | si |  |  |
| `decision_time` | TEXT | no |  |  |
| `closed_at` | TEXT | no |  |  |
| `hour_utc` | INTEGER | si |  |  |
| `atr_pct` | REAL | si |  |  |
| `risk_reward` | REAL | si |  |  |
| `sl_margin_loss_pct` | REAL | si |  |  |
| `llm_label` | TEXT | no |  |  |
| `news_sentiment` | REAL | si |  |  |
| `news_count` | INTEGER | si |  |  |
| `fear_greed` | INTEGER | si |  |  |
| `pnl_net_usdt` | REAL | no |  |  |
| `margin_usdt` | REAL | no |  |  |
| `return_pct` | REAL | no |  |  |
| `outcome` | TEXT | no |  |  |
| `close_reason` | TEXT | si |  |  |
| `duration_hours` | REAL | si |  |  |

Restricciones `CHECK`:

- `side IN ('LONG', 'SHORT')`
- `outcome IN ('WIN', 'LOSS')`

Indices:

- `idx_memory_entries_closed` (no unico): closed_at
- `sqlite_autoindex_memory_entries_1` (unico): signal_group_key

### lessons

Lecciones agregadas por version de reconstruccion (`build_id`). Solo es `ACTIVE` una leccion con muestra minima, intervalo que excluye la tasa base y signo estable en las dos mitades del tiempo.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `build_id` | TEXT | no |  |  |
| `built_at` | TEXT | no |  |  |
| `dimension` | TEXT | no |  |  |
| `pattern` | TEXT | no |  |  |
| `n` | INTEGER | no |  |  |
| `wins` | INTEGER | no |  |  |
| `winrate` | REAL | si |  |  |
| `ci_low` | REAL | si |  |  |
| `ci_high` | REAL | si |  |  |
| `base_winrate` | REAL | si |  |  |
| `mean_return_pct` | REAL | si |  |  |
| `half1_delta` | REAL | si |  |  |
| `half2_delta` | REAL | si |  |  |
| `status` | TEXT | no |  |  |

Restricciones `CHECK`:

- `status IN ('ACTIVE', 'INSUFFICIENT', 'UNSTABLE', 'NOT_SIGNIFICANT')`

Indices:

- `sqlite_autoindex_lessons_1` (unico): build_id, dimension, pattern

### lesson_applications

Que lecciones se inyectaron en cada decision (para auditar su influencia mas adelante).

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `signal_group_key` | TEXT | no |  |  |
| `created_at` | TEXT | no |  |  |
| `build_id` | TEXT | si |  |  |
| `lesson_ids_json` | TEXT | no | '[]' |  |
| `n_similar` | INTEGER | no | 0 |  |
| `evidence` | TEXT | no |  |  |

Indices:

- `sqlite_autoindex_lesson_applications_1` (unico): signal_group_key

### lessons_reports

Post-mortem de rachas perdedoras y tablas de calibracion; `dedupe_key` evita repetir el mismo.

| Columna | Tipo | Nulo | Por defecto | Clave |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK1 |
| `kind` | TEXT | no |  |  |
| `created_at` | TEXT | no |  |  |
| `dedupe_key` | TEXT | si |  |  |
| `body_json` | TEXT | no |  |  |

Restricciones `CHECK`:

- `kind IN ('POSTMORTEM', 'CALIBRATION')`

Indices:

- `sqlite_autoindex_lessons_reports_1` (unico): dedupe_key

## Disparadores

### llm_decision_inmutable

Sobre la tabla `shadow_trades`.

```sql
CREATE TRIGGER llm_decision_inmutable
BEFORE UPDATE OF llm_decision ON shadow_trades
WHEN OLD.llm_decision <> 'SIN_LLM' AND NEW.llm_decision <> OLD.llm_decision
BEGIN
    SELECT RAISE(ABORT, 'llm_decision es inmutable una vez decidida');
END
```
