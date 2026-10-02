# Fase 3 -- Gestión de riesgo, ejecución realista en paper trading y medición del valor del LLM

> Plan propuesto por el asistente, documento únicamente -- sin código hasta
> que se apruebe el contenido de este plan (distinto de la aprobación del
> propio proceso de planificación en Plan Mode, ya obtenida). Investigación
> de respaldo: dos agentes Explore (hechos verbatim de `docs/SPEC.md` /
> `docs/FASE0.md`, y del código actual de `app/execution/`,
> `app/backtesting/engine.py`, `app/core/scheduler.py`,
> `app/market/bitunix_ws.py`), lectura directa de `app/config.py`,
> `docs/FASE2_CRITERIOS.md`, `docs/FASE2_RESULTADOS.md`,
> `app/backtesting/risk_analysis.py`, y conteos reales leídos en modo
> solo-lectura de `data/minerva.db`.

## Contexto

Fase 2 cerró sin una estrategia por reglas que supere los criterios
congelados (`docs/FASE2_RESULTADOS.md`): la mejor (`ema_cross_9_21`, PF OOS
1.12) queda por debajo del mínimo (1.2), y la simulación de cartera muestra
que, al tamaño de posición actual (10 USDT margen, 3 simultáneas), 3 de las
4 estrategias no experimentales arruinan una cuenta de 100 USDT en la
mayoría de las corridas. Esto confirma lo que `docs/SPEC.md` ya preveía:
ninguna estrategia de reglas aislada debe operar sola con capital real ni
simulado sin más filtros -- la decisión final depende de la confluencia de
señales MÁS un LLM.

Por eso esta fase ya no puede limitarse a "motor de riesgo + ejecución" (el
alcance original de "Fase 3" en `docs/FASE0.md`) sin absorber también la
capa de decisión del LLM (antes rotulada "Fase 4" en esos documentos): el
segundo encargo de esta fase es diseñar cómo medir el valor del LLM, y eso
es imposible sin que el LLM esté realmente tomando decisiones sobre el
brazo B. Esta fusión de alcance se declara aquí explícitamente, no es un
cambio silencioso -- `docs/FASE0.md`/`SPEC.md` quedan superados en la
numeración de fases a partir de este plan.

Todos los valores de riesgo propuestos abajo se apoyan en tres fuentes ya
existentes: (1) los parámetros ya fijados en `app/config.py` desde Fase 0/1
(no se inventan de nuevo), (2) la metodología de bootstrap de
`app/backtesting/risk_analysis.py` (cuya salida numérica real,
`docs/FASE2_RIESGO.md`, todavía no existe porque falta correr
`scripts/analyze_risk.py` -- este plan referencia su metodología, nunca
inventa los números que falta generar), y (3) conteos reales leídos de
`data/minerva.db` en modo solo-lectura durante esta investigación (10361
operaciones distintas entre las 6 estrategias registradas, sobre 10
símbolos, abril 2022-octubre 2026: `ema_cross_9_21` 659, `trend_atr_stop_
9_21_50` 1278, `mean_reversion_rsi14_bb20` 5994, `donchian_breakout_20`
1913, `funding_contrarian_experimental` 154, `funding_contrarian_
percentile_experimental` 363 -- ~6.4 operaciones/día combinadas, usado en
el punto 6 para el costo del LLM).

## 1. Generador de señales (`signals`)

**Qué cambia:** `app/core/scheduler.py::_evaluate_symbol` hoy solo evalúa
UNA estrategia (`settings.active_strategy`) sobre UN símbolo fijo
(`settings.symbols`). Se reemplaza por un generador que, en cada tick del
scheduler (mismo `scheduler_poll_seconds`, sin cambiar la cadencia), itera:

- Todos los símbolos del universo vigente (`asset_universe` con
  `included=1` -- ya existe desde Fase 2, no `settings.symbols`, que es
  vestigio mono-símbolo de Fase 1).
- Todas las estrategias de `app/strategies/registry.py::STRATEGIES` (las 6,
  incluidas las 2 de `EXPERIMENTAL_STRATEGIES`), cada una en los timeframes
  de `STRATEGY_TIMEFRAMES`.
- Llama `strategy.precompute(df)` + `strategy.evaluate(df)` igual que el
  motor de backtest (mismo código de indicadores, cero lógica nueva ahí).

**Tabla nueva `signals`** (una fila por evaluación, incluido HOLD -- ver
razón abajo): `id, symbol, strategy, is_experimental, timeframe,
candle_close_time, evaluated_at, signal (LONG|SHORT|HOLD), price_at_eval,
stop_price, take_profit_price, trailing_distance, funding_rate_pct,
funding_is_approximated, indicators_json, status`. `UNIQUE(symbol,
strategy, timeframe, candle_close_time)` para que re-evaluar la misma vela
cerrada en polls sucesivos no duplique filas (se usa `INSERT OR IGNORE`).
`status` arranca en `PENDING` para señales accionables (LONG/SHORT) y se
actualiza según el punto 2.

Se registran también las HOLD (no solo LONG/SHORT): el volumen es acotado
(mean_reversion a 1h es la más frecuente, ~240 filas/día para 10 símbolos;
el resto a 4h/1d son muchas menos) y tener el registro completo permite
auditar después "por qué no entramos" sin tener que reconstruir el estado
de los indicadores. Si el volumen se vuelve un problema real se agrega una
poda por antigüedad de las filas HOLD -- no antes, no es un problema hoy.

## 2. Tres brazos de comparación (medición del valor del LLM)

Cada señal accionable (LONG/SHORT) de `signals` sigue uno de estos caminos,
y el `status` de la fila registra cuál exactamente:

- **Brazo A (referencia, sin filtro)**: TODA señal accionable, sin
  excepción, se simula como shadow trade con margen/capital ilimitado
  (independiente de la cuenta real) usando el mismo motor del punto 3.
  Tabla nueva `shadow_trades` (mismas columnas de riesgo que `trades`, más
  `arm` y `signal_id`).
- **Brazo B (real)**: el LLM revisa la señal (punto 6); si aprueba Y el
  motor de riesgo (punto 4) no la bloquea, se ejecuta de verdad en
  `PaperBackend`/`trades` -- status `LLM_APPROVED_EXECUTED`. Si el LLM
  aprueba pero el motor de riesgo la bloquea (límite de posiciones, de
  margen, circuit breaker activo), se simula igual en `shadow_trades` con
  `arm='B_BLOCKED'` -- se reporta aparte, nunca se mezcla con un rechazo
  del LLM (sería injusto atribuirle al LLM un resultado que el motor de
  riesgo causó).
- **Brazo C (contraejemplo)**: toda señal que el LLM RECHAZA también se
  simula en `shadow_trades` con `arm='C'` -- qué hubiera pasado si se
  hubiera operado igual.
- **Presupuesto de LLM agotado** (tope de $1/día, punto 6): la señal NO se
  aprueba por defecto -- se trata igual que un rechazo (shadow en C, status
  `SKIPPED_NO_LLM_BUDGET`) para que agotar el presupuesto nunca infle
  artificialmente el brazo B.

**Métrica clave**: profit factor y expectativa (PnL neto promedio) de B
vs. C vs. A, con el número de operaciones de cada brazo siempre reportado
al lado. Umbral de "evidencia baja" por brazo: **30 operaciones** (mismo
`BACKTEST_MIN_TRADES_PER_CELL` ya usado en Fase 2 para no introducir un
segundo número sin justificar) -- por debajo de eso el `/metrics` de
Telegram y el reporte de paso a dinero real (punto 8) muestran la cifra
junto con una advertencia explícita, nunca la ocultan ni la redondean a
"sin datos".

## 3. Un único motor de riesgo compartido (backtest + paper trading)

Ya existe el precedente: `app/execution/pnl.py` (`compute_open_fill`,
`compute_close_result`) y `app/backtesting/liquidation.py`
(`compute_liquidation_price`, `maintenance_margin_rate_for_notional`) son
funciones puras ya compartidas entre el backtest y `PaperBackend`. Lo que
falta extraer es la lógica de SL/TP/trailing/liquidación de
`app/backtesting/engine.py` (`_order_adverse_thresholds`, `_check_adverse`,
`_check_favorable_tp`), hoy privada dentro del motor de backtest y
orientada a VELAS (necesita open/high/low de dos series, LAST_PRICE y
MARK_PRICE, con lógica de "gap al open").

**Diseño**: nuevo módulo `app/trading/stop_engine.py`.

- Se mueven ahí las 3 funciones de `engine.py` **sin cambiar su
  comportamiento** (extracción pura) -- `engine.py` las importa y las sigue
  llamando igual; los tests de backtest existentes deben seguir pasando
  sin tocarlos, confirmando que no hubo regresión.
- Se agregan funciones nuevas en modo TICK (un solo precio, sin gap):
  `check_adverse_tick(is_long, last_price, mark_price, sl_threshold,
  liq_threshold)` y `check_favorable_tp_tick(is_long, price, tp_threshold)`,
  reutilizando `_order_adverse_thresholds` para decidir qué umbral revisar
  primero cuando ambos se cruzan en el mismo tick.
- `app/market/bitunix_ws.py` (cliente WS, ya construido y probado de forma
  aislada en Fase 1 pero nunca conectado -- confirmado: cero referencias
  fuera de su propio archivo) se integra por fin: un loop nuevo en
  `core/scheduler.py` (o un módulo propio `app/core/position_monitor.py`)
  mantiene las posiciones abiertas (reales + `shadow_trades`) y llama el
  stop_engine en modo tick con cada mensaje del canal `tickers`.
- La tabla `trades` (y el modelo `Trade`) se migran (mismo patrón
  idempotente `ALTER TABLE ADD COLUMN` que ya existe para
  `backtest_verdicts`) para cargar el mismo estado de riesgo que
  `BacktestTrade` ya tiene y `Trade` no: `sl_price, tp_price,
  trailing_distance, effective_stop, best_price, liq_price,
  slippage_entry_usdt, slippage_exit_usdt, sl_margin_loss_pct,
  funding_is_approximated`. `PaperBackend.open_position` empieza a llamar
  `strategy.stop_price/.take_profit_price/.trailing_distance` (ya existen
  en `BaseStrategy`, hoy nunca invocados fuera del backtest) y
  `compute_liquidation_price` para poblarlos.
- `PaperBackend.close_position` empieza a pasar `extra_costs_usdt`
  (funding acumulado + slippage) a `compute_close_result`, como ya hace el
  backtest -- hoy se omite, es la brecha concreta que cierra esta fase.
- **Reconciliación al reiniciar** (punto 7) reutiliza las mismas funciones
  en modo VELA (no tick): al arrancar, por cada posición abierta, descarga
  por REST las velas del periodo de caída (`get_kline` desde el último
  tick conocido hasta ahora) y las reproduce por el stop_engine en modo
  vela -- exactamente el mismo camino que ya usa y prueba el backtest,
  aplicado a un rango corto en vez de a la historia completa.

## 4. Gestión de riesgo -- valores propuestos

| Parámetro | Valor propuesto | Por qué |
|---|---|---|
| Margen por operación | **5 USDT** (bajar de los 10 ya configurados) | La simulación de cartera de Fase 2 arruina la cuenta a 10 USDT/3 posiciones en 3 de 4 estrategias por reglas; bajar el margen reduce la exposición por ronda mientras el filtro del LLM (brazo B) todavía no tiene evidencia propia. Revisar al alza solo cuando el punto 8 muestre que B supera a A/C con muestra suficiente. |
| Máximo de posiciones simultáneas | **3** (sin cambio) | Ya fijado en `MAX_SIMULTANEOUS_POSITIONS`; coincide con el techo de la grilla de `risk_analysis.py`, no se introduce un número nuevo. |
| Tope de pérdida del SL sobre el margen | **30%** (el más ajustado de la grilla de `risk_analysis.py`, más estricto que el `MAX_SL_MARGIN_LOSS_PCT=50%` que ya usa el backtest) | Punto de partida conservador; `docs/FASE2_RIESGO.md` (pendiente de correr `scripts/analyze_risk.py`) dirá si 30% o 50% es más razonable para este perfil de retornos -- este plan no inventa ese número, usa el extremo más seguro de la grilla ya definida hasta tener el dato real. |
| Pérdida máxima diaria | **5%** (sin cambio, `MAX_DAILY_LOSS_PCT`) | Ya fijado, sin evidencia en Fase 2 para moverlo. |
| Circuit breaker | **4 pérdidas consecutivas**, cooldown **8h**, **auto-resume** (sin cambio, valores ya en `.env`) | Ya fijados; el auto-resume es apropiado porque es una pausa táctica, no una señal de emergencia. |
| Stop total por drawdown | **20%** (`MAX_DRAWDOWN_PCT`, sin cambio), **resume manual** (no automático) | Distinto del `BACKTEST_MAX_DRAWDOWN_PCT=50%` -- ese es un criterio de descarte de ESTRATEGIA en el backtest; este es el límite de SEGURIDAD de la cuenta en vivo, deliberadamente más estricto y sin auto-resume porque es un evento grave que merece revisión humana. |
| Kill switch manual | Inmediato, **resume manual**, vía `/kill` y `/resume` de Telegram | Pausa el scheduler y marca `system_state.kill_switch_active`; a diferencia del circuit breaker, nunca se levanta solo. |

Todos los valores de esta tabla son `.env`-configurables (ya existen como
settings salvo el tope de SL de este punto, que se agrega como
`LIVE_SL_MARGIN_CAP_PCT`), para poder ajustarlos sin tocar código cuando
`docs/FASE2_RIESGO.md` y la evidencia de paper trading lo justifiquen.

## 5. Bot automático, Telegram solo controla

El bot abre y cierra posiciones solo (generador de señales + LLM + motor de
riesgo, puntos 1-4) -- Telegram nunca decide una entrada o salida, solo
informa y controla el proceso. Nuevo `app/telegram/bot.py` (no existe
ningún archivo hoy, solo placeholders de config: `telegram_bot_token`/
`telegram_chat_id`), vía long-polling (sin necesitar una URL pública/HTTPS
en una PC local, a diferencia de un webhook). Comandos (7 -- extienden los
6 que proponía `docs/FASE0.md`, que no incluía `/kill`):

- `/status` -- resumen: capital, posiciones abiertas, estado del circuit
  breaker/kill switch, gasto de LLM del día.
- `/positions` -- posiciones reales abiertas con PnL flotante.
- `/balance` -- capital actual vs. inicial.
- `/pause` / `/resume` -- pausa/reanuda el scheduler (no cierra posiciones
  abiertas, solo bloquea nuevas entradas).
- `/metrics` -- PF/expectativa de los brazos A/B/C (punto 2) con su tamaño
  de muestra y advertencia de evidencia baja si corresponde.
- `/kill` -- kill switch manual (punto 4).

## 6. Costo del LLM

Modelos (verificados contra la documentación vigente de Anthropic, no
contra el entrenamiento): **Sonnet 5 = `claude-sonnet-5`** (coincide con el
default ya en `app/config.py`) y **Haiku 4.5 = `claude-haiku-4-5-20251001`**
(el default actual en `.env`/`config.py`, `claude-haiku-4-5`, está
incompleto -- le falta el sufijo de fecha; se corrige como parte de esta
fase).

**División de trabajo**: Haiku 4.5 revisa cada señal accionable (aprobar/
rechazar + razón corta, es el camino caliente); Sonnet 5 se reserva para
el reporte periódico de `/metrics` y para escalar casos donde Haiku marca
baja confianza -- mantiene el costo por señal bajo sin sacrificar
profundidad donde importa.

**Estimación de volumen** (dato real, leído de `backtest_trades` en modo
solo lectura): ~6.4 operaciones/día combinadas entre las 6 estrategias
sobre los 10 símbolos actuales, promediado sobre ~4.5 años de historia. Se
usa como ancla del orden de magnitud esperado de señales accionables/día en
vivo (no es una medición en vivo, es un proxy histórico) -- se propone
presupuestar con margen (p. ej. hasta 20-25 señales/día) porque: (a) la
frecuencia real puede variar bastante entre regímenes, (b) el conteo del
backtest no incluye las señales que el motor de riesgo omitió por SL
excesivo, que en vivo sí llegarían al LLM.

**Costo por llamada**: no se fija un número de USD/token aquí -- la tabla
de precios de Anthropic cambia y este documento no debe quedar con una
cifra obsoleta grabada; se confirma el precio vigente de Haiku 4.5 en la
página oficial de pricing al momento de implementar el punto 6 (subfase
3.6). Con cache de prompt (el contexto fijo -- instrucciones, esquema de
la cuenta, límites de riesgo -- se cachea; solo la señal específica entra
sin cache) el costo por llamada se domina por un puñado de tokens de
salida (decisión + razón corta), así que el presupuesto de **$1/día** es
generoso para el volumen esperado -- se reevalúa con el gasto real una vez
implementado, no antes.

**Tope duro**: `system_state.llm_spend_today_usd` acumulado, reiniciado a
medianoche en `REPORT_TIMEZONE` (ya configurado); al superar
`LLM_DAILY_BUDGET_USD` (ya existe, $1 default) se bloquean llamadas nuevas
por el resto del día (ver punto 2, `SKIPPED_NO_LLM_BUDGET`).

## 7. Robustez en PC local

- **Reconciliación al reiniciar**: ver punto 3 (reproduce el stop_engine en
  modo vela sobre el rango de caída antes de retomar ticks en vivo).
- **Datos obsoletos**: tabla nueva `data_source_health` (`source,
  last_success_at, last_error, consecutive_failures`) para kline REST,
  funding REST y el WS de ticks (hoy solo existe este patrón para el
  universo de CoinGecko, `system_state.universe_last_refresh_ok_at` /
  `UNIVERSE_STALENESS_HOURS=48h`). Regla: si una fuente CRÍTICA (kline o
  WS de ticks) no tiene un éxito en más de 3x su intervalo esperado, se
  bloquean NUEVAS entradas -- nunca los cierres por SL/TP/liquidación/
  circuit breaker, que deben poder ejecutarse incluso con datos degradados
  (negarse a cerrar una posición por datos viejos es más peligroso que
  negarse a abrir una).
- **Logging de salud por fuente**: una línea de log por transición de
  estado (sano -> degradado -> bloqueado y de vuelta), reutilizando
  `app/core/logging.py` ya existente.

## 8. Criterios de paso a dinero real (evaluados, no automáticos)

Reutiliza valores YA fijados en `app/config.py` desde Fase 0/1 (no se
inventan de nuevo): **mínimo 30 días corriendo** (`MIN_PAPER_TRADING_DAYS`)
**Y** **100 operaciones cerradas** (`MIN_CLOSED_TRADES`) en la cuenta real
(brazo B), **PF > 1.3** tras costos (`MIN_PROFIT_FACTOR`). Se agregan:

- **Drawdown**: máximo observado en la ventana de evaluación no debe
  superar el límite de seguridad en vivo ya definido en el punto 4 (20%,
  `MAX_DRAWDOWN_PCT`) más de una vez (una sola activación del stop total se
  tolera como evento aislado revisado manualmente; una segunda activación
  dentro de la misma ventana descarta el paso a dinero real sin excepción).
- **Sin dependencia de una sola operación/activo**: mismo criterio ya
  congelado para el backtest (`BACKTEST_CONCENTRATION_LIMIT_PCT=40%` del
  PnL neto total) -- se reutiliza tal cual, no se define un segundo umbral.
- **Sin errores críticos de reconciliación**: cero eventos de severidad
  CRITICAL en el log de reconciliación (punto 7) durante la ventana.
- **Atribución a la IA**: el reporte de paso a dinero real muestra PF/
  expectativa de B vs. C vs. A (punto 2) lado a lado con el tamaño de
  muestra de cada uno. Si B no supera a A/C con evidencia suficiente
  (≥30 operaciones por brazo), se documenta explícitamente como una señal
  para CONSIDERAR operar sin el filtro del LLM en vez de asumir que el LLM
  aporta valor por defecto -- el reporte informa esta comparación, la
  decisión final de pasar a dinero real sigue siendo manual del usuario.

## 9. Fuera de alcance de esta fase (y por qué)

- **Dinero real** (`BitunixBackend` activo): fuera de alcance de **todo**
  v1, no solo de esta fase -- regla fija de `CLAUDE.md`.
- **Memoria de lecciones aprendidas entre operaciones, noticias/sentiment,
  recalibración automática del umbral de confianza del LLM**: quedan para
  después de esta fase -- son mejoras SOBRE el filtro aprobar/rechazar que
  esta fase ya entrega, no requisitos para medir su valor inicial.
- **Frontend/dashboard, notificaciones enriquecidas más allá de los 7
  comandos de Telegram**: Fase 5, sin cambios respecto al plan original --
  los datos de `signals`/`shadow_trades`/`trades` ya quedan en SQLite,
  listos para que Fase 5 los muestre sin recalcular nada.
- **Score de confluencia numérico formal**: no se construye una fórmula
  separada de ponderación entre estrategias -- el LLM recibe el contexto
  de TODAS las señales simultáneas de un símbolo (incluidas las HOLD de
  otras estrategias) y juzga la confluencia de forma holística, como ya
  prevé el esquema de decisión de `docs/SPEC.md`. `CONFLUENCE_SCORE_
  THRESHOLD` (ya en config) se usa solo como respaldo cuando el
  presupuesto de LLM se agota (punto 6), no como filtro previo a la
  llamada.
- **Límites de correlación entre símbolos distintos**: el plan de Fase 2
  (sección E) difirió esto explícitamente a esta fase, pero el límite por
  activo ya existente (`MAX_CAPITAL_PCT_PER_ASSET=10%`, usado en
  `Settings.max_margin_for_new_trade`) ya cubre el caso práctico para un
  universo de 10 símbolos con márgenes pequeños -- no se construye lógica
  de correlación nueva salvo que la evidencia de paper trading muestre que
  hace falta.

### Orden de subfases y criterios de aceptación

| Subfase | Contenido | Aceptación |
|---|---|---|
| 3.1 | Extraer `stop_engine.py` (punto 3, modo vela) + agregar modo tick | Tests de backtest existentes siguen verdes sin tocarlos (prueba de que la extracción no cambió comportamiento); tests nuevos de modo tick con los mismos casos límite que el modo vela (SL+TP mismo tick, orden SL vs. liquidación) |
| 3.2 | Motor de riesgo en vivo (punto 4): límites, circuit breaker, stop por drawdown, kill switch, migración de `trades`/`Trade` | Test por límite (cada uno bloquea entradas nuevas sin afectar el cierre de posiciones ya abiertas); escenario de racha de pérdidas confirma que el circuit breaker activa a la N-ésima pérdida exacta y se auto-reanuda tras el cooldown |
| 3.3 | Generador de señales (punto 1) + tabla `signals` | Smoke test sintético: una fila por (símbolo, estrategia, timeframe, vela), sin duplicados en polls repetidos de la misma vela cerrada; corrida real de 2 minutos contra copia de la BD real puebla `signals` para el universo vigente |
| 3.4 | Integración del WS (punto 3) + monitor de posiciones en vivo + reconciliación al reiniciar (punto 7) + `data_source_health` | Test de integración: secuencia de ticks que cruza el SL cierra la posición con el precio/razón correctos; test de reconciliación: vela sintética de SL durante una caída simulada cierra la posición retroactivamente antes de retomar ticks |
| 3.5 | Brazos A y C sin LLM todavía (punto 2 parcial): toda señal accionable se simula en `shadow_trades` con margen ilimitado | Smoke test: shadow trades usan exactamente el mismo cálculo de fees/PnL que `PaperBackend` para la misma señal (fixture compartida); no dependen de margen/posiciones disponibles en la cuenta real |
| 3.6 | LLM (punto 6): cliente Anthropic, validación con pydantic, cache de prompt, tope de $1/día, gating del brazo B | Tests unitarios con el cliente de Anthropic mockeado (nunca una llamada real en tests) para aprobar/rechazar/presupuesto agotado; UNA llamada real manual (Haiku, prompt trivial) la corre el usuario para confirmar credenciales y modelo, nunca el asistente (`CLAUDE.md`: nunca leer `.env`/credenciales) |
| 3.7 | Bot de Telegram (punto 5): 7 comandos, long-polling | Smoke test manual del usuario contra una instancia de paper (requiere token real de Telegram) |
| 3.8 | Reporte de paso a dinero real (punto 8) | Tests contra fixtures sintéticas que cubren cada combinación de criterio pasa/falla, incluida la comparación B vs. C vs. A con muestra insuficiente |

Orden deliberado: el motor de riesgo (3.2) va ANTES de que el generador de
señales (3.3) multiplique de 1 símbolo/1 estrategia a 10 símbolos/6
estrategias cuánto se evalúa por tick -- la red de seguridad debe existir
antes de que haya más por lo que preocuparse. El LLM (3.6) es
deliberadamente la última pieza de decisión en entrar: los brazos A/C (3.5)
ya acumulan datos de referencia mientras el LLM todavía no está conectado,
para tener una base de comparación desde el primer día que el brazo B
empiece a operar.

## Verificación de esta fase

1. `pytest` + `ruff check .` en verde en cada subfase, con los tests
   específicos de la tabla de arriba.
2. Antes de dar por terminada cualquier subfase que toque persistencia
   (3.2, 3.3, 3.4, 3.8): smoke test de extremo a extremo con datos
   sintéticos pequeños, más una corrida corta (<2 min) contra una COPIA de
   la base real (fixture `real_db_copy` ya existente) -- regla ya fijada
   en `CLAUDE.md`.
3. Las subfases 3.6 y 3.7 requieren credenciales reales (Anthropic,
   Telegram) que el asistente nunca debe leer ni usar -- sus pruebas
   manuales con la red real las corre el usuario; se entregan los comandos
   exactos, igual que se hizo para Fase 2.
4. Al cierre de la fase: actualizar `PROGRESS.md` con el estado de cada
   subfase y los primeros números reales de los brazos A/B/C (aunque sean
   de evidencia baja todavía), y detenerse a esperar aprobación antes de
   evaluar el paso a dinero real.
