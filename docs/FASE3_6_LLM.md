# Subfase 3.6 -- Decisión del LLM sobre las señales (DISEÑO v2, APROBADO)

Estado: **fases (i), (ii), (iii) y (iv) implementadas (2026-10-09); nada conectado al
bot todavía**. Lo único que sigue pendiente de ti, y no bloquea lo ya escrito, es confirmar
los precios de la sección 0 en tu consola antes de la primera llamada real
(`scripts/llm_smoke.py` o `scripts/llm_pilot.py`, ambos con `--real --confirm-real`).
Conectar el LLM al ciclo real del bot (`AUTO_OPEN_WITHOUT_LLM=true`) es una decisión
aparte, no tomada.

Qué mide la 3.6: si la decisión del LLM sobre cada señal agrupada (APROBAR o RECHAZAR)
aporta valor frente a no filtrar nada, comparando la esperanza por operación de las
operaciones sombra APROBADA y RECHAZADA (`docs/FASE3_PLAN.md`, punto 2). La cuenta real
solo abre con etiqueta APROBADA, y solo si la decisión llega a tiempo (sección l).

---

## Cambios de la versión 2

1. **Regla de decisión con tres veredictos** (sección i), con un punto de análisis fijado
   de antemano (300 conglomerados efectivos por lado) y un margen de efecto `δ` expresado
   como **% del margen por operación** (no en USDT fijos), para que valga igual con
   margen 5 o 10 USDT. "Sin evidencia" ya no equivale a "la IA no aporta valor".
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

**Configuración actual:** `app/config.py` ya tiene por defecto
`anthropic_sonnet_model = "claude-sonnet-5-5"` (cambiado del no verificado
`claude-sonnet-5`, commit `eb527bb`, previo a implementar la 3.6). Confirmarás los
precios en tu consola antes de la primera llamada real.

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

**Parámetros de la llamada (corregido el 2026-10-09 contra la documentación vigente de
la API de Anthropic -- ver nota de determinismo abajo):**

- **Sin `temperature`.** En Sonnet 5.5 (y en el resto de la generación 5.x) un valor de
  `temperature`/`top_p`/`top_k` distinto del de la API devuelve `400`; la versión
  anterior de este documento pedía `temperature = 0.0`, que ya no es válido. Se omite el
  parámetro.
- **`thinking = {"type": "between_tools"}`, `output_config = {"effort": "low"}`.** Es el
  ajuste de menor razonamiento disponible en Sonnet 5.5 (`{"type": "disabled"}` devuelve
  `400` en este modelo). Sin herramientas declaradas en la llamada, no genera bloques de
  razonamiento extendido que consuman `max_tokens`, así que los 300 tokens quedan
  enteros para la respuesta JSON. Requiere efecto `high` o menor; `low` es válido.
- **Nota de determinismo.** Sin control de `temperature`, la misma entrada ya no
  garantiza la misma decisión con tanta fuerza como en el diseño original. Es una
  limitación real de la API vigente, no una eleccion de diseño; `confianza` y
  `razonamiento` siguen dando una señal de que tan ajustada fue la decision.
- `max_tokens = 300` (antes 150). Acota la salida y el coste máximo por llamada.
- Una sola llamada por grupo, **sin reintentos**: un reintento retrasa la decisión y
  puede sesgar la comparación. Un fallo deja la etiqueta SIN_LLM (sección d).
- Tiempo máximo de espera: 20 segundos por llamada (`client.with_options(timeout=20)`).
- **`max_retries = 0` en el cliente del SDK (ajuste 1 a la fase i, 2026-10-09).** El
  SDK reintenta por defecto timeouts, 408/409/429 y `>=500` con `max_retries=2`: con
  `timeout=20s` eso puede estirar una sola llamada "lógica" hasta `20s *
  (reintentos+1)` = 60 s en el peor caso, justo el límite de `LLM_REAL_MAX_DELAY_
  SECONDS` (sección l). Como ya se pide "sin reintentos" por diseño, `max_retries=0`
  hace que eso también valga a nivel de transporte: un fallo deja SIN_LLM dentro de
  los 20 s, no en silencio hasta 60 s después.

**Plantilla (mensaje de sistema, estático):**

```
Eres un revisor de señales de trading de criptomonedas en paper trading.
Recibes una señal ya generada por reglas técnicas y debes decidir si se
APRUEBA o se RECHAZA. Decides solo con los datos del mensaje; no tienes
acceso a nada más. No predices precios. Responde SOLO con un objeto JSON
válido que siga exactamente el esquema indicado, sin texto adicional.
Esquema: {"decision": "APROBAR" | "RECHAZAR", "confianza": número entre 0 y 1,
"razonamiento": texto de máximo 300 caracteres}. JSON crudo: sin bloques de
código, sin ```json ni ``` de ningún tipo, sin texto antes ni después.
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
La respuesta cruda se guarda igualmente en `llm_logs`. Esto incluye una respuesta
envuelta en un bloque de código Markdown (` ```json ... ``` ` o ` ``` ... ``` `): el
prompt lo prohíbe explícitamente, y aunque el modelo lo haga de todos modos, el
parser JSON de pydantic la rechaza igual que cualquier otro texto fuera del objeto
-- no hace falta (ni se agrega) un paso que le quite las comillas invertidas antes
de validar.

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

**Coste estimado en TIMEOUT y ERROR_HTTP (ajuste 2 a la fase i, 2026-10-09).** Un
timeout o un error HTTP de nuestro lado no garantiza que Anthropic no haya generado
(y facturado) nada: la respuesta pudo completarse del otro lado sin llegar a tiempo.
Por eso estos dos estados registran en `llm_logs.cost_usd` una estimación
conservadora -- solo el coste de los tokens de entrada estimados, sin salida -- en vez
de 0, y el campo `error` termina con "(coste estimado)" para distinguirlo de un coste
real. Registrar 0 subestimaría `gasto_hoy` frente al tope diario.

**El tope diario NO es acumulativo entre copias de la base (ajuste 6, fase iv,
2026-10-09).** `gasto_hoy` es `SUM(cost_usd)` sobre `llm_logs` de la base que recibe
`--db`, no sobre todas las llamadas que hayas hecho hoy en total. Si corrés
`scripts/llm_smoke.py`/`llm_pilot.py` dos veces con `--db` apuntando a dos copias
DISTINTAS, cada una ve `gasto_hoy = 0` al empezar, sin memoria de la otra -- el tope
de `LLM_DAILY_BUDGET_USD` solo protege DENTRO de una misma base. La protección real
contra un gasto mayor al querido, al usar copias repetidas, es el `--max-calls` de
cada script (tope duro 3 en el smoke, 50 en el piloto) y el límite de uso/gasto que
se configure en la consola de Anthropic -- no asumas que el tope diario del `.env` te
cubre entre corridas con copias distintas.

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
ANTHROPIC_API_KEY=                 # solo en .env; el asistente NO la lee
ANTHROPIC_SONNET_MODEL=claude-sonnet-5-5   # ya en config.py; verificado (sección 0)
LLM_MAX_TOKENS=300
LLM_DAILY_BUDGET_USD=1.0
LLM_TIMEOUT_SECONDS=20
LLM_MAX_CONCURRENCY=4
LLM_REAL_MAX_DELAY_SECONDS=60
LLM_PRICE_INPUT_PER_MTOK=2.0        # fuente: sección 0, consultada 2026-10-04
LLM_PRICE_OUTPUT_PER_MTOK=10.0
LLM_NO_VALUE_DELTA_PCT=0.03         # δ, fraccion del margen por operacion
LLM_MAX_SIN_LLM_SHARE=0.10          # tope de SIN_LLM antes de INCONCLUSO por sesgo
```

No se añade `LLM_MODEL`: se reutiliza `ANTHROPIC_SONNET_MODEL`, que ya existe en
`app/config.py` desde antes de la 3.6, para no duplicar la misma configuración con dos
nombres. El `.env.example` mantiene la clave vacía.

**Implementado en la fase (iii) (2026-10-09).** Tres scripts, todos sobre una COPIA de
la base (nunca `settings.database_path`, verificado con assert) y en `--dry-run` por
defecto (cliente falso, sin red ni coste); una llamada real necesita `--real` y
`--confirm-real` juntos:

- `scripts/llm_smoke.py`: una sola llamada mínima (`--max-calls`, por defecto 1, tope
  duro 3) sobre un grupo de señal ya guardado. Imprime modelo, `prompt_version`,
  tokens de entrada y salida reales, coste, latencia, la respuesta cruda y si pasó la
  validación. Nunca imprime `ANTHROPIC_API_KEY`. Lo ejecutas tú con `--real
  --confirm-real` para confirmar clave/modelo/precio con un gasto de céntimos.
- `scripts/llm_pilot.py`: el piloto de la sección anterior (30 a 50 señales, tope
  duro 50), con las mismas protecciones.
- `scripts/placebo_check.py`: la calibración de la sección (f), aparte del reporte
  normal (`ai_value_verdict` nunca llama a `placebo_calibration` por su cuenta).

Los tres reutilizan `LlmDecisionService` (fase i): la reserva de presupuesto corre
dentro, antes de cualquier llamada real.

**Dependencia nueva:** el SDK oficial de Anthropic, asíncrono (`anthropic>=1.10,<2`,
ya en `pyproject.toml`). Lo autorizaste.

---

## (i) Regla de decisión sobre el valor de la IA (tres veredictos)

**Punto de análisis fijado de antemano: 300 conglomerados efectivos por lado.** El
veredicto se calcula **una sola vez**, cuando ambos lados alcanzan 300 conglomerados
efectivos. Hasta entonces el informe muestra solo contadores: N bruto, N efectivo por
lado, tasa de aprobación y SIN_LLM. **No muestra el veredicto.** No se mira el veredicto
antes del punto fijado.

**Definiciones.** Para cada operación, `r = pnl_net_usdt / margin_usdt` (fracción del
margen de esa operación, no USDT absolutos: así una operación con margen 5 y otra con
margen 10 contribuyen en las mismas unidades). `Δ` = esperanza de `r` por operación
APROBADA menos RECHAZADA, sobre conglomerados (`app/trading/ai_value.py`, ya existente
desde la 3.5, con bootstrap de 2.000 remuestreos y semilla fija). `IC` = intervalo de
confianza del 95 % de `Δ`, con `lo` y `hi` sus extremos. `δ` = margen de no valor, fijado
en **3 % del margen por operación** (`δ = 0,03`).

| Veredicto | Condición | Significado |
|---|---|---|
| **APORTA_VALOR** | `lo > 0` | La decisión del LLM mejora la esperanza por operación (como fracción del margen). |
| **NO_APORTA_VALOR** | `hi < δ` | El efecto, como mucho, queda por debajo de δ. No compensa el coste ni la dependencia de la API. |
| **INCONCLUSO** | el resto, o SIN_LLM > 10 % de la muestra (sección m) | La muestra no separa efecto y ausencia de efecto, o puede estar sesgada por exclusión. Se indica el motivo. |

**Orden de evaluación:** primero, si el SIN_LLM de la medición supera el 10 % de los
grupos, INCONCLUSO por posible sesgo de exclusión (sección m) sin mirar el IC. Si no,
primero `lo > 0`, luego `hi < δ`, si no INCONCLUSO. Motivos posibles de INCONCLUSO: SIN_LLM
> 10 %, N efectivo insuficiente, sin remuestreos válidos, sin datos en ambos lados, o IC
que cruza entre δ y cero.

**Justificación de δ = 3 % del margen.** Equivale a 0,067 veces la desviación típica por
operación medida en el backtest (σ = 44,9 % del margen, sección j). Un filtro que mejore
la esperanza en menos de eso no compensa la dependencia operativa de una API externa
(latencia, cortes, cambios de modelo). El coste de cada llamada (0,0054 USD como máximo)
no depende del margen y es mucho menor que δ en cualquier caso realista, así que δ mide
el valor de la decisión, no su precio. Expresarlo como fracción del margen (y no en USDT
fijos) significa que el criterio no cambia si más adelante se vuelve a ajustar
`DEFAULT_MARGIN_USDT`, y que las operaciones de antes y después de ese cambio se
comparan correctamente en la misma unidad.

**Caso no separado por la regla (decidido).** Si `lo > 0` y `hi < δ`, el veredicto es
**APORTA_VALOR** con la marca `MAGNITUD_BAJA` en el informe: el efecto es real pero
pequeño.

**Implementado en la fase (ii) (2026-10-09).** `ai_value_verdict` tiene los tres
veredictos, opera sobre `r = pnl_net_usdt / margin_usdt` en vez de `pnl_net_usdt`, con
`δ` (`delta=0.03` por defecto), `MIN_EFFECTIVE_N=300` y el tope de SIN_LLM
(`max_sin_llm_share=0.10`) como parametros de la funcion -- igual que `min_effective_n`
ya lo era desde la 3.5, no se añadieron a `app/config.py` ni a `.env` (no hay nada hoy
que los lea de ahi; se agregan si una llamada real a la API llega a necesitarlo). El
filtro de `llm_logs.fase = 'PILOTO'` lo hace el llamador (`piloto_keys`, resuelto con
`llm_logs_repo.get_piloto_signal_group_keys`), no `ai_value_verdict` -- la funcion sigue
sin tocar la base. `app/trading/ai_value.py` tambien agrega `placebo_calibration`
(seccion f) con el intervalo de Wilson. Nada de esto esta conectado al ciclo del bot.

---

## (j) Potencia estadística

**Desviación típica por operación, como fracción del margen.** Se calcula sobre
`backtest_trades` con `segment = 'OOS'` de la base v2
(`data/backups/minerva_fase2_v2.db`, consulta de solo lectura), con `r = pnl_net_usdt /
margin_usdt` (la Fase 2 se corrió con margen 10, así que `r = pnl_net_usdt / 10`).
`backtest_runs` no guarda varianza por operación, por eso la fuente es
`backtest_trades.pnl_net_usdt`. Expresar σ como fracción del margen, y no en USDT, es lo
que permite que la tabla de potencia valga igual con `DEFAULT_MARGIN_USDT=5` que con 10.

- Operaciones OOS: 4.846 (coincide con la sección 5.3 de `FASE2_REEJECUCION.md`).
- Desviación típica conjunta: **σ = 4,49 USDT / 10 = 44,9 % del margen** por operación.
- Por estrategia (en USDT sobre margen 10, es decir, en % del margen dividiendo por 10):
  2,89 (`mean_reversion_rsi14_bb20`, 2.737 ops; 28,9 %), 3,66 (`trend_atr_stop`, 493;
  36,6 %), 5,70 (`donchian_breakout_20`, 749; 57,0 %), 7,06 (`ema_cross_9_21`, 556;
  70,6 %), 7,19 (`funding_contrarian_percentile`, 290; 71,9 %) y 7,67
  (`funding_contrarian_experimental`, 21; 76,7 %). La mezcla real de la sombra puede
  tener otra σ; la estimación se revisa con la sombra.

**Error típico de la diferencia** con N conglomerados por lado (igual N en ambos lados),
en fracción del margen:

```
EE(N) = σ * sqrt(2 / N)      (σ = 0,449)
```

**Efecto mínimo detectable, con la potencia indicada en cada columna** (α = 5 %
bilateral; el umbral de potencia 50 % es, por construcción, el mismo valor que separa
APORTA_VALOR de INCONCLUSO en el punto de análisis):

| N efectivo por lado | Error típico de Δ | MDE, potencia 50 % (`1,96·EE`) | MDE, potencia 80 % (`2,80·EE`) |
|---|---|---|---|
| 100 | 6,35 % | 12,45 % | 17,80 % |
| 200 | 4,49 % | 8,81 % | 12,59 % |
| **300** | **3,67 %** | **7,19 %** | **10,28 %** |
| 400 | 3,18 % | 6,23 % | 8,90 % |

(Son cifras analíticas, no del bootstrap; la tabla de la sección anterior usaba las
mismas cifras en USDT sobre margen 10 -- por ejemplo, 7,19 % × 10 USDT = 0,72 USDT -- y
son idénticas en proporción porque δ ya era 3 % del margen en los dos casos.)

**La frase pedida explícitamente:** con N = 300 conglomerados efectivos por lado, el
efecto mínimo detectable ronda el **10 % del margen por operación** (potencia 80 %). Un
efecto del tamaño de δ (**3 % del margen**) necesitaría del orden de **3.500
conglomerados por lado con potencia 80 %**; la cifra de **~1.700** citada en la versión
anterior de este documento corresponde a una **potencia del 50 %** (el umbral exacto de
`lo > 0`, no una detección confiable). Con 3.500 por lado y 9,32 grupos/día, el lado
minoritario tarda del orden de 1.250 días con `p = 0,3` o `p = 0,7` (cota inferior, ver
la tabla de días más abajo, extrapolada).

**Qué veredicto esperar en la práctica, con N = 300 por lado (ajuste de la fase ii).**
El IC 95 % mide aproximadamente ±7 puntos del margen alrededor de `Δ` (1,96 × 3,67 %,
de la tabla de arriba). Para que `hi < δ` (NO_APORTA_VALOR) con `δ = 3 %`, hace falta
`Δ estimado < 3 % − 7 % ≈ −4 %`: la IA tiene que restar valor con claridad, no solo "no
ayudar". Con σ = 44,9 % del margen, lo esperable en la práctica a N = 300 es
**INCONCLUSO** (si el efecto real es chico o nulo) o **APORTA_VALOR** (si es claramente
positivo, con o sin la marca `MAGNITUD_BAJA`); NO_APORTA_VALOR es el veredicto menos
probable de los tres a este N, no por una falla de la regla, sino porque distinguir
"sin efecto" de "efecto negativo claro" exige más potencia que distinguir "sin efecto"
de "efecto positivo claro" cuando δ está tan cerca de cero.

**Limitaciones de la estimación:**

- Trata cada conglomerado como una observación con la σ por operación. Un conglomerado
  agrupa varias operaciones solapadas, así que su varianza real suele ser mayor. Por eso
  las cifras de la tabla son **cotas inferiores** del error.
- La σ viene del backtest con margen 10, no de la sombra en vivo con margen 5; al
  expresarla como fracción del margen asumimos que el riesgo por operación (no el PnL en
  USDT) es comparable entre ambos tamaños de margen -- razonable porque el SL se define
  como % del margen (`sl_margin_loss_pct`), igual con margen 5 que con 10.

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
2. Se procesan **30 a 50 señales** con `fase = 'PILOTO'`. Coste máximo del piloto:
   50 × 0,0054 = **0,27 USD**.
3. Se miden: validez del JSON (%), tokens de entrada y salida (media y p95), latencia
   (media y p95), tasa de APROBAR, y coste real.
4. Los grupos `PILOTO` se excluyen de la medición: el análisis filtra por
   `llm_logs.fase = 'MEDICION'`. Sus etiquetas quedan en la sombra, pero no cuentan.

**Implementado como "por repetición", no en vivo (fase iii-C, `scripts/llm_pilot.py`).**
El piloto corre en lote sobre grupos de señal que YA estan guardados en una copia de
la base (`shadow_trades` sin fila en `llm_logs`), no conectado al ciclo real del bot.
Motivo: conectarlo en vivo necesitaria wirear `app/core/scheduler.py` a la decision
del LLM -- exactamente lo que `AUTO_OPEN_WITHOUT_LLM=false` todavia impide, y que es
una decision aparte, no tomada en la 3.6. Corriendo por repeticion se mide lo mismo
(validez del JSON, tokens, latencia, tasa de aprobacion) sin esa conexion. El propio
piloto mide el presupuesto de la misma forma que una llamada en vivo (reserva bajo
lock, seccion e), asi que esa parte si es representativa.

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
No hay fecha límite de medición (decidido): el punto de análisis es fijo y el gasto de
esperar es bajo (sección e).

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
3. **Regla (decidida):** si el SIN_LLM supera el **10 %** de los grupos de la medición
   (`LLM_MAX_SIN_LLM_SHARE=0.10`), el veredicto es **INCONCLUSO** por posible sesgo de
   exclusión, sin mirar el intervalo de confianza (sección i). El informe muestra de
   todos modos los contadores y la distribución por causa/hora/volatilidad, para que se
   pueda investigar por qué.

---

## Decisiones (cerradas el 2026-10-09)

1. **δ = 3 % del margen por operación** (no USDT fijos; sección i).
2. **Punto de análisis = 300 conglomerados efectivos por lado**, uno solo, fijado de
   antemano (sección i/j). Con 9,32 grupos/día y tasa de aprobación de 30 % o 70 %, son
   unos 107 días (cota inferior).
3. **Caso `lo > 0` y `hi < δ`:** APORTA_VALOR con la marca `MAGNITUD_BAJA` (sección i).
4. **Sin fecha límite de medición** (sección l).
5. **Límite de SIN_LLM del 10 %** antes de INCONCLUSO por posible sesgo de exclusión
   (sección m).
6. **Modelo:** `claude-sonnet-5-5`. Ya cambiado en `app/config.py` y `.env.example`
   (commit `eb527bb`), junto con `DEFAULT_MARGIN_USDT` de 10 a 5 (`docs/FASE3_PLAN.md`).
7. **SDK de Anthropic:** autorizado.
8. **Precios:** los confirmas tú en tu consola antes de la primera llamada real. Es el
   único paso que sigue pendiente, y no bloquea escribir código con el cliente falso.

**Implementación (sección aparte, por fases, cada una con su commit):**

- **(i)** ✅ Cliente del LLM como interfaz + cliente falso, `llm_logs`, etiqueta
  inmutable con disparador, esquema pydantic, presupuesto con reserva, semáforo,
  `max_retries=0`. Tests sin red.
- **(ii)** ✅ `app/trading/ai_value.py` con los tres veredictos (sobre `r =
  pnl_net_usdt/margin_usdt`), `measurement_keys`, potencia y `placebo_calibration`.
  Tests.
- **(iii)** ✅ `app/llm/prompts.py`, `scripts/llm_smoke.py`, `scripts/llm_pilot.py`
  (la ejecuta Renzo) y `scripts/placebo_check.py`.
- **(iv)** ✅ Features de mercado completas (`app/llm/market_features.py`):
  timeframe y niveles planeados por estrategia, funding de `funding_cache`,
  ATR(14) y desviación de retornos en 1h, correlación con BTC a 30 y 100 velas, y
  posiciones REALES abiertas al instante de la decisión. Las 3 pruebas de fuga de
  la sección (a). `SYSTEM_PROMPT` con contexto (cuenta, apalancamiento, qué mide
  cada estrategia) y sin tasa de aprobación objetivo.

`AUTO_OPEN_WITHOUT_LLM` se mantiene en `false`: la 3.6 no activa el uso del LLM real en
el bot hasta una decisión aparte. Nada de lo implementado toca
`app/core/scheduler.py`.
