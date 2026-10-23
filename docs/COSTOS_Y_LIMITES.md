# Costos y límites

Cuánto cuesta de verdad correr Minerva, qué fuentes gratuitas usa (con sus términos y atribuciones), cuáles son sus
límites conocidos y, aparte, **lo que no se pudo verificar**. Las cifras llevan su fecha y su fuente; donde no hay
medición, dice "no verificado".

> **Minerva es paper trading, experimental y no es asesoría financiera.** Esta página no promete rendimiento: documenta
> costos y límites del sistema.

## Resumen

| Concepto | Costo |
|---|---|
| API de Anthropic (Claude) | **De pago por uso**: del orden de 0,005 USD por señal etiquetada; tope diario con corte duro (1 USD por defecto) |
| Datos de mercado (Bitunix), universo (CoinGecko), noticias (GDELT, Fed, Alternative.me), Telegram | Gratuitos y sin tarjeta, con sus límites y atribuciones (más abajo) |
| Hosting | Tu propio equipo (Docker); sin servidores de pago |
| Software | MIT; todas las librerías son de código abierto |

La regla del proyecto: **lo único que puede costar dinero es la API de Anthropic**, con presupuesto diario y corte duro
configurables; todo lo demás debe ser gratuito y sin tarjeta.

## Costo de la API de Anthropic

### Precios y modelo

El modelo que etiqueta la medición es `claude-sonnet-5-5`. Según la página oficial de precios de Anthropic (consultada el
**2026-10-23**), Claude Sonnet 5.5 cuesta **2 USD por millón de tokens de entrada y 10 USD por millón de tokens de
salida**; son los valores por defecto de `LLM_PRICE_INPUT_PER_MTOK` y `LLM_PRICE_OUTPUT_PER_MTOK`. Minerva no usa
descuentos por lotes ni *prompt caching*. Los precios cambian: verifícalos en <https://platform.claude.com/docs/en/about-claude/pricing>
antes de confiar en estas cifras, y actualiza esas dos variables si difieren (solo afectan al costo calculado y, por
tanto, al corte del tope diario).

### Lo medido

Cada señal agrupada genera **una** llamada. Mediciones reales con el prompt del proyecto:

| Medición | Llamadas | Entrada media | Costo total | Costo por llamada |
|---|---|---|---|---|
| Primera llamada real de prueba (2026-10-10) | 1 | 1.393 tokens (salida: 145) | 0,004236 USD | 0,0042 USD |
| Piloto del prompt v1 (2026-10-11) | 21 | 1.389 tokens | 0,0884 USD | 0,0042 USD |
| Piloto del prompt v3 (2026-10-12) | 32 | 1.986 tokens | 0,173 USD | 0,0054 USD |
| Medición en vivo con el prompt congelado (2026-10-17) | 14 filas | — | 0,0803 USD | 0,0057 USD |

**Orden de magnitud: 0,004 a 0,006 USD por señal etiquetada**, es decir, alrededor del 0,11 % del margen de 5 USDT de una
operación. Latencia típica de la llamada: 1,5 a 9 segundos (media del piloto v1: 1,8 s).

### Proyección

El número de señales por día depende del mercado y del universo. En la medición del autor (octubre de 2026, 10 monedas) se
estimaron del orden de **22 grupos de señal por día con el bot encendido**; a ≈ 0,0057 USD cada uno son ≈ 0,13 USD por día
(≈ 3,8 USD al mes si estuviera encendido las 24 horas). El diseño original estimó entre 9 y 25 grupos por día, es decir,
entre ≈ 1,2 y ≈ 4 USD al mes. **Son estimaciones, no garantías**: comprueba el costo real en el dashboard (tarjeta *Costo
del LLM*) o en el resumen diario.

### Los topes

1. **`LLM_DAILY_BUDGET_USD`** (1 USD por defecto, el día en `REPORT_TIMEZONE`): antes de cada llamada se reserva el costo
   **máximo** posible (≈ 0,0054 USD con las entradas estimadas y `LLM_MAX_TOKENS=300`); si la reserva, más el gasto del día y
   las llamadas en vuelo, superaría el tope, no se llama y la señal queda `SIN_LLM` (`BUDGET_EXCEEDED`). Con 1 USD caben
   ≈ 185 llamadas al día. Es un **corte duro**.
2. **El tope de gasto en la consola de Anthropic** (*Billing → Spend limits*): ponlo tú. Es la red de seguridad si algo
   fallara en el bot. Al alcanzarlo, la API devuelve HTTP 400 y las señales quedan `SIN_LLM`.
3. Un tope demasiado bajo **daña la medición**: si más del 10 % de los grupos queda `SIN_LLM`, el veredicto es automáticamente
   `INCONCLUSO`. No es "gastar más porque sí": es no perder señales.

El tope diario protege **dentro de una misma base**. Si corres `scripts/llm_smoke.py` o `scripts/llm_pilot.py` repetidamente
sobre copias distintas, cada copia empieza con gasto cero; el límite real entre corridas es su `--max-calls` y el tope de la
consola.

### Límites de uso de la API de Anthropic

Anthropic limita las peticiones por minuto según el nivel de la organización (la documentación indica 1.000 por minuto para
Sonnet 5.5 en el nivel inicial). Minerva usa como máximo 4 llamadas simultáneas (`LLM_MAX_CONCURRENCY`) y unas decenas al día:
muy por debajo. Una organización nueva puede empezar con límites más bajos.

## Fuentes gratuitas, términos y atribuciones

Todas se tratan como **poco fiables**: caché local, reintentos con espera, salud por fuente y bloqueo de nuevas entradas si un
dato crítico está obsoleto. "Verificado" significa leído en la documentación oficial o comprobado con una respuesta real en la
fecha indicada.

| Fuente | Qué se usa | Límites y plan | Términos y atribución | Verificado |
|---|---|---|---|---|
| **Bitunix** (API pública de futuros) | Velas, funding, precios (REST y WebSocket), especificaciones de contrato, niveles de margen | Sin clave. Documentada con un límite de 10 peticiones por segundo por IP; Minerva lo respeta (`BITUNIX_RATE_LIMIT_PER_SEC`) | Los términos de uso de los datos públicos **no se revisaron** | Fase 0 (2026-09-30) |
| **CoinGecko** (plan Demo) | Ranking por capitalización y categorías (stablecoins, tokens envueltos…) | Gratuito, sin tarjeta: 100 llamadas por minuto y 10.000 al mes. Minerva hace ≈ 4 llamadas por refresco diario (≈ 120 al mes) | **Atribución obligatoria**: "Data provided by CoinGecko", visible junto a los datos en el dashboard. La página de precios no indica un tipo de licencia para el plan Demo; el uso de Minerva es de investigación no comercial | 2026-10-23 |
| **GDELT DOC 2.0** | Titulares de noticias (título, URL, dominio, fecha) | Sin clave. Pide no más de una petición cada 5 segundos (responde 429); Minerva respeta el intervalo, usa *backoff* y enfría ante un 429 | Uso sin restricciones y sin costo, **citando al GDELT Project** con enlace; el dashboard y `/health` lo muestran | 2026-10-17 |
| **Reserva Federal** (RSS de política monetaria) | Comunicados | Se consulta como máximo cada 60 minutos con petición condicional | Dominio público; se cita a la Junta de Gobernadores. No se encontró una política de acceso automatizado | 2026-10-17 |
| **Alternative.me** (Fear & Greed) | Índice de miedo y codicia | Sin clave; el límite no figura; una consulta cada 6 horas | Exige reconocer la fuente "junto a los datos mostrados"; el dashboard lo hace | 2026-10-17 |
| **Telegram Bot API** | Avisos y comandos | Gratuito. Minerva deja al menos 1,2 s entre mensajes (la API pide no pasar de ≈ 1 por segundo y chat) y respeta `retry_after` | Términos de Telegram | — |

### Fuentes que se descartaron

| Fuente | Motivo |
|---|---|
| CoinDesk (RSS) | Su `robots.txt` bloquea a los rastreadores de IA y sus términos no se pudieron verificar; como el uso final es alimentar a un modelo de lenguaje, se descartó por precaución |
| Cointelegraph (RSS) | Sus términos prohíben el *scraping* automatizado y el uso de su contenido para sistemas de IA sin consentimiento escrito |
| Decrypt (RSS) | Sus términos prohíben robots y *scrapers* y exigen uso no comercial; ambiguo para un RSS, no se asume permiso |
| CryptoPanic | Exige clave (cuenta) y no se encontraron sus términos oficiales sobre plan gratuito, atribución y uso con IA |
| SEC (comunicados) | Exige identificar el bot con un contacto del operador; queda como opción futura |
| Anuncios de Bitunix | No se encontró un *feed* oficial verificable |

Las noticias son **solo informativas**: ninguna operación depende de ellas y no entran al prompt de la medición. La licencia
MIT de este repositorio cubre su código, no los datos de estas fuentes, que siguen sus propios términos: no los redistribuyas.

## Límites conocidos

### De la medición

- **Potencia estadística limitada.** Con 300 conglomerados por lado, el diseño solo detecta con fiabilidad efectos del orden
  del 10 % del margen por operación; si el efecto real es pequeño, lo esperable es `INCONCLUSO`. Un efecto del tamaño de δ
  (3 %) requeriría del orden de 3.500 conglomerados por lado.
- **Plazo largo y dependiente de la disponibilidad.** Meses de calendario; el tiempo con el equipo apagado no se recupera.
- **Un solo tramo de tiempo, un modelo y un prompt.** No generaliza a otros mercados, modelos o periodos
  ([Metodología](METODOLOGIA.md#qué-conclusiones-permite-y-cuáles-no)).
- **Sesgo de supervivencia del universo.** El universo es el de hoy (las 10 mayores); el backtest histórico lo mitiga solo con
  un grupo de control de BTC y ETH.
- **El modelo no está vigilado automáticamente** (solo el prompt, con el hash); `scripts/measurement_identity.py` lo comprueba
  a mano.
- **Fugas residuales de resultados** (declaradas y aceptadas): los logs de nivel INFO dejan pasar el motivo de cierre de las
  sombras y el PnL neto de las posiciones paper; `closed_at` y los niveles permiten inferir algo con muchas operaciones; y un
  *circuit breaker* activo implica una racha perdedora. Si quieres mantener la medición ciega, evita abrir los logs. Dejan de
  importar cuando se desbloquean los resultados.
- **Muestra pequeña del brazo ejecutado.** Del orden de 2 a 3 aperturas por día con el bot encendido; 100 operaciones
  cerradas son semanas o meses.

### De la simulación frente a la realidad

- **Brecha paper-real.** La simulación supone la comisión *taker* de VIP 0 (0,06 %) y un deslizamiento de 5 puntos básicos. No
  modela por completo la latencia, los rechazos, la profundidad del libro ni los mínimos de orden.
- **Las sombras entran al precio de cierre de la vela, sin demora**; una cuenta real entraría decenas de segundos después.
- **Tamaños mínimos.** Con 5 USDT de margen y apalancamiento 10x, algunos contratos (por ejemplo `TRXUSDT`, que exige un
  mínimo de 150 TRX) no permiten abrir la posición en la cuenta paper, aunque la sombra sí se abre: el veredicto incluye
  operaciones que la cuenta no podría abrir.
- **Reconciliación.** Tras un apagado, se reproduce lo ocurrido con velas de 1 minuto; si faltan más de dos velas, la posición
  queda abierta con un aviso crítico. Bitunix a veces omite una vela en ciertas consultas; el bot detecta y repara esos
  huecos, pero un hueco irreparable limita la reconstrucción.
- **Historia de funding más corta que la de precios.** Antes de ~2024 el funding de las pruebas históricas es aproximado.

### Del sistema

- **Un solo proceso y un solo equipo:** un único punto de falla; sin alta disponibilidad.
- **SQLite en modo WAL, una conexión.** Simple y suficiente; no está pensado para varios procesos.
- **Windows:** `stop_bot.ps1` es un corte duro; un etiquetado en vuelo se pierde (de ahí la ventana segura de reinicio). La
  parada de Docker es ordenada.
- **Docker:** un contenedor `unhealthy` no se reinicia solo; Docker Desktop no inicia con Windows por defecto; borrar el
  volumen borra la medición.
- **Sin HTTPS, sin roles, sin límite de intentos de login.** Solo uso local ([Seguridad](SEGURIDAD.md)).
- **Sin tiempo real en el navegador** (consulta periódica), sin cierre manual de posiciones ni edición de parámetros de riesgo
  desde la interfaz, sin curvas de equity ni PnL agrupado (serían resultados).
- **Las fuentes gratuitas fallan:** GDELT respondió 429 casi siempre desde el equipo de desarrollo, por lo que la cobertura de
  titulares depende de él. Ante una fuente caída el contexto de noticias queda vacío con su motivo; nunca se inventa.
- **El `.env` está en claro en el disco**; los respaldos no están cifrados.

## Lo no verificado

Declarado, no oculto. Es lo que **no se pudo comprobar** y que cualquiera que use o amplíe el proyecto debe tener presente.

### Ejecución real en Bitunix

No existe un `BitunixBackend` funcional, así que ninguno de estos puntos se probó. Cada uno tendría que cerrarse con la
documentación oficial o con una prueba controlada antes de pensar en dinero real (detalle en
[Paso a dinero real](PASO_DINERO_REAL.md)):

- **Latencia** de las órdenes y **límites de tasa** de los endpoints de trading (la introducción de la API no los documenta).
- **Modo de posición** de una cuenta (el parámetro `tradeSide` solo se exige en modo *hedge*).
- **Mecánica de cobro del funding** (la documentación define los campos, no cómo se cobra).
- **Base de cobro y tipo de orden** de la comisión de una orden a mercado (la página de tarifas publica la tabla VIP 0 pero no
  dice si una orden a mercado siempre es *taker*).
- **Mínimo de orden en USDT:** la documentación de pares da `minTradeVolume` y `basePrecision` pero no un mínimo en USDT.
- **Semántica de `priceProtectScope`** (documentado como campo, sin explicar su significado) y ausencia de protección de
  precio o de slippage en la documentación de *Place order* (la ausencia se verificó).
- **Ejecución del SL y TP en el exchange** ante saltos de precio y fills parciales (se documentan los parámetros, no el
  comportamiento). En paper nadie protege una posición con el equipo apagado; en dinero real el SL/TP debería vivir en el
  exchange.
- **Los términos de uso de los datos públicos de Bitunix** (cómo se pueden usar automatizadamente, atribución).

### Servicios externos

- **El formato real de los artículos de GDELT** y su límite efectivo desde el equipo de desarrollo (respondió 429 casi siempre);
  la política de acceso automatizado de la Fed; el límite de Alternative.me; los términos de CoinDesk (la página devolvió 404)
  y de CryptoPanic; y un feed de anuncios de Bitunix.
- **Los términos comerciales del plan Demo de CoinGecko:** su página de precios marca "atribución requerida" pero no indica el
  tipo de licencia. Sin clave una llamada pública respondió bien el 2026-10-23; los límites sin clave no están documentados.
- **Los precios de Anthropic** pueden cambiar; las cifras de arriba son de las fechas indicadas.

### Del entorno y de la operación

- **La entrega real de mensajes de Telegram** en el chat de cada usuario: depende de su token, que el proyecto nunca lee.
- **El aspecto visual del dashboard en un navegador real y en un teléfono.** Solo se probó con Vitest/jsdom y `tsc`/`vite build`;
  el modo oscuro y el diseño adaptable están implementados pero sin revisión visual.
- **Linux y macOS como equipo anfitrión:** el proyecto se desarrolló y probó en Windows. La imagen de Docker es de Linux y su
  construcción y ejecución sí se probaron; no se ejecutó nada del anfitrión (scripts de Python, `cron`, permisos del `.env`) en
  Linux ni en macOS.
- **Semanas de operación continua:** la disponibilidad histórica del autor fue del 23,6 %; no hay datos de crecimiento de la base
  a largo plazo, rotación de logs en Docker ni de una suspensión o hibernación del equipo con el reloj de la máquina virtual de
  Docker.
- **Que Docker Desktop inicie con Windows** y el bot vuelva solo tras reiniciar el equipo, y un **Ctrl+C a mitad de la mudanza**
  de nativo a Docker.
- **Las tareas del Programador de Windows:** se verificó la sintaxis construyendo los objetos y, con el Programador real, solo
  que `schtasks /Change /DISABLE` y `/ENABLE` funcionan sobre una tarea ficticia.
- **La revisión a mano de un cierre por SL, TP o trailing** con ticks reales (comparar el precio de salida, la comisión, el
  slippage y el funding contra los logs y la base): requiere mirar resultados, así que debe esperar al registro oficial del
  veredicto.
- **La reproducibilidad del backtest hoy:** se fijó una fecha final (`BACKTEST_OFFICIAL_END_DATE`) para que dos corridas con
  la misma caché den los mismos números, pero no se volvió a correr desde cero con la documentación pública.
