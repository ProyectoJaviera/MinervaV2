# Subfase 3.6 -- Decisión del LLM sobre las señales (DISEÑO, sin código)

Estado: **diseño pendiente de aprobación**. Este documento no implementa nada. Hasta
que lo apruebes no se escribe código de la 3.6, no se añade el SDK de Anthropic al
proyecto y no se hace ninguna llamada a la API.

Qué mide la 3.6: si la decisión del LLM sobre cada señal agrupada (APROBAR o RECHAZAR)
aporta valor frente a no filtrar nada, comparando la esperanza por operación de las
operaciones sombra APROBADA y RECHAZADA (`docs/FASE3_PLAN.md`, punto 2). La cuenta real
solo abre con etiqueta APROBADA.

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
| Sonnet 5.5 | `claude-sonnet-5-5` | **Verificado** en la tabla de modelos |
| Opus 5.5 | `claude-opus-5-5` | **Verificado** |
| Fable 5.1 | `claude-fable-5-1` | **Verificado** |
| Haiku 4.5 | `claude-haiku-4-5-20251001` (ID fijado); alias `claude-haiku-4-5` | **Verificado**. Se retira no antes del 15-oct-2026 |
| Sonnet 5 | `claude-sonnet-5` | **NO verificado**: la tabla vigente no lo lista; solo aparece como modelo heredado, sin ID visible en el texto que recibí |

**Advertencia sobre la configuración actual:** `app/config.py` tiene por defecto
`anthropic_sonnet_model = "claude-sonnet-5"`, que no está verificado. Antes de cualquier
llamada, la configuración debe usar un ID verificado. La propuesta es `claude-sonnet-5-5`.
Confírmalo tú en la consola antes de cambiarlo.

**Advertencia sobre Haiku:** su retiro no es antes del 15-oct-2026. No conviene basar el
diseño en él.

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
   retornos logarítmicos de las últimas 30 velas cerradas.
6. **Correlación con BTC:** correlación de retornos de las últimas 30 y 100 velas
   cerradas, calculada solo con velas de BTCUSDT cuyo cierre es anterior o igual a la
   vela evaluada.
7. **Posiciones abiertas en el momento de la decisión:** número y dirección de
   posiciones reales y de sombras abiertas, y cuántas son del mismo símbolo. Es
   información de cartera que un operador humano también tendría.

**No entra, por diseño:**

- Cualquier vela posterior al cierre evaluado, y cualquier dato que dependa de ella.
- El resultado de operaciones sombra o reales posteriores a la decisión.
- El historial de aciertos de la propia IA o de la estrategia. En la v1 se excluye
  para no contaminar la comparación: si el LLM recibe su propio historial, la
  comparación APROBADA vs RECHAZADA deja de medir solo el juicio sobre la señal.
- La etiqueta de otras señales del mismo grupo, y el precio o tiempo de la ejecución
  real.

**Prueba de fuga (obligatoria antes de usarlo):** el constructor de features recibe
las velas y el instante de decisión. Un test inyecta velas posteriores con valores
extremos y comprueba que las features no cambian.

---

## (b) Prompt y esquema de salida

**Versión del prompt:** `prompt_version = "signal_review_v1"`. Se guarda en `llm_logs`
junto con un hash SHA-256 de la plantilla. Cualquier cambio de texto cambia la versión.

**Parámetros de la llamada:**

- `temperature = 0.0` (la más baja permitida), para que la misma entrada tienda a la
  misma decisión.
- `max_tokens = 150`, para acotar la salida y el coste máximo por llamada.
- Una sola llamada por grupo, **sin reintentos**: un reintento retrasa la decisión y
  puede sesgar la comparación. Un fallo deja la etiqueta SIN_LLM (ver d).
- Tiempo máximo de espera: 20 segundos.

**Plantilla (mensaje de sistema, estático):**

```
Eres un revisor de señales de trading de criptomonedas en paper trading.
Recibes una señal ya generada por reglas técnicas y debes decidir si se
APRUEBA o se RECHAZA. Decides solo con los datos del mensaje; no tienes
acceso a nada más. No predices precios. Responde SOLO con un objeto JSON
válido que siga exactamente el esquema indicado, sin texto adicional.
Esquema: {"decision": "APROBAR" | "RECHAZAR", "confianza": número entre 0 y 1,
"razonamiento": texto de máximo 400 caracteres}.
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
    razonamiento: str = Field(min_length=1, max_length=400)
```

Una respuesta que no pase la validación (JSON malformado, campo extra, valor fuera de
rango, texto antes o después del JSON) se trata como fallo: etiqueta SIN_LLM. La
respuesta cruda se guarda igualmente en `llm_logs`.

La etiqueta sale de `decision` con un mapeo fijo: `APROBAR` → APROBADA,
`RECHAZAR` → RECHAZADA. La `confianza` se guarda para análisis, pero **no** cambia la
etiqueta.

---

## (c) Tabla `llm_logs`

| Columna | Tipo | Contenido |
|---|---|---|
| `id` | INTEGER PK | |
| `created_at` | TEXT | instante de la llamada (UTC) |
| `signal_group_key` | TEXT | clave única de la señal agrupada (`symbol|side|vela`) |
| `shadow_trade_id` | INTEGER NULL | operación sombra asociada, si se abrió |
| `model` | TEXT | ID exacto de API usado |
| `prompt_version` | TEXT | p. ej. `signal_review_v1` |
| `prompt_sha256` | TEXT | hash de la plantilla de sistema |
| `prompt` | TEXT | mensaje completo enviado (sistema + usuario) |
| `response_raw` | TEXT NULL | respuesta cruda, aunque sea inválida |
| `status` | TEXT | `OK`, `TIMEOUT`, `ERROR_HTTP`, `INVALID`, `BUDGET_EXCEEDED` |
| `error` | TEXT NULL | mensaje de error |
| `input_tokens` | INTEGER NULL | tokens de entrada reportados por la API |
| `output_tokens` | INTEGER NULL | tokens de salida reportados por la API |
| `cost_usd` | REAL | coste calculado con los precios de la sección 0 |
| `latency_ms` | INTEGER | latencia de la llamada |

Cada llamada genera exactamente una fila, incluidas las fallidas y las bloqueadas por
presupuesto (con `status` correspondiente).

---

## (d) Etiqueta inmutable

**Regla:** APROBADA o RECHAZADA se escribe **una sola vez**, en el momento de la
decisión, en la misma transacción que la fila de `llm_logs`. No se modifica nunca
después.

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

**Operación sombra:** se abre con su etiqueta ya decidida, o con SIN_LLM si falló la
decisión. Esto no cambia el PnL de la sombra.

---

## (e) Control de coste

**Tope diario de 1 USD con corte duro.** El gasto de hoy se suma desde `llm_logs`
(por día en `REPORT_TIMEZONE`). Antes de cada llamada se calcula el coste **máximo**
posible con los tokens de entrada estimados y `max_tokens`:

```
coste_max = tokens_entrada_estimados * precio_entrada + max_tokens * precio_salida
```

Si `gasto_hoy + coste_max > LLM_DAILY_BUDGET_USD`, no se llama: se registra
`BUDGET_EXCEEDED` y la etiqueta queda SIN_LLM. Así el tope nunca se supera, ni siquiera
con respuestas largas.

**Precios:** los precios por modelo van en la configuración con la fuente y la fecha
de la sección 0. Se verifican antes de cada cambio de modelo.

**Estimación de coste diario** (1.200 tokens de entrada, 150 de salida; precios de la
sección 0):

| Modelo | Por llamada | A 9,32 grupos/día | A 25 grupos/día | Llamadas/día con 1 USD |
|---|---|---|---|---|
| Sonnet 5.5 | 0,00390 USD | 0,036 USD | 0,097 USD | 256 |
| Haiku 4.5 | 0,00195 USD | 0,018 USD | 0,049 USD | 513 |
| Opus 5.5 | 0,00780 USD | 0,073 USD | 0,195 USD | 128 |
| Fable 5.1 | 0,01950 USD | 0,182 USD | 0,487 USD | 51 |

**Número de señales por día que se usa:**

- **9,32 grupos/día (central):** es el ritmo OOS del backtest v2 de las seis estrategias
  (4.846 operaciones en 520,17 días, `docs/FASE2_REEJECUCION.md`, sección 5.3). Es una
  cota de referencia, porque las operaciones del backtest no son grupos de señal: los
  grupos reales se conocerán con la sombra en vivo.
- **25 grupos/día (cota superior):** el presupuesto de señales de `docs/FASE3_PLAN.md`.

Con Sonnet 5.5 el gasto mensual es de unos **1,09 USD** a 9,32 grupos/día, y de unos
**2,92 USD** a 25. Con el tope de 1 USD/día el corte solo se activaría por encima de
256 llamadas al día. Si las features reales son más largas que 1.200 tokens, el coste
sube de forma lineal: la primera ejecución de prueba medirá los tokens reales.

**Caché de prompt:** el mensaje de sistema es estático y se puede cachear (escritura
1,25 veces el precio de entrada, lectura 0,10 veces). No se incluye en la estimación:
es un ahorro adicional, no un requisito.

---

## (f) Placebo aleatorio: calibrar qué produce el azar

**Objetivo:** comprobar que el procedimiento de medición no inventa efectos. Si un
etiquetado aleatorio con la misma tasa de aprobación que el LLM produce "APORTA_VALOR"
con frecuencia, el criterio está mal calibrado.

**Procedimiento:**

1. Tomar las operaciones sombra cerradas con su PnL real.
2. Asignar a cada **conglomerado** (no a cada operación, para conservar la dependencia)
   una etiqueta APROBADA con probabilidad `p`, igual a la tasa de aprobación observada
   del LLM. Las operaciones de un mismo conglomerado reciben la misma etiqueta.
3. Aplicar exactamente el mismo `ai_value_verdict` (conglomerados y bootstrap).
4. Repetir 1.000 veces. La frecuencia de "APORTA_VALOR" debe estar por debajo de
   0,05 (contraste unilateral, con un nivel de significación del 5 %).

Si la frecuencia supera 0,05, el criterio no está bien calibrado y no se usa para
decidir hasta corregirlo. El placebo se registra en `docs/FASE3_6_LLM.md` con sus
resultados antes de la primera decisión real.

El placebo no sustituye a la regla de decisión: la calibra.

---

## (g) La cuenta real solo abre con APROBADA; cómo se prueba sin red ni gasto

**Regla de apertura real:** una señal abre posición en la cuenta real solo si:

1. `AUTO_OPEN_WITHOUT_LLM = true`, y
2. su etiqueta es **APROBADA**.

Con `AUTO_OPEN_WITHOUT_LLM=false` (el valor por defecto, que **no cambia** hasta que tú
lo decidas), nada abre posiciones reales. Las sombras siguen registrándose con su
etiqueta.

**Pruebas sin red ni gasto:**

- El cliente del LLM es una interfaz (`decide(request) -> RawResponse`). En los tests
  se inyecta un **cliente falso** con respuestas programadas: `APROBAR`, `RECHAZAR`,
  JSON malformado, campo extra, texto fuera del JSON, timeout, error HTTP y presupuesto
  agotado.
- Un test comprueba que no se construye ningún cliente real: la fábrica del cliente se
  parchea para lanzar un error si se invoca.
- Los tests verifican: la etiqueta correcta en cada caso, una fila de `llm_logs` por
  llamada, que el fallo deja SIN_LLM y nunca RECHAZADA, que la etiqueta no cambia tras
  escribirse (el disparador lanza error), y que con `AUTO_OPEN_WITHOUT_LLM=false` no se
  abre ninguna posición real aunque la etiqueta sea APROBADA.
- La prueba de fuga de la sección (a) y el placebo de la sección (f) se ejecutan sin
  red.

---

## (h) IDs en `.env` y script de una sola llamada

**IDs:** van en `.env`, nunca en el código. Variables propuestas:

```
ANTHROPIC_API_KEY=            # solo en .env; el asistente NO la lee
LLM_MODEL=claude-sonnet-5-5   # ID verificado; confirmar en la consola
LLM_DAILY_BUDGET_USD=1.0
LLM_TIMEOUT_SECONDS=20
LLM_PRICE_INPUT_PER_MTOK=2.0  # fuente: sección 0, consultada 2026-10-04
LLM_PRICE_OUTPUT_PER_MTOK=10.0
```

El `.env.example` mantiene la clave vacía.

**Script de prueba de una sola llamada (lo ejecutas tú):** `scripts/llm_smoke.py`.
Lee la configuración de `.env`, envía una única petición mínima (un par de frases,
`max_tokens=50`), imprime el modelo, los tokens de entrada y salida, el coste y la
latencia, y no escribe en la base de trabajo. Así verificas que la clave, el modelo y
el precio son correctos, con un gasto de céntimos.

**Dependencia nueva:** la 3.6 necesita el SDK oficial de Anthropic. Se añade a
`pyproject.toml` solo con tu aprobación.

---

## (i) Regla de decisión sobre el valor de la IA (ya fijada)

Sin intervalo de confianza del 95 % que excluya el cero, con **N efectivo ≥ 100 por
lado**, la conclusión es **"la IA no aporta valor"** y se detiene el gasto en la API.

Esta regla ya está implementada en `app/trading/ai_value.py` (`ai_value_verdict`):
- N efectivo = número de conglomerados por lado, no de operaciones.
- Bootstrap por conglomerados, 2.000 remuestreos, semilla fija.
- Veredicto `APORTA_VALOR` solo si N efectivo ≥ 100 por lado y el intervalo está por
  encima de cero. En cualquier otro caso, `LA_IA_NO_APORTA_VALOR` con el motivo.
- Sin decisiones del LLM, `SIN_DATOS`.

**Coste de tiempo estimado:** con 9,32 grupos/día y tasa de aprobación `p`, se
necesitan unas `200 / 9,32` días si `p = 0,5`. Es una **cota inferior**: el N efectivo
siempre es menor o igual que el bruto, así que hará falta más tiempo. El tiempo real se
medirá con `scripts/shadow_report.py` sobre la base en vivo.

---

## Pendientes de tu decisión

1. Aprobar el diseño completo o indicar cambios.
2. Elegir el modelo: propuesta `claude-sonnet-5-5` (verificado). `claude-sonnet-5`
   queda sin verificar.
3. Autorizar la dependencia del SDK de Anthropic.
4. Confirmar los precios de la sección 0 en la consola antes de la primera llamada.
