# Fase 3 -- Gestión de riesgo, ejecución realista en paper trading y medición del valor del LLM

> **Plan aprobado por el usuario, con los ajustes de la tercera ronda ya
> incorporados** (ver abajo). Documento únicamente -- la implementación
> real avanza subfase por subfase (ver la tabla de subfases), deteniéndose
> a esperar aprobación al terminar cada una. Revisión (segunda ronda, tras
> feedback del usuario): corrige el diseño de los tres brazos de
> comparación, el modelo y costo del LLM, el tope de SL de la sección 4
> (hallazgo empírico nuevo) y el ritmo esperado de operaciones reales.
> Tercera ronda: corrige la fiabilidad de la dimensión "posiciones
> simultáneas" del bootstrap (sección 4), corrige el ritmo de operaciones
> con las cifras OOS reales (sección 8, antes mezclaba IS+OOS), eleva el
> criterio de valor de la IA a N=100 por lado con intervalo de confianza
> (sección 2), hace configurable el stop por drawdown (sección 4), y deja
> explícito que el precio de Sonnet no está verificado (sección 6).
> Investigación de respaldo: dos agentes Explore (hechos verbatim de
> `docs/SPEC.md` / `docs/FASE0.md`, y del código actual de `app/execution/`,
> `app/backtesting/engine.py`, `app/core/scheduler.py`,
> `app/market/bitunix_ws.py`), lectura directa de `app/config.py`,
> `docs/FASE2_CRITERIOS.md`, `docs/FASE2_RESULTADOS.md`,
> `app/backtesting/risk_analysis.py`; conteos y distribuciones reales
> leídos en modo solo-lectura de `data/minerva.db` (incluida la
> distribución de `sl_margin_loss_pct` por estrategia, que cambió la
> recomendación de la sección 4); y precios vigentes de la API de Claude
> consultados contra la documentación oficial de Anthropic en esta sesión.

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
es imposible sin que el LLM esté realmente tomando decisiones. Esta fusión
de alcance se declara aquí explícitamente, no es un cambio silencioso --
`docs/FASE0.md`/`SPEC.md` quedan superados en la numeración de fases a
partir de este plan.

Todos los valores de riesgo propuestos abajo se apoyan en tres fuentes ya
existentes: (1) los parámetros ya fijados en `app/config.py` desde Fase 0/1
(no se inventan de nuevo), (2) la metodología de bootstrap de
`app/backtesting/risk_analysis.py` (cuya salida numérica real,
`docs/FASE2_RIESGO.md`, todavía no existe porque falta correr
`scripts/analyze_risk.py` -- este plan referencia su metodología, nunca
inventa los números que falta generar), y (3) conteos y distribuciones
reales leídos de `data/minerva.db` en modo solo-lectura durante esta
investigación (10361 operaciones distintas entre las 6 estrategias
registradas, sobre 10 símbolos, abril 2022-octubre 2026: `ema_cross_9_21`
659, `trend_atr_stop_9_21_50` 1278, `mean_reversion_rsi14_bb20` 5994,
`donchian_breakout_20` 1913, `funding_contrarian_experimental` 154,
`funding_contrarian_percentile_experimental` 363 -- ~6.4 operaciones/día
combinadas). Una lectura más profunda de la distribución de
`sl_margin_loss_pct` (cuánto del margen perdería el SL planeado de cada
operación) encontró algo importante para la sección 4: revisar antes de
leerla.

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

## 2. Comparación de la decisión del LLM (medición de su valor)

> Revisado tras feedback: el diseño anterior (brazos A/B/C como tres
> poblaciones distintas de operaciones) mezclaba la calidad del juicio del
> LLM con los límites de la cuenta real. Este diseño separa ambas cosas.

**Filtro de tope de SL, primero y para todos por igual**: antes de que una
señal accionable (LONG/SHORT) de `signals` llegue a cualquier simulación o
decisión, se aplica el tope de SL en vivo (`LIVE_SL_MARGIN_CAP_PCT`, punto
4): si `sl_margin_loss_pct` (el riesgo planeado al abrir, igual que ya
calcula el backtest) supera el tope, la señal se descarta por completo --
no se simula, no se le pide decisión al LLM, no cuenta en ninguna
comparación. Se registra en una tabla nueva `signals_discarded_by_sl_cap`
(mismo patrón que `backtest_skipped_entries`). Esto garantiza que todo lo
que sigue -- sombra y real -- parte exactamente del mismo filtro, con el
mismo instante de entrada, margen, slippage y costos (todas pasan por el
mismo motor del punto 3): ningún brazo tiene una ventaja de muestreo sobre
otro.

**Toda señal que sobrevive ese filtro se simula como operación sombra**,
con margen/capital ilimitado, independiente de la cuenta real (tabla nueva
`shadow_trades`, mismas columnas de riesgo que `trades` más `signal_id`).
Cada fila se etiqueta con la decisión real del LLM sobre esa señal
(`llm_decision`): `APROBADA`, `RECHAZADA`, o `SIN_LLM` (presupuesto de
$1/día agotado, punto 6 -- a efectos de ejecución real se trata igual que
un rechazo, pero se reporta aparte, ver abajo: agotar el presupuesto no es
una señal de la calidad del LLM).

**Comparación principal**: profit factor y esperanza por operación de
`APROBADA` vs. `RECHAZADA`, ambas dentro de `shadow_trades` -- mismo motor,
mismo instante de entrada, mismo margen, mismo slippage, mismos costos, sin
que el límite de margen/posiciones de la cuenta real interfiera en ninguna
de las dos. Esto aísla la calidad del juicio del LLM de cualquier efecto
del motor de riesgo o de cupos de posición disponibles -- exactamente lo
que se quiere medir. `SIN_LLM` queda fuera de esta comparación (no se
mezcla con `RECHAZADA`) y se reporta aparte, como contexto de cuánta señal
quedó sin evaluar por costo.

**La cuenta real (paper, `PaperBackend`/`trades`) es el subconjunto
EJECUTADO, no la fuente de la comparación de calidad**: una señal
`APROBADA` que además pasa el motor de riesgo (punto 4: posiciones
disponibles, margen disponible, circuit breaker inactivo) se ejecuta de
verdad ahí; si el motor de riesgo la bloquea, se queda solo en
`shadow_trades` (ya está ahí, etiquetada `APROBADA`) con un flag
`executed_in_real_account=false`. La cuenta real sirve para medir lo que
solo una cuenta real puede medir: drawdown realizado, supervivencia (riesgo
de ruina) y si los límites del punto 4 se sostienen en la práctica -- no
para la comparación de calidad del LLM, que ya está limpia en
`shadow_trades`.

**Criterio de valor de la IA** (revisado, tercera ronda -- el umbral
anterior de N=30 por lado medía solo que hubiera "algo" de muestra, no que
la diferencia fuera real): se considera que hay evidencia SUFICIENTE para
concluir si el LLM aporta valor cuando existen, en `shadow_trades`, **al
menos N=100 operaciones `APROBADA` Y al menos N=100 `RECHAZADA`**, Y el
intervalo de confianza bootstrap (remuestreo con reemplazo, mismo método
ya construido en `app/backtesting/risk_analysis.py`, aplicado aquí a la
diferencia de esperanza `esperanza(APROBADA) - esperanza(RECHAZADA)`) al
95% **excluye el cero** -- si el intervalo cruza el cero, la diferencia
observada podría ser ruido, y se reporta así explícitamente (no se
redondea a "el LLM ayuda" ni a "el LLM no ayuda"). Con N entre 30 y 100 en
cualquiera de los dos lados, el `/metrics` de Telegram y el reporte de
paso a dinero real (punto 8) muestran las cifras igual, con una advertencia
de **evidencia baja** (no "insuficiente" -- se muestra, pero no decide
nada); por debajo de 30 por lado, se muestra la cifra cruda sin más
análisis. Esta comparación ocurre enteramente dentro de `shadow_trades`
(operación sombra, margen ilimitado, mismo motor para ambos lados) -- la
cuenta real NO es la fuente de este criterio, es intencionalmente una
población distinta y más chica (solo lo que además pasó el motor de
riesgo); la cuenta real sirve para medir drawdown y supervivencia (punto
8), nunca para decidir si el LLM vale la pena.

**Deduplicación de señales simultáneas (nuevo)**: la tabla `signals` del
punto 1 registra SIEMPRE una fila por cada (símbolo, estrategia,
timeframe, vela) -- nunca se deduplica en el origen, es el registro
auditable completo. Pero cuando dos o más estrategias distintas emiten
señal accionable en el MISMO símbolo, la MISMA dirección y la MISMA vela
(p. ej. `ema_cross_9_21` y `donchian_breakout_20` ambas LONG en BTCUSDT al
cierre de la misma vela de 4h), se agrupan en UN solo candidato de
operación antes de simular/decidir -- no se abren 2-3 posiciones
(sombra o reales) por lo que es, en la práctica, la misma oportunidad de
mercado, y no se le hacen 2-3 llamadas al LLM por lo mismo. El candidato
agrupado guarda la lista completa de estrategias que coincidieron
(`contributing_strategies`); el LLM recibe el contexto de todas ellas
junto (esto es, en los hechos, la señal de confluencia que ya preveía
`docs/SPEC.md`). Para que el reporte "por estrategia" (`/metrics`, punto 8)
siga siendo posible pese a esta agrupación, el resultado de la operación
agrupada se atribuye a CADA estrategia contribuyente en su propio desglose
-- nunca solo a la primera o a una "estrategia ganadora" arbitraria. Todos
los reportes de este punto (y de `/metrics`, punto 5, y del reporte de
paso a dinero real, punto 8) muestran SIEMPRE el desglose por estrategia,
nunca solo el agregado -- el agregado puede ocultar que una sola
estrategia domina el resultado.

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

**Hallazgo empírico que cambia esta sección** (leído de
`data/minerva.db`, modo solo lectura, antes de que exista
`docs/FASE2_RIESGO.md`): `ema_cross_9_21`, `funding_contrarian_
experimental` y `funding_contrarian_percentile_experimental` no definen un
`stop_price` propio -- usan el SL de respaldo porcentual
(`BACKTEST_FALLBACK_SL_PCT=5%` de movimiento de precio, que a 10x de
apalancamiento es **exactamente 50% de pérdida de margen**) en el **100%**
de sus operaciones históricas (659, 154 y 363 respectivamente, verificado
una por una). Un tope de SL en vivo más ajustado que 50% las excluiría POR
COMPLETO -- cero operaciones posibles, para siempre, no "menos
operaciones". La primera versión de este plan proponía 30% (el extremo más
conservador de la grilla de `risk_analysis.py`); se corrige a **50%** por
esto -- 30% sigue siendo un punto válido para EXPLORAR en la grilla (de
hecho, la grilla real mostrará 0 operaciones incluidas ahí para estas 3
estrategias, confirmando este hallazgo con el método ya construido), pero
como parámetro EN VIVO excluiría justo a la estrategia que mejor pasó Fase
2 (`ema_cross_9_21`) y a las dos que `docs/FASE2_RESULTADOS.md` ya marcó
explícitamente para observación hacia adelante.

**Advertencia sobre la dimensión "posiciones simultáneas" del bootstrap**
(tercera ronda, corrección del usuario): `docs/FASE2_RIESGO.md` NO es
fiable para justificar `MAX_SIMULTANEOUS_POSITIONS` por dos motivos
metodológicos documentados ahí mismo: (1) el bootstrap por lotes
(`app/backtesting/risk_analysis.py`) mide el drawdown solo al CIERRE de
cada lote, no dentro de él -- con más posiciones por lote, menos lotes
totales en el mismo ensayo, así que hay MENOS puntos de medición, lo que
sesga el drawdown medido hacia abajo (parece menos riesgoso de lo que es);
(2) el método asume operaciones independientes, pero las altcoins del
universo están correlacionadas entre sí (suelen moverse juntas) -- abrir
"3 posiciones simultáneas" en la práctica es más parecido a una sola
apuesta direccional grande que a 3 apuestas independientes, algo que el
bootstrap no puede capturar porque remuestrea operaciones sueltas sin
noción de qué símbolo o dirección tenía cada una. El efecto neto de ambos
sesgos es que MÁS posiciones simultáneas aparenta MENOS riesgo en la
tabla -- al revés de la realidad. Por eso el valor de
`MAX_SIMULTANEOUS_POSITIONS` de abajo NO se justifica con esa columna
(se mantiene en 3 porque ya estaba fijado desde Fase 0/1, no porque la
grilla lo confirme), y se agrega un límite nuevo e independiente,
`MAX_SAME_DIRECTION_POSITIONS`, para acotar directamente el riesgo de
correlación que el bootstrap no puede medir.

| Parámetro | Valor propuesto | Por qué | Qué de `docs/FASE2_RIESGO.md` lo confirma o lo cambia |
|---|---|---|---|
| Margen por operación | **5 USDT** (bajar de los 10 ya configurados) | La simulación de cartera de Fase 2 arruina la cuenta a 10 USDT/3 posiciones en 3 de 4 estrategias por reglas; bajar el margen reduce la exposición por ronda mientras el filtro del LLM todavía no tiene evidencia propia. | Fila margen=5 en la grilla por estrategia de `ema_cross_9_21` y las 2 experimentales de funding (las elegibles para cuenta real, ver abajo): si P(ruina) ahí es alta, reconsiderar a la baja. |
| Máximo de posiciones simultáneas | **3** (sin cambio) | Ya fijado en `MAX_SIMULTANEOUS_POSITIONS` desde Fase 0/1 -- **NO** se justifica con la columna de posiciones de `docs/FASE2_RIESGO.md` (ver advertencia arriba: esa columna está sesgada hacia "más posiciones = menos riesgo", al revés de la realidad). | Ninguna -- esta columna del archivo no es apta para decidir este parámetro. |
| **Máximo de posiciones en la misma dirección** (nuevo) | **`MAX_SAME_DIRECTION_POSITIONS=2`** | Acota directamente el riesgo de correlación entre altcoins que el bootstrap no puede medir: nunca más de 2 posiciones LONG (o 2 SHORT) abiertas a la vez, sin importar en qué símbolos, para no convertir "3 posiciones simultáneas" en una sola apuesta direccional triple. `.env`-configurable. | No aplica (es precisamente la limitación que la grilla no cubre). |
| Tope de pérdida del SL sobre el margen | **50%** (revisado desde 30% -- ver hallazgo arriba; es exactamente `MAX_SL_MARGIN_LOSS_PCT`, no un número nuevo) | A 30%, las 3 estrategias elegibles para cuenta real quedan con 0 operaciones posibles. 50% es el mismo tope que ya usa el backtest, así que todo lo que el backtest aprobó sigue siendo operable en vivo. | La columna "Excluidas por tope" en la fila de 50% para esas 3 estrategias debe dar 0 (si no, hay una discrepancia que investigar); la fila de 40% es la evidencia documentada de por qué un tope más ajustado no es viable para ellas. |
| Pérdida máxima diaria | **5%** (sin cambio, `MAX_DAILY_LOSS_PCT`) | Ya fijado, sin evidencia en Fase 2 para moverlo. | No depende de la grilla de riesgo de ruina por operación -- es un límite de cuenta independiente. |
| Circuit breaker | **4 pérdidas consecutivas**, cooldown **8h**, auto-resume (sin cambio) | Ya fijados; el auto-resume es apropiado porque es una pausa táctica, no una señal de emergencia. | idem (independiente de la grilla). |
| Stop total por drawdown | **20%** (`MAX_DRAWDOWN_PCT`, sin cambio) -- modo configurable, ver abajo | Distinto del `BACKTEST_MAX_DRAWDOWN_PCT=50%` -- ese es un criterio de descarte de ESTRATEGIA en el backtest; este es el límite de SEGURIDAD de la cuenta en vivo. | idem (independiente de la grilla). |
| Kill switch manual | Inmediato, resume manual, vía `/kill`/`/resume` | Pausa el scheduler; a diferencia del circuit breaker, nunca se levanta solo. | No aplica. |

**Stop por drawdown configurable (nuevo, tercera ronda)**:
`DRAWDOWN_STOP_MODE` (`.env`, valores `"duro"` o `"alerta"`, default
**`"duro"`**) -- en modo `"duro"` el stop del 20% detiene el scheduler de
verdad (resume manual, igual que antes); en modo `"alerta"` el bot sigue
operando pero notifica por Telegram. En AMBOS modos se incrementa un
contador persistente `system_state.drawdown_stop_would_have_triggered_count`
cada vez que el drawdown cruza el 20%, se esté deteniendo el bot o no --
este contador alimenta el criterio de paso a dinero real (punto 8) sin
importar en qué modo haya corrido la cuenta: una cuenta que pasó meses en
modo `"alerta"` y cruzó el umbral muchas veces no debe parecer más segura
que una en modo `"duro"` solo porque nunca se detuvo de verdad.

**Riesgo por operación con estos valores** (regla simple, pedida
explícitamente): margen 5 USDT × tope de SL 50% = **2.5 USDT = 2.5% del
capital** de pérdida máxima por operación; con 3 posiciones simultáneas,
**7.5 USDT = 7.5%** de riesgo total abierto en el peor caso de que las 3
toquen su SL a la vez. Esto es más alto que el 1.5%/4.5% que resultaría de
un tope de 30% -- consecuencia directa y aceptada de que 30% deja a las
estrategias elegibles para cuenta real sin poder operar, no una elección
aislada.

**Elegibilidad por estrategia para la cuenta real** (nuevo -- responde a si
`mean_reversion_rsi14_bb20` debería ocupar cupo real o solo sombra): al
arrancar Fase 3, propongo que solo `ema_cross_9_21` (única que crece en el
100% de las corridas Monte Carlo de cartera de Fase 2, aunque no pase el
umbral de PF) y las 2 estrategias de funding experimentales (que
`docs/FASE2_RESULTADOS.md` ya marca para "observación hacia adelante... en
el paper trading de Fase 3") sean elegibles para ejecutarse en la cuenta
real, sujeto a aprobación del LLM y al motor de riesgo. `trend_atr_stop_
9_21_50`, `mean_reversion_rsi14_bb20` y `donchian_breakout_20` -- las 3 que
la simulación de cartera de Fase 2 mostró más frágiles -- arrancan en modo
**solo sombra**: sus señales se generan, se registran, el LLM las evalúa
igual (alimentan la comparación `APROBADA` vs. `RECHAZADA` del punto 2),
pero nunca se ejecutan en la cuenta real hasta que haya evidencia de paper
trading que lo justifique. Razón adicional para `mean_reversion` en
particular: con 5994 de las 10361 operaciones históricas totales (58%), si
fuera elegible dominaría los 3 cupos de posición simultánea y buena parte
del presupuesto de LLM, dejando poca oportunidad real a las demás
estrategias -- mejor observarla a fondo primero. Esta lista de elegibilidad
es `.env`-configurable (`REAL_ACCOUNT_ELIGIBLE_STRATEGIES`), para poder
ampliarla sin tocar código cuando corresponda.

Todos los valores numéricos de esta tabla son `.env`-configurables (ya
existen como settings salvo el tope de SL, que se agrega como
`LIVE_SL_MARGIN_CAP_PCT`), para poder ajustarlos sin tocar código cuando
`docs/FASE2_RIESGO.md` y la evidencia de paper trading lo justifiquen.

## 5. Bot automático, Telegram solo controla

El bot abre y cierra posiciones solo (generador de señales + LLM + motor de
riesgo, puntos 1-4) -- Telegram nunca decide una entrada o salida, solo
informa y controla el proceso. Nuevo `app/telegram/bot.py` (no existe
ningún archivo hoy, solo placeholders de config: `telegram_bot_token`/
`telegram_chat_id`), vía long-polling (sin necesitar una URL pública/HTTPS
en una PC local, a diferencia de un webhook).

**Allowlist obligatoria**: el bot responde SOLO a mensajes cuyo `chat_id`
coincide exactamente con `TELEGRAM_CHAT_ID` configurado -- cualquier otro
chat o usuario se ignora por completo (ni siquiera un mensaje de error, para
no confirmarle a un tercero que el bot existe y está escuchando). La
subfase 3.7 incluye un test explícito que confirma que un `chat_id`
distinto del configurado no dispara ningún comando, usando un `chat_id`
sintético distinto en un update simulado.

Comandos (7 -- extienden los 6 que proponía `docs/FASE0.md`, que no incluía
`/kill`):

- `/status` -- resumen: capital, posiciones abiertas, estado del circuit
  breaker/kill switch, gasto de LLM del día.
- `/positions` -- posiciones reales abiertas con PnL flotante.
- `/balance` -- capital actual vs. inicial.
- `/pause` / `/resume` -- pausa/reanuda el scheduler (no cierra posiciones
  abiertas, solo bloquea nuevas entradas).
- `/metrics` -- PF/expectativa de `APROBADA` vs. `RECHAZADA` (punto 2), por
  estrategia y agregado, con su tamaño de muestra y advertencia de
  evidencia baja si corresponde.
- `/kill` -- kill switch manual (punto 4).

## 6. Costo del LLM

**Modelos** -- configurables en `.env`, **NO se dan por verificados en este
documento** (regla ya vigente de `CLAUDE.md`: no inventar parámetros ni
firmas sin verificar contra la documentación oficial; la verificación real
la hace el usuario con una llamada de prueba en la subfase 3.6). Como
referencia a confirmar en ese momento: Sonnet vigente `claude-sonnet-5-5` y
Haiku `claude-haiku-4-5-20251001`. `ANTHROPIC_SONNET_MODEL`/
`ANTHROPIC_HAIKU_MODEL` (ya existen en `app/config.py`) quedan como los
puntos de configuración; ningún string de modelo se fija en el código.

**Quién decide**: **Sonnet** toma la decisión aprobar/rechazar de cada
señal accionable (no Haiku, a diferencia de la primera versión de este
plan) -- el volumen es bajo (ver estimación abajo) y lo que se mide es la
CALIDAD del juicio del LLM (punto 2), no el costo por llamada. Haiku queda
reservado para tareas futuras de mayor volumen (p. ej. si Fase 4 agrega
resumen masivo de noticias).

**Estimación de volumen** (dato real, leído de `backtest_trades` en modo
solo lectura): ~6.4 operaciones/día combinadas entre las 6 estrategias
sobre los 10 símbolos actuales, promediado sobre ~4.5 años de historia --
usado como ancla del orden de magnitud esperado de señales accionables/día
en vivo. Se presupuesta con margen, hasta **25 señales/día**, porque (a) la
frecuencia real puede variar entre regímenes y (b) el conteo del backtest
no incluye las señales que el motor de riesgo omitió por SL excesivo, que
en vivo sí llegarían al LLM (antes del filtro del punto 2).

**Estimación de costo -- precio NO verificado** (tercera ronda: no se da
por confirmado el precio de Sonnet en este documento, ni siquiera el
consultado en esta sesión contra la documentación de Anthropic -- los
precios cambian y este plan no debe quedar con una cifra vieja grabada
como si fuera un hecho. **El usuario confirma el precio vigente de Sonnet
directamente en la consola de Anthropic (console.anthropic.com, sección
de pricing/billing) antes de implementar la subfase 3.6** -- es el mismo
paso que ya exige `CLAUDE.md` para no inventar parámetros sin verificar).
Como referencia de orden de magnitud, NO como dato confirmado: el nivel
Sonnet rondaría $2.00 por millón de tokens de entrada y $10.00 por millón
de salida, con lecturas de caché de prompt a ~10% del precio de entrada
(descuento estándar documentado). Con hasta 25 señales/día, un contexto
fijo cacheado de ~2500 tokens (instrucciones, límites de riesgo del punto
4, esquema de la cuenta), ~700 tokens variables por señal (indicadores,
funding, señales relacionadas de otras estrategias en el mismo símbolo) y
~250 tokens de salida (decisión + razón corta):

- Con el caché de prompt funcionando: **≈ $0.10-0.12/día**.
- Sin caché (escenario conservador, por si algo invalida el caché): **≈
  $0.22/día**.

Ambos escenarios quedan muy por debajo del tope de **$1/día** -- incluso el
escenario sin caché deja más de 4x de margen, con suficiente colchón para
absorber que el precio real resulte distinto al de referencia de arriba.
Son cifras de diseño sobre un precio no confirmado, no una medición real;
se reevalúan con el precio real (confirmado en la consola antes de 3.6) y
el gasto real una vez implementado (ver `llm_logs` abajo).

**Registro (`llm_logs`, tabla nueva)**: cada llamada al LLM guarda
`signal_id, prompt, response, model, input_tokens, output_tokens, cost_usd,
latency_ms, prompt_version, created_at` -- el registro completo que
`CLAUDE.md` ya exige para toda decisión del LLM (validada con esquema y
registrada en la base de datos), y la base para recalcular el costo real
frente a la estimación de arriba. `prompt_version` identifica qué versión
del prompt produjo cada decisión histórica, para cuando el prompt cambie.

**Temperatura**: baja (cercana a 0) para que la misma señal, con el mismo
contexto, tienda a la misma decisión -- la comparación del punto 2
(`APROBADA` vs. `RECHAZADA`) pierde sentido si el LLM es inconsistente ante
inputs casi idénticos.

**Tope duro**: `system_state.llm_spend_today_usd` acumulado, reiniciado a
medianoche en `REPORT_TIMEZONE` (ya configurado); al superar
`LLM_DAILY_BUDGET_USD` (ya existe, $1 default) se bloquean llamadas nuevas
por el resto del día (ver punto 2, status `SIN_LLM`).

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

**Estimación de ritmo real de operaciones -- corregida (tercera ronda)**:
la versión anterior usaba conteos IS+OOS combinados sobre todo el rango
histórico, lo cual es incorrecto para estimar el ritmo EN VIVO -- lo
correcto es usar solo las operaciones OOS (el segmento que realmente
evalúa desempeño fuera de muestra) sobre la duración real de la ventana
OOS, no sobre toda la historia. Corregido con datos reales de
`backtest_trades` (`segment='OOS'`): la ventana OOS real es
2025-04-30 a 2026-10-01, **≈519 días**. Operaciones OOS de las estrategias
elegibles para cuenta real (punto 4): `ema_cross_9_21` **243** (≈0.47/día),
`funding_contrarian_percentile_experimental` **184** (≈0.35/día),
`funding_contrarian_experimental` **14** (≈0.03/día). La suma da **≈0.85
señales accionables elegibles/día** -- techo optimista, no resta las que
el LLM rechace ni las que el motor de riesgo bloquee por cupos ocupados.

**`funding_contrarian_experimental` con solo 14 operaciones OOS NO es
evidencia sólida**: su PF de 1.43 (citado en `docs/FASE2_RESULTADOS.md`)
viene de una muestra demasiado chica para confiar en él -- ni para
aprobarla ni para descartarla. Se mantiene elegible para cuenta real
(Fase 2 ya la marcó para observación hacia adelante) pero su contribución
al ritmo de operaciones y a cualquier métrica se reporta siempre junto a
su N, nunca como si el PF 1.43 estuviera confirmado.

A ≈0.85 señales/día, **llegar a 100 operaciones cerradas en la cuenta real
toma aproximadamente 118 días (~3.9 meses), no 30**. El criterio "mínimo
30 días Y 100 operaciones" (`MIN_PAPER_TRADING_DAYS`/`MIN_CLOSED_TRADES`,
ya fijados en Fase 0) en la práctica queda determinado por el conteo de
operaciones, no por los días -- 30 días se cumple mucho antes de llegar a
100 operaciones a este ritmo. Opciones si se quiere evaluar antes: (a)
aceptar la ventana más larga (~120+ días), o (b) ampliar la elegibilidad de
cuenta real a estrategias con más volumen (p. ej. `donchian_breakout_20`,
749 operaciones OOS ≈1.44/día por sí sola) a costa de un perfil de riesgo
menos probado -- esta decisión queda para el usuario, este plan no la
fuerza. No se recomienda bajar el umbral de 100 operaciones: Fase 0 ya lo
fijó explícitamente para tener muestra estadística suficiente.

Reutiliza, además, valores YA fijados en `app/config.py` desde Fase 0/1:
**PF > 1.3** tras costos (`MIN_PROFIT_FACTOR`) en la cuenta real (brazo
ejecutado). Se agregan:

- **Drawdown**: `system_state.drawdown_stop_would_have_triggered_count`
  (punto 4 -- se incrementa en cualquier modo, `"duro"` o `"alerta"`) no
  debe superar 1 durante la ventana de evaluación (una sola activación se
  tolera como evento aislado revisado manualmente; una segunda descarta el
  paso a dinero real sin excepción). Usar el contador en vez del modo en
  sí evita que correr en `"alerta"` haga parecer la cuenta más segura de
  lo que realmente fue.
- **Sin dependencia de una sola operación/activo**: mismo criterio ya
  congelado para el backtest (`BACKTEST_CONCENTRATION_LIMIT_PCT=40%` del
  PnL neto total) -- se reutiliza tal cual, no se define un segundo umbral.
- **Sin errores críticos de reconciliación**: cero eventos de severidad
  CRITICAL en el log de reconciliación (punto 7) durante la ventana.
- **Atribución a la IA**: el reporte de paso a dinero real muestra PF/
  expectativa de `APROBADA` vs. `RECHAZADA` (punto 2), SIEMPRE por
  estrategia y agregado, lado a lado con el tamaño de muestra de cada uno.
  Usa el criterio de valor de la IA ya definido en el punto 2 (N=100 por
  lado Y el intervalo de confianza bootstrap de la diferencia de esperanza
  excluyendo el cero) -- si no se cumple, se documenta explícitamente como
  una señal para CONSIDERAR operar sin el filtro del LLM en vez de asumir
  que aporta valor por defecto -- el reporte informa esta comparación, la
  decisión final de pasar a dinero real sigue siendo manual del usuario.

## 9. Fuera de alcance de esta fase (y por qué)

- **Dinero real** (`BitunixBackend` activo): fuera de alcance de **todo**
  v1, no solo de esta fase -- regla fija de `CLAUDE.md`.
- **Frontend/dashboard, notificaciones enriquecidas más allá de los 7
  comandos de Telegram**: Fase 5. Los datos de `signals`/`shadow_trades`/
  `trades`/`llm_logs` ya quedan en SQLite, listos para que Fase 5 los
  muestre sin recalcular nada.
- **Score de confluencia numérico formal**: no se construye una fórmula
  separada de ponderación entre estrategias -- el LLM recibe el contexto
  de TODAS las señales simultáneas de un símbolo (incluidas las HOLD de
  otras estrategias) y juzga la confluencia de forma holística, como ya
  prevé el esquema de decisión de `docs/SPEC.md`. `CONFLUENCE_SCORE_
  THRESHOLD` (ya en config) se usa solo como respaldo cuando el
  presupuesto de LLM se agota (status `SIN_LLM`, punto 6), no como filtro
  previo a la llamada.
- **Límites de correlación entre símbolos distintos**: el plan de Fase 2
  (sección E) difirió esto explícitamente a esta fase, pero el límite por
  activo ya existente (`MAX_CAPITAL_PCT_PER_ASSET=10%`, usado en
  `Settings.max_margin_for_new_trade`) ya cubre el caso práctico para un
  universo de 10 símbolos con márgenes pequeños -- no se construye lógica
  de correlación nueva salvo que la evidencia de paper trading muestre que
  hace falta.

### Hoja de ruta posterior

- **Fase 4**: noticias (RSS CoinDesk/Cointelegraph u otra fuente gratuita
  verificada) **tratadas como texto NO confiable** -- un titular o resumen
  de noticia es contenido externo que llega al contexto del LLM, igual que
  cualquier otro dato de una fuente que no controlamos; se trata como
  información a evaluar, nunca como instrucción (mismo principio que ya
  aplica a datos de herramientas en este mismo asistente), y no decide por
  sí sola ninguna entrada/salida. También Fase 4: memoria de lecciones
  aprendidas entre operaciones (qué patrones de señales rechazadas o
  aprobadas salieron bien/mal, para dárselo como contexto al LLM en
  decisiones futuras) y recalibración del umbral de confianza del LLM con
  esa historia. Ninguna de las dos es necesaria para medir el valor inicial
  del LLM (punto 2) -- son mejoras sobre el filtro aprobar/rechazar que esta
  fase ya entrega.
- **Fase 5**: frontend/dashboard, autenticación, Telegram enriquecido más
  allá de los 7 comandos de control.

### Orden de subfases y criterios de aceptación

| Subfase | Contenido | Aceptación |
|---|---|---|
| 3.1 | Extraer `stop_engine.py` (punto 3, modo vela) + agregar modo tick | Tests de backtest existentes siguen verdes sin tocarlos (prueba de que la extracción no cambió comportamiento); tests nuevos de modo tick con los mismos casos límite que el modo vela (SL+TP mismo tick, orden SL vs. liquidación) |
| 3.2 | Motor de riesgo en vivo (punto 4): límites (incl. `MAX_SAME_DIRECTION_POSITIONS`), lista de elegibilidad por estrategia, circuit breaker, stop por drawdown configurable (`DRAWDOWN_STOP_MODE` + contador `drawdown_stop_would_have_triggered_count`), kill switch, migración de `trades`/`Trade` | Test por límite (cada uno bloquea entradas nuevas sin afectar el cierre de posiciones ya abiertas); test de elegibilidad (una señal de una estrategia no elegible nunca llega a `PaperBackend.open_position`); test de `MAX_SAME_DIRECTION_POSITIONS` (una 3ª señal LONG se bloquea aunque `MAX_SIMULTANEOUS_POSITIONS` todavía tenga cupo); test de ambos modos de `DRAWDOWN_STOP_MODE` (el contador sube en los dos, solo `"duro"` detiene el scheduler); escenario de racha de pérdidas confirma que el circuit breaker activa a la N-ésima pérdida exacta y se auto-reanuda tras el cooldown |
| 3.3 | Generador de señales (punto 1) + tabla `signals` + filtro de tope de SL (`signals_discarded_by_sl_cap`, punto 2) | Smoke test sintético: una fila por (símbolo, estrategia, timeframe, vela), sin duplicados en polls repetidos de la misma vela cerrada; una señal con SL planeado por encima del tope queda en `signals_discarded_by_sl_cap`, no en `signals`; corrida real de 2 minutos contra copia de la BD real puebla `signals` para el universo vigente |
| 3.4 | Integración del WS (punto 3) + monitor de posiciones en vivo + reconciliación al reiniciar (punto 7) + `data_source_health` | Test de integración: secuencia de ticks que cruza el SL cierra la posición con el precio/razón correctos; test de reconciliación: vela sintética de SL durante una caída simulada cierra la posición retroactivamente antes de retomar ticks |
| 3.5 | `shadow_trades` sin LLM todavía (punto 2 parcial): señales que sobreviven el filtro de SL se AGRUPAN primero por (símbolo, dirección, vela) entre estrategias (`contributing_strategies`) y luego se simulan con margen ilimitado, `llm_decision` queda `PENDIENTE` hasta 3.6 | Smoke test: shadow trades usan exactamente el mismo cálculo de fees/PnL que `PaperBackend` para la misma señal (fixture compartida); no dependen de margen/posiciones disponibles en la cuenta real; test de agrupación: 2 señales de estrategias distintas, mismo símbolo/dirección/vela, producen UN solo `shadow_trade` con ambas estrategias listadas, no dos |
| 3.6 | LLM (punto 6): cliente Anthropic (Sonnet), `llm_logs`, validación con pydantic, cache de prompt, temperatura baja, tope de $1/día, etiquetado `APROBADA`/`RECHAZADA`/`SIN_LLM` | Tests unitarios con el cliente de Anthropic mockeado (nunca una llamada real en tests) para aprobar/rechazar/presupuesto agotado; UNA llamada real manual (modelo y precio a confirmar contra la documentación oficial en ese momento) la corre el usuario para validar credenciales, modelo y costo real vs. la estimación de la sección 6, nunca el asistente (`CLAUDE.md`: nunca leer `.env`/credenciales) |
| 3.7 | Bot de Telegram (punto 5): 7 comandos, long-polling, allowlist por `TELEGRAM_CHAT_ID` | Test que confirma que un `chat_id` distinto del configurado no dispara ningún comando; smoke test manual del usuario contra una instancia de paper (requiere token real de Telegram) |
| 3.8 | Reporte de paso a dinero real (punto 8), con el ritmo de operaciones revisado (≈0.85 señales/día elegibles, ≈118 días a 100 operaciones) | Tests contra fixtures sintéticas que cubren cada combinación de criterio pasa/falla, incluido el intervalo de confianza bootstrap de `APROBADA` vs. `RECHAZADA` en los tres regímenes de evidencia (<30, 30-100, ≥100 por lado) |

Orden deliberado: el motor de riesgo (3.2) va ANTES de que el generador de
señales (3.3) multiplique de 1 símbolo/1 estrategia a 10 símbolos/6
estrategias cuánto se evalúa por tick -- la red de seguridad debe existir
antes de que haya más por lo que preocuparse. El LLM (3.6) es
deliberadamente la última pieza de decisión en entrar: `shadow_trades`
(3.5) ya acumula datos de referencia mientras el LLM todavía no está
conectado, para tener una base de comparación desde el primer día que
empiece a etiquetar `APROBADA`/`RECHAZADA`.

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
   subfase y los primeros números reales de `APROBADA`/`RECHAZADA` (aunque
   sean de evidencia baja todavía), y detenerse a esperar aprobación antes
   de evaluar el paso a dinero real.
