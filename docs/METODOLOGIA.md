# Metodología

Cómo intenta Minerva responder **una** pregunta con honestidad, explicado sin suponer que sepas estadística. Si
algún término te suena a jerga, mira el [glosario](GLOSARIO.md). El diseño técnico completo (con las cifras de
potencia, la regla de decisión y los pilotos del prompt) está en [`FASE3_6_LLM.md`](FASE3_6_LLM.md) y el
procedimiento operativo del veredicto en [`VEREDICTO_3_6.md`](VEREDICTO_3_6.md).

> **Minerva es paper trading, experimental y no es asesoría financiera.** Esta metodología describe cómo se *mide*
> una idea, no la promete: hasta ahora **ninguna estrategia por reglas mostró ventaja** y el valor del filtro de IA
> **todavía no está medido**.

## La pregunta

El proyecto no pregunta *"¿este bot gana dinero?"* sino algo más acotado:

> **Si un modelo de lenguaje (Claude) revisa cada señal técnica de trading y decide aprobarla o rechazarla,
> ¿mejora el resultado medio por operación frente a operar todas las señales sin filtrar?**

Se parte de señales de seis estrategias sencillas por reglas (cruces de medias, rupturas, reversión a la media,
funding). El backtest ya mostró que, solas, no superan los criterios que se fijaron de antemano. Si esas señales son
ruidosas, quizá un filtro inteligente separe las mejores de las peores. Eso es lo que se mide. Las respuestas posibles
son tres: *sí aporta valor*, *no aporta valor* o *no se puede saber todavía con esta muestra*.

## El diseño en una página

1. En cada cierre de vela, las estrategias generan señales.
2. **Cada** señal se simula como una **operación sombra**: una operación ficticia que se abre y se cierra sola, con los
   mismos costos y reglas que la cuenta paper, pero sin límite de margen.
3. Claude revisa la señal y la etiqueta `APROBADA` o `RECHAZADA`. La etiqueta se guarda una sola vez y no se puede
   cambiar.
4. Se espera a que las sombras se cierren (por SL, TP, trailing o liquidación).
5. Al final se compara el resultado medio, como fracción del margen, de las sombras `APROBADA` contra las
   `RECHAZADA`.

Como las dos mitades salen del **mismo conjunto de señales en el mismo mercado**, las condiciones son iguales para
ambas: la diferencia se puede atribuir al juicio del filtro y no a que unas operaciones cayeron en un mercado mejor.

## Por qué sombras

- **Para tener un contrafactual.** Si solo se operaran las aprobadas, no sabríamos qué habría pasado con las
  rechazadas, y sin eso no hay comparación posible. Con sombras, ambas existen.
- **Sin límites de cupo ni de margen.** La cuenta paper tiene reglas de riesgo (máximo de posiciones, pérdida diaria,
  etc.). Si la comparación dependiera de ellas, una aprobada podría "perderse" por falta de cupo y no por su calidad.
- **Sin bloqueo por solapes.** Una sombra se abre aunque otra del mismo símbolo y dirección siga abierta. Si no, una
  operación *rechazada* impediría abrir la siguiente y las dos mitades dejarían de compararse sobre las mismas
  oportunidades. El precio de esa decisión es que las operaciones dejan de ser independientes: eso lo resuelven los
  conglomerados (más abajo).
- **La cuenta paper es otra cosa.** El **brazo ejecutado** abre posiciones simuladas solo con las aprobadas que
  llegaron a tiempo y pasaron el motor de riesgo; sirve para ver cómo se comportaría una operativa sujeta a esos límites. El
  veredicto del valor de la IA se calcula con las **sombras**, no con esa cuenta.

## Qué es una etiqueta: `APROBADA`, `RECHAZADA` y `SIN_LLM`

| Etiqueta | Significa |
|---|---|
| `APROBADA` | Claude respondió `APROBAR` |
| `RECHAZADA` | Claude respondió `RECHAZAR` |
| `SIN_LLM` | No hubo decisión: la sombra nació así y sigue así si hubo un fallo de la API, un tiempo agotado (20 s por defecto), una respuesta inválida o el presupuesto diario agotado |

Tres reglas importan:

- **`SIN_LLM` nunca es `RECHAZADA`.** Un fallo técnico no es un juicio negativo; tratarlo así contaminaría la
  comparación. Las sombras sin decisión no entran en ninguno de los dos lados.
- **La etiqueta es inmutable.** Una sombra pasa de `SIN_LLM` a `APROBADA` o a `RECHAZADA` una única vez; un disparador
  de la base de datos impide cualquier otro cambio.
- **Demasiados `SIN_LLM` invalidan el veredicto.** Si más del 10 % de los grupos de la medición quedó sin etiqueta, el
  veredicto es `INCONCLUSO` automáticamente: podría haber un sesgo de exclusión (por ejemplo, que fallen justo las
  señales más raras). Por eso un presupuesto de gasto demasiado corto daña la medición.

Además se vigila que el filtro **filtre de verdad**: la tasa de aprobación debe estar entre el 15 % y el 85 %. Un filtro
que aprueba casi todo (o casi nada) no filtra. La primera versión del prompt aprobó el 90 % y se descartó por eso.

## El prompt congelado, y por qué

Claude recibe, por cada señal, un mensaje con datos conocidos **en el instante del cierre de la vela**: la identidad de la
señal, los indicadores de las estrategias que la generaron, los niveles planeados (entrada, SL, TP, liquidación y el
riesgo como porcentaje del margen), el funding, la volatilidad reciente, el contexto de tendencia, la correlación con
BTC y las posiciones realmente abiertas en ese momento. **No recibe** velas posteriores, resultados, su propio
historial de aciertos, la etiqueta de otras señales ni noticias. Hay pruebas de *fuga* que lo verifican (si se
inyectaran velas futuras con valores extremos, lo que ve Claude no cambia).

El texto del prompt y la definición de esas *features* se **congelaron** el 2026-10-12, después de cuatro pilotos con
llamadas reales en los que se leyeron las respuestas una por una y se corrigieron defectos concretos (unidades
equivocadas, el signo del funding en posiciones cortas…). Un defecto que resistió la explicación en texto se resolvió
**calculándolo** en código y entregando a Claude la respuesta ya hecha.

¿Por qué congelarlo? Porque **cualquier cambio en el prompt, las *features* o el modelo es otro experimento**. Si se
mezclan versiones, la comparación entre aprobadas y rechazadas deja de significar algo. Así se hace cumplir:

- El **hash** SHA-256 del texto del prompt y de la versión de las *features* queda fijado en el código. Se comprueba en
  **cada ciclo**; si no coincide, el etiquetado se detiene solo (`/health` lo muestra como
  `llm_labeling.frozen_hash_ok: false`) en lugar de contaminar la medición.
- Todo el análisis filtra por ese hash: las filas de otras versiones o de los pilotos no cuentan.
- **El modelo no está cubierto por el hash.** Por eso hay un script (`scripts/measurement_identity.py`) que comprueba que
  todas las filas medidas usan el mismo modelo, prompt y versión de *features*; no hay todavía una guarda automática
  equivalente a la del hash.

## Conglomerados y N efectivo

Cuando varias sombras del **mismo símbolo y la misma dirección** están abiertas a la vez, se mueven con el mismo
movimiento de precio: son, en la práctica, la misma apuesta contada varias veces. Si se contaran como operaciones
independientes, la evidencia parecería mayor de lo que es.

Por eso se agrupan: dos operaciones del mismo símbolo y dirección cuyos intervalos de vida (de apertura a cierre) se
solapan cuentan como **un solo conglomerado**. El **N efectivo** es el número de conglomerados, no el de operaciones, y
el cálculo estadístico remuestrea conglomerados enteros.

## El punto de análisis: 300 por lado

El veredicto se calcula **una sola vez**, cuando se reúnen **300 conglomerados efectivos de cada lado** (300 con
operaciones `APROBADA` y 300 con operaciones `RECHAZADA`). A esa condición se le llama *gate* (compuerta).

- **Por qué un punto fijo.** Si se mirara el resultado cada semana y se parara cuando "se ve bien", se encontrarían falsos
  positivos con mucha facilidad. Fijar de antemano cuándo se mira, y mirar una vez, evita engañarse.
- **Por qué 300.** La variabilidad de una operación individual es grande (una desviación típica del orden del 45 % del
  margen). Con 300 conglomerados por lado, el efecto más pequeño que el diseño detecta con buena fiabilidad (80 % de
  potencia) ronda el **10 % del margen por operación**.
- **Cuánto tarda.** Meses. En la medición del autor se juntaban del orden de 1,25 conglomerados por día de calendario y
  lado (≈ 5 por día con el bot encendido, que solo estuvo encendido el 23,6 % del tiempo). Un equipo apagado no
  recupera medición.

## La regla de «una sola mirada»

- Antes de llegar a 300 por lado, **las herramientas solo imprimen contadores** (cuántos conglomerados hay, la tasa de
  aprobación, cuántos `SIN_LLM`…): ningún PnL, ninguna diferencia, ningún veredicto.
- Cuando se llega, el informe se corre **una vez**, sobre **una copia** de la base, y se guarda como **registro
  oficial** (`docs/VEREDICTO_3_6_REGISTRO_<fecha>.txt`). Volver a correrlo con más datos **no es un veredicto
  nuevo**; la herramienta se niega a crear un segundo registro.
- La decisión de cuándo repetir el procedimiento depende **solo de conteos**, nunca de resultados.

## El placebo

Antes del veredicto se hace una prueba de **calibración**: ¿puede el procedimiento inventar un efecto donde no lo hay?

1. Se toman las sombras cerradas y, **al azar**, se reetiqueta cada conglomerado como aprobado con la misma probabilidad
   que tuvo Claude (las operaciones de un mismo conglomerado reciben la misma etiqueta).
2. Se aplica **exactamente la misma regla de decisión** y se anota el veredicto.
3. Se repite 1.000 veces.

Si con etiquetas puramente aleatorias el procedimiento da `APORTA_VALOR` con frecuencia, el criterio está mal
calibrado y no sirve para decidir. Se considera **calibrado** si la tasa de falsos positivos por azar queda por debajo
del 5 %. Una repetición solo puede dar `APORTA_VALOR` si el azar deja al menos 300 conglomerados en cada lado; si
menos del 50 % de las repeticiones llega a ese punto, el placebo es **no informativo**: no se corre el veredicto y se
**esperan 7 días más de datos** antes de repetir. El placebo no revela nada sobre la IA (las etiquetas reales se
sustituyen por azar), así que puede correrse antes de que se abra el *gate*.

El orden oficial es: **copia → placebo → (solo si sale calibrado) informe una única vez**.

## El veredicto y el margen de no valor

Para cada sombra cerrada se calcula su **retorno** = PnL neto / margen (una fracción del margen, no dólares: así vale
igual con 5 o 10 USDT de margen). La cifra clave es **Δ** = retorno medio de las `APROBADA` − retorno medio de las
`RECHAZADA`, con su **intervalo de confianza del 95 %** `[lo, hi]` calculado por remuestreo de conglomerados.

| Veredicto | Condición | Significado |
|---|---|---|
| `APORTA_VALOR` | `lo > 0` | La decisión de Claude mejora el resultado por operación. Con la marca `MAGNITUD_BAJA` si, además, `hi < δ`: el efecto es real pero pequeño |
| `NO_APORTA_VALOR` | `hi < δ` | Como mucho, el efecto queda por debajo de δ: no compensa el costo ni la dependencia de la API |
| `INCONCLUSO` | El resto, o `SIN_LLM` > 10 % | La muestra no separa un efecto de la ausencia de efecto, o puede haber sesgo de exclusión |

**δ = 3 % del margen por operación** es el "margen de no valor": la mejora mínima que justificaría depender de una API
externa de pago (latencia, cortes, cambios de modelo). Equivale a unas 0,07 desviaciones típicas por operación. El costo
de cada llamada es mucho menor que δ, así que δ mide el valor de la decisión, no su precio.

**Qué esperar con 300 por lado:** el intervalo de confianza mide aproximadamente ±7 puntos de margen. Por eso, si el
efecto real es pequeño o nulo, lo esperable es `INCONCLUSO`; y `NO_APORTA_VALOR` exigiría que la IA *reste* valor con
claridad. Detectar un efecto del tamaño de δ con buena fiabilidad requeriría del orden de 3.500 conglomerados por lado.
El diseño lo asume y lo dice de antemano: un `INCONCLUSO` no es un fracaso del procedimiento.

## Por qué se ocultan los resultados

Cualquiera que vea el PnL de las operaciones aprobadas y rechazadas *antes* del punto de análisis puede, sin querer, tomar
decisiones que sesgan el veredicto: parar cuando "se ve bien", retocar el prompt "solo un poco", cambiar de modelo o de
umbral. Para que no dependa de la fuerza de voluntad, **el código lo impide**:

- Ningún canal (API, dashboard, Telegram, avisos, resumen diario, informes) muestra PnL, saldo, equity, precio de
  salida ni motivo de cierre hasta que se cumplan **dos** condiciones: el *gate* está abierto **y** existe el registro
  oficial del veredicto.
- La puerta **falla cerrada**: ante cualquier error al evaluarla, los resultados siguen ocultos. Se hace cumplir con
  listas blancas de campos y se comprueba con un barrido de pruebas sobre todos los canales.
- Lo que sí se ve: la estructura de las operaciones (símbolo, lado, niveles, horas), conteos, el avance hacia los 300,
  la salud del bot y el costo.

Fugas conocidas, declaradas y aceptadas: los logs de nivel INFO dejan pasar el motivo de cierre de las sombras y el PnL
neto de las posiciones; las horas de cierre y los niveles permiten inferir algo con muchas operaciones; y un *circuit
breaker* activo implica una racha perdedora. Si quieres mantener la medición ciega, evita abrir los logs. Más en
[Costos y límites](COSTOS_Y_LIMITES.md#límites-conocidos).

## Qué conclusiones permite (y cuáles no)

**Permite**, cuando llegue el veredicto: decir si, *para este conjunto de monedas, estas seis estrategias, este periodo,
este modelo y este prompt*, el filtro de IA mejora o no el retorno medio por operación en la simulación, con un
margen de error cuantificado.

**No permite:**

- **Afirmar que el bot es rentable.** Un `APORTA_VALOR` dice que aprobadas rinden más que rechazadas, no que la cuenta
  gane dinero: ninguna estrategia por reglas mostró ventaja en el backtest, y mejorar una señal mala no la vuelve buena.
- **Generalizar.** Otro modelo, otro prompt, otro mercado u otro periodo (un mercado alcista frente a uno bajista) son
  otro experimento. La medición cubre un solo tramo de tiempo.
- **Garantizar el comportamiento en dinero real.** La simulación supone costos (comisión *taker* de VIP 0, deslizamiento de
  5 puntos básicos) y no modela por completo la latencia, los rechazos del exchange, la profundidad del libro ni los
  mínimos de orden. Las sombras entran al precio de cierre de la vela sin demora, mientras que una cuenta real entraría
  algunas decenas de segundos después. Los puntos que no se pudieron verificar están en
  [Costos y límites](COSTOS_Y_LIMITES.md#lo-no-verificado) y en [Paso a dinero real](PASO_DINERO_REAL.md).
- **Concluir sobre estrategias o monedas concretas.** El desglose por estrategia y símbolo es exploratorio, no corrige
  comparaciones múltiples y solo se muestra si el veredicto es `APORTA_VALOR`.
- **Interpretar `INCONCLUSO` como "no funciona".** Significa "esta muestra no alcanza para saberlo".
- **Eliminar el sesgo de supervivencia.** El universo de hoy (las 10 mayores) puede no reflejar qué monedas eran
  líquidas en 2022, por eso el backtest incluye un grupo de control con BTC y ETH.

## Para auditarlo o reproducirlo

Todo queda registrado: cada llamada al LLM (mensaje enviado, respuesta cruda, tokens, costo y latencia) en `llm_logs`,
cada sombra en `shadow_trades`, cada decisión del brazo ejecutado en `paper_execution_log`.

| Herramienta | Para qué | Muestra resultados |
|---|---|---|
| `scripts/measurement_status.py` | Avance de la medición: conteos, ritmo, costo | No (solo conteos) |
| `scripts/measurement_identity.py` | ¿Un solo modelo, prompt y versión de *features*? | No |
| `scripts/placebo_check.py` | Calibración del criterio | No revela nada de la IA |
| `scripts/ai_value_report.py` | El veredicto, una sola vez | Se niega con el *gate* cerrado |

Los comandos exactos, con Docker y sin él, están en el [procedimiento del veredicto](VEREDICTO_3_6.md) y en
[Uso diario](USO.md#cuando-se-abre-el-gate).
