# Glosario

Los términos que aparecen en el dashboard, en Telegram y en la documentación, en orden alfabético. Si buscas el
porqué de la medición, lee la [metodología](METODOLOGIA.md).

**APROBADA / RECHAZADA** — Las dos etiquetas con las que Claude juzga una señal. Se guardan una sola vez y no se
pueden cambiar. Una sombra sin decisión es `SIN_LLM`, que **no** equivale a `RECHAZADA`.

**API (de Minerva)** — Las rutas HTTP que sirve el propio bot en `127.0.0.1:8000`. Casi todas exigen iniciar sesión.
`GET /health` no.

**ATR** — *Average True Range*: promedio del rango de movimiento de las últimas velas, una medida de volatilidad. Varias
estrategias lo usan para colocar el *stop loss*.

**Backtest** — Simular una estrategia sobre datos históricos para ver cómo habría funcionado. El de Minerva (Fase 2)
usa criterios fijados antes de correrlo y concluyó que ninguna estrategia por reglas tiene ventaja demostrable.

**Bitunix** — El exchange de futuros del que Minerva toma datos públicos de mercado (velas, funding, precios). No se usan
credenciales suyas.

**Brazo ejecutado** — La parte del bot que abre posiciones *simuladas* en la cuenta paper solo con las señales
`APROBADA` que llegaron a tiempo (hasta 60 s después del cierre de la vela) y pasaron el motor de riesgo. Cada decisión,
abierta u omitida, queda registrada con su motivo.

**Circuit breaker** — Freno automático: tras `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER` pérdidas seguidas se pausan las
entradas nuevas durante unas horas (8 por defecto) y se genera un *post-mortem*.

**Cierre de vela** — El instante en que termina el periodo de una vela (1 hora, 4 horas, 1 día). Las estrategias se
evalúan en cada cierre.

**CoinGecko** — Servicio gratuito del que sale el ranking de monedas por capitalización de mercado. Exige atribución:
"Data provided by CoinGecko".

**Conglomerado** — Grupo de operaciones sombra del mismo símbolo y dirección cuyas vidas se solapan; cuenta como una
sola unidad estadística porque se mueven juntas. Ver [Conglomerados y N efectivo](METODOLOGIA.md#conglomerados-y-n-efectivo).

**Contrafactual** — "Qué habría pasado si…". Las operaciones sombra dan el contrafactual de las señales rechazadas.

**Cuenta paper** — La cuenta simulada (capital inicial de 100 USDT por defecto) donde se abren las posiciones del
brazo ejecutado. Dinero ficticio.

**δ (delta)** — El margen de "no valor": 3 % del margen por operación. Un filtro que mejore el resultado en menos que eso
no compensa depender de una API externa. Ver [El veredicto y el margen de no valor](METODOLOGIA.md#el-veredicto-y-el-margen-de-no-valor).

**Donchian (canal)** — Máximo y mínimo de las últimas N velas. Una ruptura de ese canal es una señal de *breakout*.

**Drawdown** — Caída del capital desde su máximo. El *stop por drawdown* detiene las entradas si cae más del 20 % (por
defecto).

**EMA** — Media móvil exponencial: promedio de precios que da más peso a los más recientes.

**Equity** — Capital realizado más el PnL flotante de las posiciones abiertas.

**Etiqueta** — El resultado del juicio del LLM sobre una señal (`APROBADA`, `RECHAZADA` o `SIN_LLM`).

**Factor de beneficio (profit factor, PF)** — Ganancias brutas totales divididas entre pérdidas brutas totales. Mayor que
1 gana más de lo que pierde; el backtest exigió al menos 1,2 fuera de muestra.

**Features** — Los datos calculados que se le entregan a Claude con cada señal (indicadores, niveles, funding,
volatilidad…). Su versión actual es `features_v2`.

**Fuera de muestra (OOS, *out of sample*)** — Datos que no se usaron para diseñar una estrategia, reservados para
evaluarla (en el backtest, el 30 % más reciente).

**Funding (tasa de financiación)** — Pago periódico entre posiciones largas y cortas de un contrato perpetuo, que mantiene
el precio del perpetuo cerca del precio real. Según su signo, una posición paga o cobra funding.

**Gate (compuerta)** — La condición "ya hay 300 conglomerados efectivos de cada lado". Abrirla **no** muestra
resultados: hace falta además el registro oficial del veredicto.

**Hash congelado** — La huella SHA-256 del texto del prompt y de la versión de las *features*. El código compara su hash
actual con el congelado en cada ciclo; si difieren, el etiquetado se detiene. Se ve en `/health` como
`llm_labeling.frozen_hash_ok`.

**HOLD** — "No hacer nada": una evaluación de una estrategia que no produjo señal. También se registran.

**Intervalo de confianza (IC)** — El rango en el que, con cierta probabilidad (95 %), está la diferencia real entre
aprobadas y rechazadas, dada la muestra.

**Kill switch** — Interruptor de emergencia que bloquea las entradas nuevas. En Minerva (`/kill`) además **pausa el
scheduler**, lo que detiene la medición. Se levanta con `/resume`.

**Liquidación** — Cierre forzado de una posición apalancada cuando la pérdida consume el margen. Minerva la simula en
margen aislado.

**LONG / SHORT** — Posición comprada (gana si el precio sube) o vendida en corto (gana si baja).

**Margen aislado** — El riesgo de una posición se limita al margen que se le asignó. Es el único modo que simula Minerva.

**Marca de la mudanza** — El archivo `data/MIGRADO_A_DOCKER.txt`: indica que el bot corre en Docker y que la base nativa
de `data/` es una copia vieja. Ver [Pasar un bot nativo a Docker](MUDANZA_DOCKER.md).

**Medición ciega** — Diseño por el cual nadie ve resultados (PnL, tasas de acierto, comparación entre aprobadas y
rechazadas) hasta el punto de análisis. Ver [Por qué se ocultan los resultados](METODOLOGIA.md#por-qué-se-ocultan-los-resultados).

**Monitor de posiciones** — Tarea que vigila con ticks en vivo el SL, el TP, el trailing y la liquidación de las
posiciones y de las sombras, y reconcilia lo ocurrido si el bot estuvo apagado.

**N efectivo** — Número de conglomerados (no de operaciones) de un lado. El punto de análisis es 300 por lado.

**Operación sombra (*shadow trade*)** — Operación simulada que se abre por **cada** señal, sin límite de margen ni de
cupo, para poder comparar aprobadas con rechazadas. No es dinero ni posiciones de la cuenta paper.

**Paper trading** — Operar con dinero simulado y datos reales de mercado. Minerva nunca envía órdenes reales.

**Perpetuo** — Contrato de futuros sin fecha de vencimiento, típico de las criptomonedas. Minerva usa los perpetuos
USDT de Bitunix.

**Placebo** — Prueba de calibración del veredicto: se reetiquetan al azar los conglomerados y se comprueba que el
procedimiento no inventa un efecto. Ver [El placebo](METODOLOGIA.md#el-placebo).

**PnL** — *Profit and loss*: ganancia o pérdida. El PnL neto descuenta comisiones, funding y deslizamiento.

**Post-mortem** — Resumen determinista que se genera tras una racha de pérdidas, con lo que tuvieron en común.

**Prompt (congelado)** — El texto de instrucciones que se le da a Claude para juzgar cada señal. El de la medición está
congelado: ver [El prompt congelado](METODOLOGIA.md#el-prompt-congelado-y-por-qué).

**Puerta de resultados (*reveal*)** — El mecanismo que oculta PnL, saldo y motivos de cierre en todos los canales
mientras no exista el registro oficial del veredicto. Falla cerrada.

**Reconciliación** — Al arrancar, reproducir con velas de 1 minuto lo que habría pasado con las posiciones mientras el
bot estaba apagado (si tocaron un SL, un TP o la liquidación).

**Registro oficial (del veredicto)** — El archivo `docs/VEREDICTO_3_6_REGISTRO_<fecha>.txt` que guarda el único
veredicto. Su existencia desbloquea los resultados. Solo puede haber uno.

**Resumen diario** — Mensaje de Telegram de una vez al día con el estado y el avance de la medición. Nunca lleva PnL.

**SIN_LLM** — Ver *APROBADA / RECHAZADA*.

**SL (*stop loss*)** — Precio al que se cierra una posición para limitar la pérdida.

**Slippage (deslizamiento)** — Diferencia entre el precio esperado y el precio real de ejecución. Minerva lo simula con
`BACKTEST_SLIPPAGE_BPS` (5 puntos básicos por defecto).

**TP (*take profit*)** — Precio al que se cierra una posición para asegurar la ganancia.

**Trailing stop** — Un SL que se desplaza siguiendo al precio cuando este avanza a favor de la posición.

**Universo** — El conjunto de monedas que se evalúan: las de mayor capitalización (10 por defecto) que tienen perpetuo
USDT en Bitunix, sin stablecoins ni tokens envueltos. Se refresca cada 24 horas.

**USDT** — Moneda estable ligada al dólar en la que se cotizan y liquidan los perpetuos.

**Veredicto** — El resultado final de la medición: `APORTA_VALOR`, `NO_APORTA_VALOR` o `INCONCLUSO`. Se calcula una
sola vez.

**Ventana segura** — Los minutos :10 a :40 de una hora UTC que no sea de cierre de vela de 4 h; el momento en que se
puede reiniciar el bot sin cortar etiquetados en vuelo. Ver [Reinicios seguros](USO.md#reinicios-seguros).

**Volumen (Docker)** — Almacenamiento interno de Docker donde viven la base de datos y los logs del contenedor
(`minerva_data`, `minerva_logs`). Borrarlo borra la medición.

**WAL** — *Write-Ahead Logging*: el modo de SQLite que usa Minerva. Por eso la base no se copia a mano, sino con las
herramientas de respaldo.

**Walk-forward** — Evaluar una estrategia en tramos sucesivos del tiempo (en el backtest, de 6 meses con paso de 2) para
ver si su resultado es consistente.
