# Subfase 3.6 -- Decisión del LLM sobre las señales (DISEÑO v2, sin código)

Estado: **diseño v2 con los cambios que aprobaste; sin implementar**. Hasta que indiques
que empiece la implementación no se escribe código de la 3.6, no se hace ninguna llamada
a la API y `app/trading/ai_value.py` se mantiene como está.

Qué mide la 3.6: si la decisión del LLM sobre cada señal agrupada (APROBAR o RECHAZAR)
aporta valor frente a no filtrar nada, comparando la esperanza por operación de las
operaciones sombra APROBADA y RECHAZADA (`docs/FASE3_PLAN.md`, punto 2). La cuenta real
solo abre con etiqueta APROBADA, y solo si la decisión llega a tiempo (sección l).

---

## Cambios de la versión 2

1. **Regla de decisión con tres veredictos** (sección i), con un punto de análisis fijado
   de antemano y un margen de efecto `δ`. "Sin evidencia" ya no equivale a "la IA no
   aporta valor".
2. **Potencia estadística** (sección j): desviación típica por operación medida en el
   backtest OOS, error típico, efecto mínimo detectable y tiempos estimados.
3. **`max_tokens = 300`** y razonamiento de hasta 300 caracteres; coste máximo por
   llamada recalculado (secciones b y e).
4. **Contexto del prompt solo con posiciones reales** (sección a). Prueba de fuga ampliada.
5. **Piloto de 30 a 50 señales con prompt congelado** (sección k), marcado `PILOTO` y
   excluido de la medición. Banda de aprobación 15 %–85 %.
6. **Concurrencia**: decisiones en paralelo con semáforo (por defecto 4), 20 s como
   máximo por llamada, y la cuenta real no abre si la decisión llega más de 60 s después
   del cierre de la vela (sección l). La sombra no depende de ese retraso.
7. **Causas de SIN_LLM** registradas y reportadas por causa, hora y volatilidad, para
   detectar sesgo de exclusión (sección m).
8. **Placebo con intervalo de confianza** de la frecuencia observada (sección f).

**Corrección que introduce la versión 2.** La versión anterior decía que la sombra se
abría ya con su etiqueta decidida. Con decisiones en paralelo eso no es posible sin
bloquear el ciclo de señales. Ahora la sombra se abre al cierre de la vela con
`SIN_LLM` y la decisión escribe la etiqueta una sola vez (sección d). El PnL de la
sombra no depende de la etiqueta, así que la comparación no cambia.

---

## 0. Verificación de precios e IDs de modelo (fuente y fecha)

Consultado el **2026-10-04** en la documentación oficial de Anthropic:

- Precios: `https://platform.claude.com/docs/en/about-claude/pricing` (la página no
  muestra fecha propia; la consulta es la fecha de referencia).
- Modelos e IDs: `https://platform.claude.com/docs/en/models/overview`.

Las URL `docs.anthropic.com/...` redirigen a `platform.claude.com`.

**Precios por millón de tokens (entrada / salida), tabla oficial:**

| Modelo | Entrada | Salida | Lectura de caché | Retiro (no antes de) |
|---|---|---|---|---|
| Claude Sonnet 5.5 | $2 | $10 | $0,20 | 28-sep-2027 |
| Claude Sonnet 5 | $2 | $10 | $0,20 | (página de modelo heredado, ver abajo) |
| Claude Haiku 4.5 | $1 | $5 | $0,10 | **15-oct-2026** |
| Claude Opus 5.5 | $4 | $20 | $0,20 | 22-sep-2027 |
| Claude Fable 5.1 | $10 | $50 | $0,25 | 1-sep-2027 |

La página indica que el precio de Sonnet 5 de $2/$10 pasó a ser el estándar tras el
31-ago-2026 y que el aumento previsto a $3/$15 no ocurrirá. Los lotes (Batch API)
tienen 50 % de descuento, pero la 3.6 necesita respuesta en tiempo real: no aplican.

**IDs de modelo, según la tabla oficial de modelos:**

| Uso | ID de API | Estado |
|---|---|---|
| Sonnet 5.5 | `claude-sonnet-5-5` | **Verificado** en la tabla de modelos. **Elegido** |
| Opus 5.5 | `claude-opus-5-5` | **Verificado** |
| Fable 5.1 | `claude-fable-5-1` | **Verificado** |
| Haiku 4.5 | `claude-haiku-4-5-20251001` (ID fijado); alias `claude-haiku-4-5` | **Verificado**. Se retira no antes del 15-oct-2026 |
| Sonnet 5 | `claude-sonnet-5` | **NO verificado**: la tabla vigente no lo lista; solo aparece como modelo heredado |

**Configuración actual:** `app/config.py` tiene por defecto
`anthropic_sonnet_model = "claude-sonnet-5"`, que no está verificado. Al implementar, el
valor pasa a `claude-sonnet-5-5`. Confirmarás los precios en tu consola antes de la
primera llamada real.

---

## (a) Información que recibe el LLM por señal, y por qué

Se envía una sola vez por grupo de señal (símbolo, dirección, vela de cierre), al
cerrar la vela evaluada. Todo son datos hasta el instante de la decisión.

**Entra (solo datos disponibles en el cierre de la vela):**

1. **Identidad:** símbolo, dirección, timeframe, instante de cierre de la vela, lista
   de estrategias contribuyentes y la representativa.
2. **Indicadores de cada contribuyente**, tal como los calcula `precompute` en la
   última vela cerrada (`indicators_json` de `signals`). Por ejemplo, EMA rápida y
   lenta, RSI, banda de Bollinger, canal de Donchian, ATR.
3. **Niveles y riesgo planeado:** SL, TP, trailing, precio de liquidación estimado y
   `sl_margin_loss_pct` (riesgo planeado como % del margen, frente al tope de 50 %).
4. **Funding:** tasa en la vela evaluada, intervalo del contrato, y si la tasa es
   aproximada (bares posteriores al último evento real, ver subfase 3.3).
5. **Volatilidad reciente:** ATR(14) relativo al precio, y desviación típica de los
   retornos logarítmicos de las últimas 30 velas cerradas. Ambas calculadas solo con
   velas cuyo cierre es anterior o igual a la vela evaluada.
6. **Correlación con BTC:** correlación de retornos de las últimas 30 y 100 velas
   cerradas, calculada solo con velas de BTCUSDT cuyo cierre es anterior o igual a la
   vela evaluada.
7. **Posiciones reales abiertas en el momento de la decisión:** número y dirección de
   las posiciones de la cuenta paper real que están abiertas, y cuántas son del mismo
   símbolo. **Las operaciones sombra no entran.** Son un instrumento del experimento,
   sin límite de cupo ni de margen; incluirlas haría que el prompt dependiera del propio
   experimento, y un operador real no vería esas posiciones.

**No entra, por diseño:**

- Cualquier vela posterior al cierre evaluado, y cualquier dato que dependa de ella.
- El resultado de operaciones sombra o reales posteriores a la decisión.
- El historial de aciertos de la propia IA o de la estrategia. Si el LLM recibe su
  propio historial, la comparación APROBADA vs RECHAZADA deja de medir solo el juicio
  sobre la señal.
- La etiqueta de otras señales del mismo grupo, y el precio o tiempo de la ejecución
  real.

**Prueba de fuga (obligatoria antes de usarlo):** el constructor de features recibe
las velas y el instante de decisión. Tres tests:

- Se inyectan velas posteriores con valores extremos (p. ej. un cierre x100): las
  features del instante evaluado no cambian.
- La correlación con BTC y el ATR calculados sobre la serie completa son iguales a los
  calculados sobre la serie truncada en el cierre evaluado.
- Ninguna feature usa una vela con `open_time` posterior al cierre evaluado.

---

## (b) Prompt y esquema de salida

**Versión del prompt:** `prompt_version = "signal_review_v1"`. Se guarda en `llm_logs`
junto con un hash SHA-256 de la plantilla. Cualquier cambio de texto cambia la versión
(sección k).

**Parámetros de la llamada:**

- `temperature = 0.0` (la más baja permitida), para que la misma entrada tienda a la
  misma decisión.
- `max_tokens = 300` (antes 150). Acota la salida y el coste máximo por llamada.
- Una sola llamada por grupo, **sin reintentos**: un reintento retrasa la decisión y
  puede sesgar la comparación. Un fallo deja la etiqueta SIN_LLM (sección d).
- Tiempo máximo de espera: 20 segundos por llamada.

**Plantilla (mensaje de sistema, estático):**

```
Eres un revisor de señales de trading de criptomonedas en paper trading.
Recibes una señal ya generada por reglas técnicas y debes decidir si se
APRUEBA o se RECHAZA. Decides solo con los datos del mensaje; no tienes
acceso a nada más. No predices precios. Responde SOLO con un objeto JSON
válido que siga exactamente el esquema indicado, sin texto adicional.
Esquema: {"decision": "APROBAR" | "RECHAZAR", "confianza": número entre 0 y 1,
"razonamiento": texto de máximo 300 caracteres}.
```

**Mensaje de usuario (dinámico):** un objeto JSON con las features de la sección (a),
con campos fijos y nulos explícitos cuando falte un dato. No se mezcla texto libre
con datos.

**Esquema de salida, validación estricta con pydantic:**

```python
class LlmDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APROBAR", "RECHAZAR"]
    confianza: float = Field(ge=0.0, le=1.0)
    razonamiento: str = Field(min_length=1, max_length=300)
```

Una respuesta que no pase la validación (JSON malformado, campo extra, valor fuera de
rango, texto antes o después del JSON) se trata como fallo `INVALID`: etiqueta SIN_LLM.
La respuesta cruda se guarda igualmente en `llm_logs`.

La etiqueta sale de `decision` con un mapeo fijo: `APROBAR` → APROBADA,
`RECHAZAR` → RECHAZADA. La `confianza` se guarda para análisis, pero **no** cambia la
etiqueta.

---

## (c) Tabla `llm_logs`

| Columna | Tipo | Contenido |
|---|---|---|
| `id` | INTEGER PK | |
| `created_at` | TEXT | instante de envío de la llamada (UTC) |
| `signal_group_key` | TEXT UNIQUE | clave única de la señal agrupada (`symbol\|side\|vela`); una llamada por grupo |
| `shadow_trade_id` | INTEGER NULL | operación sombra asociada |
| `fase` | TEXT | `PILOTO` o `MEDICION`. Los `PILOTO` se excluyen del análisis |
| `candle_close_time` | TEXT | cierre de la vela evaluada (UTC) |
| `decision_delay_s` | REAL | `created_at` menos `candle_close_time`, en segundos |
| `hour_utc` | INTEGER | hora UTC del cierre de la vela (0–23) |
| `atr_pct` | REAL NULL | ATR(14) relativo al precio en el momento de la decisión |
| `model` | TEXT | ID exacto de API usado |
| `prompt_version` | TEXT | p. ej. `signal_review_v1` |
| `prompt_sha256` | TEXT | hash de la plantilla de sistema |
| `prompt` | TEXT | mensaje completo enviado (sistema + usuario) |
| `response_raw` | TEXT NULL | respuesta cruda, aunque sea inválida |
| `status` | TEXT | `OK`, `TIMEOUT`, `ERROR_HTTP`, `INVALID`, `BUDGET_EXCEEDED` |
| `error` | TEXT NULL | mensaje de error |
| `decision` | TEXT NULL | `APROBAR` o `RECHAZAR` si la validación pasó |
| `input_tokens` | INTEGER NULL | tokens de entrada reportados por la API |
| `output_tokens` | INTEGER NULL | tokens de salida reportados por la API |
| `cost_usd` | REAL | coste real calculado con los precios de la sección 0 |
| `latency_ms` | INTEGER | latencia de la llamada |

Cada grupo que pasa por el LLM genera exactamente una fila, incluidas las fallidas y las
bloqueadas por presupuesto (con su `status`).

---

## (d) Etiqueta inmutable

**Apertura de la sombra:** la sombra se abre **al cierre de la vela**, con
`llm_decision = SIN_LLM`, sin esperar a la llamada. Así el ciclo de señales no depende
de la latencia de la API y la sombra no depende del retraso de la cuenta real.

**Etiquetado:** la decisión escribe la etiqueta **una sola vez**, en la misma
transacción que la fila de `llm_logs`. No se modifica nunca después.

**Garantía en la base:** un disparador impide cambiar `llm_decision` cuando ya no es
SIN_LLM:

```sql
CREATE TRIGGER llm_decision_inmutable
BEFORE UPDATE OF llm_decision ON shadow_trades
WHEN OLD.llm_decision <> 'SIN_LLM' AND NEW.llm_decision <> OLD.llm_decision
BEGIN
    SELECT RAISE(ABORT, 'llm_decision es inmutable una vez decidida');
END;
```

**Transiciones permitidas:** `SIN_LLM → APROBADA` y `SIN_LLM → RECHAZADA`, una sola vez.
Cualquier fallo, timeout, respuesta inválida o presupuesto agotado deja **SIN_LLM**,
nunca RECHAZADA. Un fallo técnico no es un juicio negativo y contaminaría la
comparación.

**Operación sombra:** su PnL no depende de la etiqueta. La etiqueta solo decide la
comparación del análisis y, en la cuenta real, si se abre la posición (sección g).

---

## (e) Control de coste

**Tope diario de 1 USD con corte duro, con reserva.** Antes de cada llamada se calcula
el coste **máximo** posible con los tokens de entrada estimados y `max_tokens`:

```
coste_max = tokens_entrada_estimados * precio_entrada + max_tokens * precio_salida
          = 1.200 * 2 / 1.000.000 + 300 * 10 / 1.000.000 = 0,0054 USD (Sonnet 5.5)
```

**Reserva bajo lock.** Como las decisiones se hacen en paralelo (sección l), la
comprobación y la reserva se hacen en una sección crítica:

```
gasto_hoy (llm_logs del día en REPORT_TIMEZONE)
+ reservas_en_vuelo (coste_max de las llamadas en curso)
+ coste_max
> LLM_DAILY_BUDGET_USD   →  no se llama: BUDGET_EXCEEDED, etiqueta SIN_LLM
```

Al terminar una llamada, su reserva se sustituye por el coste real. Así el tope no se
supera ni con respuestas largas ni con llamadas simultáneas.

**Precios:** en la configuración, con la fuente y la fecha de la sección 0. Se verifican
antes de cada cambio de modelo.

**Estimación de coste** (1.200 tokens de entrada y 300 de salida, precios de la
sección 0; el coste real lo medirá el piloto):

| Modelo | Por llamada (máx.) | A 9,32 grupos/día | A 25 grupos/día | Llamadas/día con 1 USD |
|---|---|---|---|---|
| Sonnet 5.5 | 0,0054 USD | 0,050 USD | 0,135 USD | 185 |
| Haiku 4.5 | 0,0027 USD | 0,025 USD | 0,068 USD | 370 |
| Opus 5.5 | 0,0108 USD | 0,101 USD | 0,270 USD | 92 |
| Fable 5.1 | 0,0270 USD | 0,252 USD | 0,675 USD | 37 |

Con Sonnet 5.5 el gasto mensual (30 días) es de unos **1,51 USD** a 9,32 grupos/día y
de unos **4,05 USD** a 25. El corte diario de 1 USD solo se activaría por encima de 185
llamadas al día. Si las features reales superan los 1.200 tokens de entrada, el coste
sube de forma lineal. El piloto medirá los tokens reales y, si difieren, se recalcula.

**Número de grupos por día que se usa:**

- **9,32 grupos/día (central):** es el ritmo OOS del backtest v2 de las seis estrategias
  (4.846 operaciones en 520,17 días, `docs/FASE2_REEJECUCION.md`, sección 5.3). **No es
  una medida de grupos de señal:** las operaciones del backtest bloquean solapes dentro
  de la misma celda y la sombra no los bloquea, así que el número real de grupos por
  día se conocerá con la sombra en vivo.
- **25 grupos/día (cota superior):** el presupuesto de señales de `docs/FASE3_PLAN.md`.

**Caché de prompt:** el mensaje de sistema es estático y se puede cachear (escritura
1,25 veces el precio de entrada, lectura 0,10 veces). No se incluye en la estimación:
es un ahorro adicional, no un requisito.

---

## (f) Placebo aleatorio: calibrar qué produce el azar

**Objetivo:** comprobar que el procedimiento de medición no inventa efectos. Si un
etiquetado aleatorio con la misma tasa de aprobación que el LLM produce "APORTA_VALOR"
con frecuencia, el criterio está mal calibrado.

**Procedimiento:**

1. Tomar las operaciones sombra cerradas con su PnL real (sin grupos `PILOTO`).
2. Asignar a cada **conglomerado** (no a cada operación, para conservar la dependencia)
   una etiqueta APROBADA con probabilidad `p`, igual a la tasa de aprobación observada
   del LLM en la medición. Las operaciones de un mismo conglomerado reciben la misma
   etiqueta.
3. Aplicar exactamente la misma regla de decisión de la sección (i), con el mismo
   bootstrap por conglomerados y la misma semilla.
4. Repetir 1.000 veces. Registrar la frecuencia de **APORTA_VALOR**.

**Criterio de calibración (sin cambios):** la frecuencia de APORTA_VALOR debe ser menor
que 0,05 (contraste unilateral, con un nivel de significación del 5 %).

**Intervalo de confianza de la frecuencia (informativo).** Además del punto, se reporta
el intervalo de Wilson al 95 % de esa frecuencia, porque con 1.000 repeticiones el propio
placebo tiene error de muestreo. Ejemplos con 1.000 repeticiones:

| Frecuencia observada | IC 95 % de Wilson |
|---|---|
| 0,000 | [0,000; 0,004] |
| 0,020 | [0,013; 0,031] |
| 0,050 | [0,038; 0,065] |

Si la frecuencia supera 0,05, el criterio no está bien calibrado y no se usa para
decidir hasta corregirlo. El placebo se registra en este documento con sus resultados
antes de la primera decisión real.

El placebo no sustituye a la regla de decisión: la calibra. Con el criterio `lo > 0` y un
IC bilateral del 95 %, la probabilidad de APORTA_VALOR sin efecto real ronda el 2,5 %,
por debajo del umbral del placebo.

---

## (g) La cuenta real solo abre con APROBADA y a tiempo; cómo se prueba sin red ni gasto

**Regla de apertura real:** una señal abre posición en la cuenta real solo si:

1. `AUTO_OPEN_WITHOUT_LLM = true`,
2. su etiqueta es **APROBADA**, y
3. la decisión llegó a tiempo: `decision_delay_s <= LLM_REAL_MAX_DELAY_SECONDS` (60 s por
   defecto, sección l).

Con `AUTO_OPEN_WITHOUT_LLM=false` (el valor por defecto, que **no cambia** hasta que tú
lo decidas), nada abre posiciones reales. Las sombras siguen registrándose con su
etiqueta, con independencia de la regla 3.

**Pruebas sin red ni gasto:**

- El cliente del LLM es una interfaz (`decide(request) -> RawResponse`). En los tests
  se inyecta un **cliente falso** con respuestas programadas: `APROBAR`, `RECHAZAR`,
  JSON malformado, campo extra, texto fuera del JSON, timeout, error HTTP y presupuesto
  agotado.
- Un test comprueba que no se construye ningún cliente real: la fábrica del cliente se
  parchea para lanzar un error si se invoca.
- Los tests verifican: la etiqueta correcta en cada caso, una fila de `llm_logs` por
  llamada, que el fallo deja SIN_LLM y nunca RECHAZADA, que la etiqueta no cambia tras
  escribirse (el disparador lanza error), que con `AUTO_OPEN_WITHOUT_LLM=false` no se
  abre ninguna posición real aunque la etiqueta sea APROBADA, y que una decisión con
  `decision_delay_s` mayor que 60 s no abre posición real aunque sea APROBADA, pero la
  sombra sí se conserva con su etiqueta.
- La prueba de fuga de la sección (a) y el placebo de la sección (f) se ejecutan sin
  red.
- Un test de concurrencia: con más grupos que el semáforo, el gasto reservado nunca
  supera `LLM_DAILY_BUDGET_USD`.

---

## (h) IDs en `.env` y script de una sola llamada

**IDs:** van en `.env`, nunca en el código. Variables propuestas:

```
ANTHROPIC_API_KEY=            # solo en .env; el asistente NO la lee
LLM_MODEL=claude-sonnet-5-5   # ID verificado; confirmar en la consola
LLM_MAX_TOKENS=300
LLM_DAILY_BUDGET_USD=1.0
LLM_TIMEOUT_SECONDS=20
LLM_MAX_CONCURRENCY=4
LLM_REAL_MAX_DELAY_SECONDS=60
LLM_PRICE_INPUT_PER_MTOK=2.0  # fuente: sección 0, consultada 2026-10-04
LLM_PRICE_OUTPUT_PER_MTOK=10.0
```

El `.env.example` mantiene la clave vacía.

**Script de prueba de una sola llamada (lo ejecutas tú):** `scripts/llm_smoke.py`.
Lee la configuración de `.env`, envía una única petición mínima (un par de frases,
`max_tokens=50`), imprime el modelo, los tokens de entrada y salida, el coste y la
latencia, y no escribe en la base de trabajo. Así verificas que la clave, el modelo y
el precio son correctos, con un gasto de céntimos.

**Dependencia nueva:** el SDK oficial de Anthropic, asíncrono. Lo autorizaste; se añade
a `pyproject.toml` en la implementación.

---

## (i) Regla de decisión sobre el valor de la IA (tres veredictos)

**Punto de análisis fijado de antemano: 300 conglomerados efectivos por lado.** El
veredicto se calcula **una sola vez**, cuando ambos lados alcanzan 300 conglomerados
efectivos. Hasta entonces el informe muestra solo contadores: N bruto, N efectivo por
lado, tasa de aprobación y SIN_LLM. **No muestra el veredicto.** No se mira el veredicto
antes del punto fijado.

**Definiciones.** `Δ` = esperanza por operación APROBADA menos RECHAZADA, sobre
conglomerados (`app/trading/ai_value.py`, ya existente desde la 3.5, con bootstrap de
2.000 remuestreos y semilla fija). `IC` = intervalo de confianza del 95 % de `Δ`, con
`lo` y `hi` sus extremos. `δ` = margen de no valor, propuesto `+0,3 USDT` por operación.

| Veredicto | Condición | Significado |
|---|---|---|
| **APORTA_VALOR** | `lo > 0` | La decisión del LLM mejora la esperanza por operación. |
| **NO_APORTA_VALOR** | `hi < δ` | El efecto, como mucho, queda por debajo de δ. No compensa el coste ni la dependencia de la API. |
| **INCONCLUSO** | el resto | La muestra no separa efecto y ausencia de efecto. Se indica el motivo. |

**Orden de evaluación:** primero `lo > 0`, luego `hi < δ`, si no INCONCLUSO. Motivos
posibles de INCONCLUSO: N efectivo insuficiente, sin remuestreos válidos, sin datos en
ambos lados, o IC que cruza entre δ y cero.

**Justificación de δ = +0,3 USDT.** Es el 3 % del margen por operación (10 USDT) y
0,067 veces la desviación típica por operación (sección j). Un filtro que mejore la
esperanza en menos de eso no compensa la dependencia operativa de una API externa
(latencia, cortes, cambios de modelo). El coste de cada llamada (0,0054 USD como máximo)
es mucho menor que δ, así que δ mide el valor de la decisión, no su precio.

**Caso no separado por la regla.** Si `lo > 0` y `hi < δ`, la regla da APORTA_VALOR, pero
el efecto está por debajo de δ. Propuesta: APORTA_VALOR con la marca `MAGNITUD_BAJA` en
el informe. Queda como decisión tuya (pendientes).

**Cambio en la implementación:** `ai_value_verdict` pasa de dos veredictos a tres, con
`δ` configurable (`LLM_NO_VALUE_DELTA_USDT=0.3`) y `MIN_EFFECTIVE_N` de 100 a 300. Se
actualizan `app/trading/ai_value.py` y `tests/unit/test_ai_value.py` al implementar.

---

## (j) Potencia estadística

**Desviación típica por operación.** Se calcula sobre `backtest_trades` con
`segment = 'OOS'` de la base v2 (`data/backups/minerva_fase2_v2.db`, consulta de solo
lectura). `backtest_runs` no guarda varianza por operación, por eso la fuente es
`backtest_trades.pnl_net_usdt`.

- Operaciones OOS: 4.846 (coincide con la sección 5.3 de `FASE2_REEJECUCION.md`).
- Desviación típica conjunta: **σ = 4,49 USDT** por operación.
- Por estrategia: 2,89 (`mean_reversion_rsi14_bb20`, 2.737 ops), 3,66 (`trend_atr_stop`,
  493), 5,70 (`donchian_breakout_20`, 749), 7,06 (`ema_cross_9_21`, 556), 7,19
  (`funding_contrarian_percentile`, 290) y 7,67 (`funding_contrarian_experimental`, 21).
  La mezcla real de la sombra puede tener otra σ; la estimación se revisa con la sombra.

**Error típico de la diferencia** con N conglomerados por lado (igual N en ambos lados):

```
EE(N) = σ * sqrt(2 / N)
```

**Efecto mínimo detectable** (α = 5 % bilateral, potencia 80 %): `MDE = 2,80 * EE(N)`.

| N efectivo por lado | Error típico de Δ | Efecto mínimo detectable | APORTA_VALOR si Δ estimado es mayor que | NO_APORTA_VALOR si Δ estimado es menor que |
|---|---|---|---|---|
| 100 | 0,635 | 1,78 USDT | +1,25 | −0,95 |
| 200 | 0,449 | 1,26 USDT | +0,88 | −0,58 |
| **300** | **0,367** | **1,03 USDT** | **+0,72** | **−0,42** |
| 400 | 0,318 | 0,89 USDT | +0,62 | −0,32 |

(Los umbrales de las dos últimas columnas son aproximados: usan el error típico analítico
en lugar del bootstrap.)

**Lectura de la tabla.** A N = 300 el veredicto solo separa efectos fuera del intervalo
(−0,42; +0,72) USDT por operación. Un efecto real de +0,3 USDT (el δ propuesto) daría
APORTA_VALOR solo en torno al 13 % de las veces (error típico de 0,367; hace falta
Δ estimado > 0,72), y quedaría en INCONCLUSO el resto. Esto es consecuencia de σ = 4,49
y no de la regla: para que el intervalo excluya el cero con un efecto de 0,3 USDT harían
falta unos 1.700 conglomerados por lado (unos 600 días con p = 0,3), y unos 3.500 para
una potencia del 80 %. Lo registro para que la decisión sea consciente.

**Limitaciones de la estimación:**

- Trata cada conglomerado como una observación con la σ por operación. Un conglomerado
  agrupa varias operaciones solapadas, así que su varianza real suele ser mayor. Por eso
  las cifras de la tabla son **cotas inferiores** del error.
- La σ viene del backtest, no de la sombra en vivo.

**Tiempo estimado hasta el punto de análisis.** Con 9,32 grupos/día y tasa de aprobación
`p`, el lado minoritario se llena al ritmo `9,32 · min(p, 1−p)` conglomerados por día.
Los días hasta que ambos lados alcanzan N son:

| Tasa de aprobación | Ritmo del lado minoritario | N = 100 | N = 200 | N = 300 | N = 400 |
|---|---|---|---|---|---|
| 30 % | 2,80 por día | 36 días | 72 días | **107 días** | 143 días |
| 50 % | 4,66 por día | 22 días | 43 días | **64 días** | 86 días |
| 70 % | 2,80 por día | 36 días | 72 días | **107 días** | 143 días |

Son **cotas inferiores**: N efectivo ≤ N bruto (los solapes reducen los conglomerados), y
9,32 grupos/día es una referencia (sección e). El tiempo real se medirá con
`scripts/shadow_report.py` sobre una copia de la base en vivo.

---

## (k) Piloto con prompt congelado

**Objetivo:** medir en la práctica validez del JSON, tokens reales, latencia y tasa de
aprobación antes de que empiece la medición.

**Procedimiento:**

1. Se fija `prompt_version` y la plantilla. Se registra su hash.
2. Se procesan **30 a 50 señales** reales en vivo con `fase = 'PILOTO'`. Coste máximo del
   piloto: 50 × 0,0054 = **0,27 USD**.
3. Se miden: validez del JSON (%), tokens de entrada y salida (media y p95), latencia
   (media y p95), tasa de APROBAR, y coste real.
4. Los grupos `PILOTO` se excluyen de la medición: el análisis filtra por
   `llm_logs.fase = 'MEDICION'`. Sus etiquetas quedan en la sombra, pero no cuentan.

**Banda de aprobación 15 %–85 %:**

- Si la tasa del piloto está dentro de la banda, se congela el prompt y empieza la
  medición.
- Si está fuera, se ajusta el prompt **antes** de empezar la medición, se cambia
  `prompt_version`, y se repite el piloto. El piloto anterior queda en `llm_logs` con
  `fase = 'PILOTO'` como registro.

**Congelación:** una vez empezada la medición, cualquier cambio de texto del prompt
cambia `prompt_version` y **reinicia el recuento** de la medición. Las filas de la
versión anterior no se mezclan con las nuevas.

**Efecto del piloto en las cifras:** si los tokens reales de entrada no son 1.200, se
recalcula la sección (e) con el valor medido.

---

## (l) Concurrencia, retraso de la cuenta real y plazos

**Paralelismo.** Las decisiones se lanzan como tareas asíncronas. El ciclo de señales no
espera a la API. El número máximo de llamadas simultáneas lo limita un semáforo de
`LLM_MAX_CONCURRENCY` (por defecto 4).

**Tiempo máximo por llamada:** 20 s (`LLM_TIMEOUT_SECONDS`). Un timeout deja SIN_LLM con
`status = TIMEOUT`.

**Retraso de la cuenta real.** Se mide `decision_delay_s` como el instante de la decisión
menos el cierre de la vela. Si supera `LLM_REAL_MAX_DELAY_SECONDS` (60 s por defecto), la
cuenta real **no abre** aunque la etiqueta sea APROBADA. Motivo: el precio de entrada real
es el del momento de la apertura, no el de cierre de vela que usa la sombra; un retraso
grande haría que la cuenta real y la sombra no fueran comparables. La sombra no depende de
este retraso.

**Asimetría que queda.** Con retrasos de hasta 60 s, la entrada real puede diferir de la
de la sombra. El informe compara el precio de entrada real con el de la sombra en las
operaciones que se abrieron en ambas, y lo reporta.

**Plazos de medición.** El punto de análisis es de 300 conglomerados por lado (sección i).
No hay fecha límite de medición en esta propuesta (pendientes).

---

## (m) Reporte de SIN_LLM y detección de sesgo de exclusión

Las decisiones que fallan (SIN_LLM) no entran en la comparación. Si fallan de forma
no aleatoria (p. ej. solo en alta volatilidad), la comparación se hace sobre una muestra
sesgada. Por eso el informe reporta:

1. **Tasa de SIN_LLM por causa:** `TIMEOUT`, `INVALID` (JSON), `BUDGET_EXCEEDED`,
   `ERROR_HTTP`. Número y porcentaje sobre el total de grupos.
2. **Distribución de SIN_LLM por franja horaria UTC** (6 franjas de 4 horas) y **por
   tercil de volatilidad** (`atr_pct`, con los terciles de la muestra). Se compara con la
   distribución de todos los grupos.
3. **Regla propuesta:** si el SIN_LLM supera el 10 % de los grupos de la medición, el
   informe lo marca y no emite veredicto hasta revisar las causas. Pendiente de tu
   aprobación.

---

## Pendientes de tu decisión

1. **Margen δ = +0,3 USDT por operación.** Aprobar o indicar otro valor.
2. **Punto de análisis = 300 conglomerados efectivos por lado.** Aprobar. Con 9,32 grupos/día
   y tasa de aprobación de 30 % o 70 %, son unos 107 días (cota inferior, sección j).
3. **Caso `lo > 0` y `hi < δ`:** propuesta de APORTA_VALOR con la marca `MAGNITUD_BAJA`.
   ¿De acuerdo, o prefieres otro tratamiento?
4. **Fecha límite de medición:** propuesta, ninguna. El punto de análisis es fijo. El gasto
   con Sonnet 5.5 es de unos 1,5 USD/mes a 9,32 grupos/día, así que el coste de esperar no
   es un problema. Confirmar.
5. **Límite de SIN_LLM del 10 %** para no emitir veredicto (sección m). Aprobar o cambiar.
6. **Modelo:** `claude-sonnet-5-5` (verificado y aprobado). El cambio de `claude-sonnet-5`
   en `app/config.py` se hace al implementar.
7. **SDK de Anthropic:** autorizado; se añade al implementar.
8. **Precios:** confirmar en tu consola los de la sección 0 antes de la primera llamada real.
