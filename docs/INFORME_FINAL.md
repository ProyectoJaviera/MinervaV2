# Informe final de Minerva v1 (paper trading)

**Fecha:** 2026-10-18 · **Alcance:** Fases 0 a 6 · **Modo:** 100 % paper trading (ninguna orden real)

> ## ⚠ Aviso de riesgos
>
> El bot puede perder dinero y nada garantiza ganancias. Esta versión **no envía órdenes reales** y, con la
> evidencia disponible, **no hay base para pasar a dinero real**: ninguna estrategia por reglas demostró ventaja
> en el backtest y el valor del filtro de IA todavía no se ha medido (ver §1 y §5).

## 1. Resumen ejecutivo

**Qué es.** Minerva evalúa en cada cierre de vela un universo de 10 criptomonedas (top por capitalización con
perpetuo USDT en Bitunix) con seis estrategias por reglas. Cada señal se simula como *operación sombra*; Claude la
etiqueta `APROBADA` o `RECHAZADA` con un prompt **congelado**, y una cuenta paper abre únicamente las aprobadas que
llegaron a tiempo, a través de un motor de riesgo y un simulador de ejecución realista (comisiones, funding,
slippage, liquidación, SL/TP/trailing evaluados con ticks, reconciliación al reiniciar). Todo queda registrado, hay
un dashboard, un bot de Telegram y una API con login. El proyecto está diseñado alrededor de una pregunta:
**¿el filtro de IA aporta valor sobre las señales por reglas?** Y de una restricción: responderla sin sesgo.

**Qué se logró.**

- La plataforma completa de las seis fases, con **1.800 pruebas automáticas de Python** (1.745 unitarias, 51 de punta
  a punta y 4 de integración con la red real, omitidas por defecto) y 106 del frontend, humos contra copias de la
  base real y contra la red real, y el pipeline corriendo en vivo.
- Una **medición ciega y trazable** del valor de la IA: sombras comparables, conglomerados para no contar dos veces
  operaciones solapadas, prompt y features congelados con hash vigilado en cada ciclo, y una *puerta de resultados*
  que, hasta el registro oficial del veredicto, impide que **ningún canal** (API, dashboard, Telegram, avisos, resumen)
  muestre PnL, saldo, precio ni motivo de cierre.
- Docker, respaldo y restauración consistentes, manual de operación y esquema de la base generado del código.

**Qué NO se sabe (y no hay que fingir lo contrario).**

1. **Ninguna estrategia por reglas tiene ventaja demostrable.** En el backtest con criterios pre-registrados, el factor
   de beneficio fuera de muestra (v2) fue 0,970 (`ema_cross`, 556 operaciones), 1,025 (`trend_atr`, 493), 0,742
   (`mean_reversion`, 2.737) y 0,902 (`donchian`, 749), todos bajo el mínimo de 1,2. Las dos variantes de funding son
   experimentales y no entran al veredicto; si se les aplicara el criterio de concentración (≤ 40 %) fallarían (76 % y
   78 %), y su mejor cifra (PF 1,80) sale de solo 21 operaciones.
2. **El valor de la IA está sin medir.** El 2026-10-18 había 5 y 6 conglomerados efectivos por lado de los 300
   exigidos. A ~1,25 conglomerados por día de calendario y lado (~5,27 por día en marcha, con 23,6 % de
   disponibilidad; estimación volátil, medida el 2026-10-17 con 4 conglomerados) el punto de análisis está a **meses
   de calendario**, o a ~2 meses *en marcha*. Y aun llegando a 300, el diseño solo detecta con 80 % de potencia
   efectos de ~10 % del margen por operación (§6): si el efecto real es pequeño, lo esperable es un veredicto
   `INCONCLUSO`.
3. **El brazo ejecutado de la cuenta paper no tiene resultados que mostrar:** acaba de empezar y, por diseño, sus
   resultados están ocultos.
4. **Docker no se probó con un daemon real** (Docker Desktop estaba apagado en el PC donde se desarrolló): ver §5.2.

**Conclusión honesta.** Se entrega un sistema sólido para *medir* —no un sistema rentable—. Que el sistema funcione
no dice nada sobre si gana dinero. El siguiente paso útil es **dejarlo correr con la mayor disponibilidad posible
hasta el punto de análisis** y decidir con el veredicto, no antes.

## 2. Qué se construyó, por fase

Cifras del árbol versionado al cierre de la Fase 6 (2026-10-18): **379 archivos**; `app/` 145 archivos y 21.339 líneas
de Python; `tests/` 129 archivos y 29.206 líneas; `scripts/` 28 archivos y 4.467 líneas; frontend 22 archivos y 2.760
líneas más 10 de pruebas (2.093 líneas); 25 documentos (7.070 líneas). La API tiene 22 rutas, **solo tres de
escritura**: login, logout y `POST /api/control/{action}`.

| Fase | Entregable | Estado |
|---|---|---|
| 0 | Evaluación crítica de `openspec.md`, verificación de APIs y diseño (`docs/FASE0.md`) | Aprobada |
| 1 | Estructura, datos de mercado de Bitunix, vertical mínima de punta a punta | Aprobada |
| 2 | Universo dinámico y backtest riguroso | Cerrada |
| 3 | Riesgo, simulador realista, señales en vivo, sombras, medición del LLM, brazo ejecutado (3.1–3.9) | Implementada |
| 4 | Noticias y memoria de lecciones (apagadas durante la medición) | Aprobada |
| 5 | Dashboard completo, controles, push de Telegram | Aprobada |
| 6 | Docker, pruebas de punta a punta, documentación final y limpieza | Este informe |

### Fase 0: evaluación y decisiones

Se evaluó `docs/openspec.md` contra la especificación: se reutilizó como referencia de bajo nivel (firma HMAC,
reconexión del WebSocket, mecánica de TP parcial y trailing) y se descartó su alcance mono-activo, sus SL/TP fijos y
su LLM binario sin medición. Se verificaron los endpoints públicos de Bitunix contra su documentación oficial (sin
tarifa pública: se usa VIP0; sin open interest público: se omite). CoinGecko Demo exige una clave gratuita (100
llamadas/min, 10.000/mes). Diez decisiones del usuario fijaron capital (100 USDT), drawdown máximo (20 %), circuit
breaker (4 pérdidas, 8 h), presupuesto del LLM (1 USD/día con corte duro), zona horaria de reportes
(`America/Santiago`) y el criterio de paso a real (≥ 30 días y ≥ 100 operaciones).

### Fase 1: vertical mínima

Cliente REST público de Bitunix (7 endpoints verificados, limitador de 10 req/s, backoff exponencial) y un único
módulo de red (`app/core/http.py`, con `truststore` para redes con inspección TLS); caché histórica de velas
paginada; cliente WebSocket; indicadores (EMA, RSI de Wilder, ATR) con pandas puro; `PaperBackend` y un
`BitunixBackend` que **lanza `NotImplementedError` al instanciarse** y que ningún módulo de `app/` importa;
persistencia con `aiosqlite`; scheduler; API y frontend mínimos. Verificado con datos reales de Bitunix.

### Fase 2: universo y backtest

Universo dinámico (CoinGecko menos stablecoins, envueltos y liquid-staking, intersectado con perpetuos de Bitunix)
con un grupo de control BTC/ETH. Motor de backtest **sin sesgo de anticipación** (fill en la apertura de la vela
siguiente, liquidación por mark price, SL/TP por último precio, funding real prorrateado) y seis candidatas de
familias distintas, validadas dentro/fuera de muestra y con *walk-forward*, con **criterios de descarte escritos antes
de correr** (`docs/FASE2_CRITERIOS.md`). Una simulación de cartera Monte Carlo (200 corridas) se reporta solo como
información.

**Resultado:** ninguna estrategia supera los criterios congelados. Un error de redondeo en el tope de SL
(`50.000000000000014 > 50`) había descartado 2.042, 509 y 1.037 entradas según la estrategia; se corrigió y se
reejecutó (v2, vigente); el factor de beneficio de `ema_cross` pasó de 1,12 a 0,97. Se reportó sin relajar ningún
umbral. La investigación también encontró y corrigió defectos reales (diagnóstico erróneo de "bloqueo de red", una
estrategia que nunca emitía señales, un sesgo alfabético en la admisión de la cartera, un `INSERT` desincronizado,
un piso de caché ausente y un desfase de uno en el chequeo de huecos).

### Fase 3: riesgo, simulador, medición y brazo ejecutado

| Subfase | Qué hace |
|---|---|
| 3.1 | `stop_engine`: la lógica de SL/TP/trailing del backtest extraída sin cambios y ampliada a modo tick |
| 3.2 | Motor de riesgo en vivo: kill switch, stop por drawdown (duro/alerta), circuit breaker, pérdida diaria, elegibilidad por estrategia, cupos, tope de SL y margen; solo bloquea **entradas**, nunca cierres; cada rechazo se audita |
| 3.3 | Generador de señales en vivo: universo × 6 estrategias, una fila por (símbolo, estrategia, timeframe, vela cerrada), HOLD incluido |
| 3.4 | Monitor de posiciones: WebSocket, SL/TP/trailing/liquidación por ticks, latido del feed, slippage, `fill_source`, **reconciliación al arrancar** con velas de 1 minuto |
| 3.5 | Operaciones sombra con las mismas funciones que la cuenta paper (paridad de PnL probada), agrupadas por símbolo/lado/vela y analizadas por **conglomerados** |
| 3.6 | LLM: esquema y registro completo (`llm_logs` con disparador de inmutabilidad), presupuesto con corte duro, tres veredictos, pilotos v1→v4 y **congelamiento del prompt**, etiquetado en vivo, herramientas del veredicto y del placebo |
| 3.7 | Bot de Telegram por *long-polling* (7 comandos; `/pause`, `/resume` y `/kill` con confirmación) |
| 3.8 | Reporte de paso a dinero real: **solo recomienda**; cada criterio es PASA / NO PASA / SIN DATOS y la falta de datos nunca pasa |
| 3.9 | Brazo ejecutado: abre en la cuenta paper solo las `APROBADA` con decisión en ≤ 60 s, vía el motor de riesgo; registro idempotente de cada decisión considerada |

Dos **incidentes de estabilidad** dejaron mejoras estructurales. El 2026-10-10, tras ~5,75 días apagado, la
reconciliación falló para tres sombras abiertas por velas de 1 minuto faltantes: Bitunix, en respuestas de
`limit=200` sin `startTime`, a veces omite una vela real que sí devuelve con una ventana estrecha. El monitor cerró
dos con el precio del reinicio en vez del nivel de SL/TP (un TP quedó en ~6 veces el resultado previsto). Se añadió
detección y reparación de huecos con pedidos estrechos (`find_gaps`/`fill_gaps`), que corre sola al descargar: 244
huecos reparados y 0 pendientes sobre la copia del incidente. El 2026-10-11 el bot se congeló unas 4 horas porque el
pipe de la consola quedó pausado y el log síncrono bloqueó el único hilo de asyncio: el logging pasó a colas no
bloqueantes con descarte contado, se añadió un latido y `GET /health` con `last_cycle_at` (la señal de negocio,
distinta del latido del loop). Después, una alerta externa por Telegram y un resumen diario viven en procesos
separados del bot.

**El prompt del LLM se iteró con pilotos reales y se congeló el 2026-10-12.** v1 aprobó el 90 % (fuera de la banda
15-85 %) y las 21 respuestas leídas una por una mostraron defectos concretos (convención invertida del signo del
funding, geometría liquidación/SL, unidades). v2 (51,7 %) arregló eso y reveló dos fallos de sintaxis JSON y un bug de
unidades del ATR (la fracción se leía como porcentaje). v3 (46,9 %) dejó un único defecto: el signo del funding en
SHORT con funding negativo, **pese a explicarlo en el texto**. v4 lo resolvió de forma **determinista** (un campo
calculado `funding_effect_on_position`): 45/45 correctos, 60 % de aprobación, y quedó congelado
(`FROZEN_PROMPT_SHA256 = 0fa0dd75a3fecdae36f3b77c8b7ec5d97d58a2238349283c61bfa7632188c777`).

### Fase 4: noticias y memoria

Recolección de noticias aislada, gratuita y determinista (sentimiento por léxico, sin LLM): **GDELT**, comunicados
de política monetaria de la **Fed** y **Fear & Greed** de Alternative.me, con atribución junto al dato. Se
**excluyeron** Cointelegraph (prohíbe el scraping y el uso para sistemas de IA), CoinDesk (su robots.txt bloquea a
ClaudeBot) y Decrypt (solo uso no comercial). La memoria de lecciones usa solo operaciones cerradas anteriores a la
decisión, exige muestra mínima e intervalo de Wilson del 99 % y pasa por la puerta de resultados. Un prompt v5 quedó
**preparado y sin usar**, con pruebas de huellas literales. Ambos módulos están apagados durante la medición
(`LESSONS_MEMORY_ENABLED` nunca se enciende; las noticias solo en el puerto 8000).

### Fase 5: dashboard, controles y push

Autenticación de un usuario (bcrypt, JWT en cookie `httpOnly`, 24 h), API de solo lectura con **listas blancas clave
por clave** (una columna nueva no sale por olvido), controles de pausa/reanudación/kill que exigen login, mismo
origen, JSON y una palabra de confirmación exacta, y quedan auditados; notificaciones push por Telegram
(*sondeando la base*, sin ganchos en el núcleo); dashboard de cinco pestañas con la atribución "Data provided by
CoinGecko". Con la puerta cerrada, el servidor no consulta siquiera el PnL, y el navegador también falla cerrado.

### Fase 6: Docker, pruebas de punta a punta, documentación y limpieza

- **Docker** (`Dockerfile`, `docker-compose.yml`, `docker/`): imagen multietapa sin Node ni compiladores, versiones
  **exactas** de las 38 dependencias (`docker/requirements.lock`), usuario sin privilegios, `.env` fuera de la imagen,
  puerto solo en `127.0.0.1`, volúmenes con nombre, `docs/` montado de solo lectura, los **mismos interruptores que
  `start_bot.ps1`**, certificados raíz opcionales y healthcheck propio. **No reemplaza** el arranque actual.
- **Pruebas de punta a punta** (`tests/e2e/`, 51 pruebas): el sistema real sobre una base temporal y solo el exterior
  simulado (Anthropic, Bitunix, WebSocket y Telegram), con un candado de red. Cubren el ciclo de vela de 4 h LONG y
  SHORT (señal, sombra, etiqueta, brazo, monitor, cierre), TP, SL, trailing, liquidación, circuit breaker con
  post-mortem, pausa y kill desde Telegram y dashboard, reinicio con reconciliación y un **barrido de que, con la
  puerta cerrada, ningún canal muestra resultados**.
- **Herramientas de operación**: `scripts/backup_db.py` y `scripts/restore_db.py` (consistentes, verificados y
  reversibles), `scripts/resume_drawdown_stop.py` y `scripts/export_schema_doc.py` (genera `docs/ESQUEMA_BD.md`: 28
  tablas, 13 índices explícitos, 1 disparador).
- **Documentación final**: README, `docs/MANUAL_OPERACION.md` y este informe.
- **Limpieza**: análisis de código muerto con `vulture` (entorno aislado, usando `app/`, `scripts/` y `tests/` como
  fuentes de uso) y comprobación aparte de cada campo de `Settings` por nombre y alias. Se **retiraron 7 campos de
  configuración sin ninguna referencia** (`TRADING_SYMBOLS`, `KLINE_INTERVAL`, `STRATEGY_HISTORY_BARS`,
  `ACTIVE_STRATEGY`, `MARGIN_MODE`, `ANTHROPIC_HAIKU_MODEL` y `CONFLUENCE_SCORE_THRESHOLD`: vestigios de la vertical
  mínima o previstos y nunca usados) y 3 funciones sin uso. Se **conservó a propósito** lo que no es muerto de verdad:
  rutas y validadores de FastAPI/pydantic (vulture no ve los decoradores), `BitunixBackend` (interfaz exigida por la
  especificación y desactivada), los envoltorios de endpoints verificados (`get_depth`, `get_categories_list`),
  `maker_fee_pct` (la especificación pide comisiones maker y taker), constantes de vocabulario documentadas
  (`PILOTO`, `FILL_REST`, `VALID_VERDICTS`; esta última en un módulo de la puerta de resultados, que no se tocó) y
  algunos campos informativos de dataclasses. Se corrigieron las notas obsoletas (`config.py`, `llm/__init__.py`,
  `strategies/base.py`, `market/bitunix_ws.py` y otras). No quedaba ningún `TODO`/`FIXME`. `.env.example` **no tenía 31
  variables** de `Settings` (backtest, universo, noticias, memoria): añadidas, y una prueba exige que estén todas, que
  las credenciales vayan vacías y que copiar la plantilla dé exactamente los valores por defecto del código. Una
  prueba de higiene escanea los 379 archivos que git versionaría (claves de Anthropic, tokens de Telegram, hashes
  bcrypt, claves privadas, tokens de GitHub y de AWS y asignaciones a variables de credencial) y comprueba que
  `.gitignore` cubre lo que nunca debe subirse (reforzado con `.env.*`, `logs/` y `*.sqlite*`). Revisión única del
  historial de git (solo conteos): 0 coincidencias de esos patrones y 0 archivos prohibidos entre los 374 que alguna
  vez se añadieron; es una revisión por patrones, no una garantía contra formatos de secreto desconocidos.

## 3. Cumplimiento de la especificación

Contrastado línea por línea con `docs/SPEC.md`.

| Requisito | Estado | Nota |
|---|---|---|
| 100 % paper; sin código de órdenes reales activable | **Cumplido** | `BitunixBackend` lanza `NotImplementedError` y una prueba por AST garantiza que nada de `app/` lo importa |
| Sin credenciales de Bitunix | **Cumplido** | Solo endpoints públicos |
| Claude vía SDK asíncrono, JSON validado con esquema, todo registrado | **Cumplido** | `llm_logs` guarda la decisión completa; la etiqueta es inmutable por disparador |
| Python 3.11, asyncio, FastAPI, SQLite, autenticación | **Cumplido** | |
| Universo top 10, sin stablecoins ni envueltos, solo perpetuos USDT, refresco de 24 h, regla de salida | **Cumplido** | Regla de `FASE2_PLAN.md`: un activo que sale del universo no se cierra a la fuerza; deja de generar señales nuevas (el generador solo recorre el universo vigente) y su posición abierta sigue gestionada por el monitor, que no depende del universo, hasta su cierre normal. Un activo cuyo precio en CoinGecko y Bitunix difiere más de 5 % no entra |
| Simulador realista: comisiones, funding, slippage, liquidación aislada, mínimos, ticks, reconciliación | **Cumplido** | Fase 3.4; la reconciliación está probada en las pruebas de punta a punta |
| Capacidades 1-9 del backend (abrir, cerrar, TP, SL, trailing, saldo, posiciones, precios, diseño futuro) | **Cumplido** | |
| 3 a 5 estrategias con backtest y descarte de las que no muestren ventaja | **Cumplido** | Seis candidatas; **ninguna muestra ventaja**. Se conservan como *generadores de señales* para medir el filtro de IA, no como estrategias rentables |
| Gestión de riesgo (posiciones, pérdida diaria, breaker, kill switch por API/frontend/Telegram, exposición, correlación, datos obsoletos) | **Cumplido** | Correlación: `MAX_SAME_DIRECTION_POSITIONS` |
| Criterios de paso a real e informe automático | **Cumplido, bajo demanda** | `real_money_report.py`; solo recomienda y exige el registro oficial |
| Comandos de Telegram (`/status`, `/positions`, `/balance`, `/pause`, `/resume`, `/metrics`, `/kill`) | **Cumplido** | |
| Telegram: aperturas, cierres con motivo y PnL, breaker, resumen diario | **Parcial, por diseño** | Los cierres **no llevan motivo ni PnL** hasta el registro oficial; el resumen diario nunca trae resultados |
| **"La IA decide abrir y cerrar sin aprobación humana"** y el JSON de decisión con `sl`, `tp`, `trailing` y `porcentaje_exito_estimado` | **Desviación deliberada** | La IA **filtra** (`APROBAR`/`RECHAZAR`, `confianza`, `razonamiento`) las señales de estrategias por reglas; los niveles vienen de la estrategia y los cierres son mecánicos. Es lo que permite comparar aprobadas con rechazadas bajo el mismo cálculo; una IA que fija niveles y cierres libremente no se puede medir contra un contrafactual |
| Umbral mínimo de confianza para abrir | **No implementado** | La `confianza` se registra pero no se usa como umbral; la decisión es binaria |
| Memoria, lecciones, post-mortem, calibración | **Implementado, apagado** | Se enciende solo tras el veredicto, con aprobación y un prompt nuevo |
| Noticias con deduplicación, activo afectado y sentimiento | **Cumplido** | Tres fuentes gratuitas verificadas; no entran al prompt de la medición |
| Frontend: banner permanente de MODO PAPER TRADING, autenticación, noticias, lecciones, avance de la medición | **Cumplido** | |
| Frontend: responsivo y modo oscuro | **Implementado, sin revisión visual** | Variantes `dark:` en los 17 archivos de interfaz (siguen la preferencia del sistema) y diseño adaptable; nadie lo ha mirado en un navegador real ni en un teléfono (§5.2) |
| Frontend: saldo, PnL no realizado, ROI, curva de equity, PnL agrupado, winrate | **No mostrado, a propósito** | Son resultados: se habilitan con el registro oficial. Las curvas y agrupaciones **no están construidas** (quedan para después del veredicto) |
| Frontend: tiempo real por WebSocket, PWA/APK | **No hecho** | Consulta periódica; fuera del alcance acordado |
| Controles: cerrar posición, cerrar todo, editar parámetros de riesgo | **No hecho, a propósito** | Solo pausa, reanudación y kill; menos superficie de ataque |
| Docker Compose, pruebas de punta a punta, guía de puesta en marcha, aviso de riesgos | **Cumplido** (Docker sin daemon real) | |
| Esquema de base de datos | **Cumplido** | `docs/ESQUEMA_BD.md`, generado del código y vigilado por una prueba |
| Informe de métricas de paper trading | **Aún no producible** | La medición es ciega y el gate está cerrado; lo generarán `ai_value_report.py` y `real_money_report.py` tras el registro oficial |
| Paper trading mínimo de 30 días corridos | **Pendiente** | El brazo ejecutado acaba de empezar |

## 4. Decisiones clave

1. **Paper puro y ejecución real solo como interfaz.** Es la regla crítica del proyecto y se hace cumplir con pruebas
   (AST), no con disciplina.
2. **Pre-registrar los criterios antes de ver resultados** (Fase 2 y veredicto del LLM). Es lo que permitió informar
   "ninguna estrategia pasa" sin discusión.
3. **Fusionar la antigua Fase 4 (LLM) con la Fase 3.** Medir el valor del LLM exige que el LLM ya esté decidiendo, así
   que no podía quedar en una fase posterior separada.
4. **Operaciones sombra en lugar de dos cuentas.** Cada señal tiene una sombra con margen ilimitado y el mismo
   cálculo de costos; las aprobadas y las rechazadas se comparan sobre el *mismo* universo de señales. La sombra **no
   bloquea** por solapes (bloquear sesgaría la comparación), así que el análisis cuenta **conglomerados**.
5. **Un solo punto de análisis: 300 conglomerados efectivos por lado, δ = 3 % del margen.** Tres veredictos
   (`APORTA_VALOR` si el límite inferior del IC es > 0; `NO_APORTA_VALOR` si el superior es < δ; `INCONCLUSO` en el
   resto o si `SIN_LLM` > 10 %). Placebo previo para calibrar el criterio; si es no informativo se espera 7 días.
   **Una sola mirada y un solo registro oficial.**
6. **Medición ciega aplicada por código.** La puerta de resultados (`reveal`) falla cerrada, se aplica con listas
   blancas y se comprueba con un barrido sobre todos los canales.
7. **Congelar el prompt y vigilarlo en cada ciclo.** El hash cubre texto *y* features; si no coincide, el etiquetado se
   detiene solo. Cuando un defecto resistió la explicación en texto se resolvió **calculándolo**, no pidiéndoselo al
   modelo.
8. **Todo lo nuevo, apagado por defecto, con su interruptor y en una tarea aislada** (`run_isolated`): un fallo de
   Telegram, noticias, memoria o push no alcanza al scheduler, al etiquetado ni al monitor.
9. **Proceso único con SQLite en modo WAL.** Simple de operar y suficiente; el costo es que hay un solo punto de
   falla y que el apagado duro de Windows corta lo que esté en vuelo (por eso la ventana de reinicio).
10. **Reconciliar al arrancar.** Los SL/TP/liquidaciones que habrían ocurrido con el bot apagado se reproducen con
    velas de 1 minuto; si faltan más de 2 velas la posición queda abierta con un CRITICAL.
11. **Fuentes externas solo gratuitas y con sus términos verificados.** Lo único que cuesta dinero es la API de
    Anthropic, con tope diario y corte duro.
12. **Docker como alternativa, no como reemplazo.** El bot real sigue con `start_bot.ps1` hasta la migración;
    volúmenes con nombre para no abrir nunca el `data/` real ni depender del WAL sobre carpetas compartidas.

## 5. Qué está verificado y qué no

### 5.1 Verificado

| Qué | Con qué evidencia |
|---|---|
| Lógica de riesgo, simulación y datos | 1.800 pruebas de `pytest` recogidas (1.745 unitarias, 4 de integración con la red real y 51 de punta a punta): 1.796 pasan y las 4 de red real se omiten salvo con `MINERVA_RUN_LIVE_TESTS=1`; 106 de Vitest; `ruff` limpio; `tsc` y `vite build` limpios |
| Casos límite de SL/TP/trailing, liquidación, comisiones y funding | Pruebas dedicadas por función de riesgo y de simulación, y paridad de PnL entre la sombra y la cuenta paper |
| Ciclo completo de punta a punta | 51 pruebas con el sistema real y el exterior simulado, con un candado que impide cualquier conexión de red |
| Persistencia y migraciones con datos reales | Cada tarea sobre datos se probó contra una **copia** de la base real (ruta distinta verificada con `assert`): migraciones aditivas, mismas filas en las demás tablas, `PRAGMA integrity_check` |
| Mercado real | Humos con la red real de Bitunix (REST y WebSocket), Fed y Fear & Greed |
| Ocultamiento de resultados | Barrido sobre API, dashboard, Telegram, push y resumen; mutaciones de prueba del backend que las pruebas detectaron |
| Aislamiento de la medición | Con el brazo/noticias/memoria/push encendidos o apagados, `llm_logs`, `shadow_trades` y el avance son idénticos; el hash congelado está fijado por prueba |
| Docker, hasta donde se puede sin daemon | `docker compose config`, 90 pruebas estáticas del `Dockerfile`/compose/healthcheck, resolución de dependencias para Linux, réplica de la etapa de Node y humo del árbol de la imagen |
| Respaldo y restauración | 21 pruebas, humo sobre una copia real (con el conteo de tablas y filas antes y después) |

### 5.2 No verificado (declarado, no oculto)

- **Docker con un daemon real:** `docker compose build`, arranque, estado `healthy`, que el volumen herede el dueño del
  usuario sin privilegios, el montaje de `docs/` en solo lectura, el flujo de certificados corporativos y cualquier
  comportamiento específico de Linux. El manual indica cómo probarlo sin riesgo (`MINERVA_REAL_BOT=false`).
- **Entrega real de mensajes de Telegram** en el chat del usuario (requiere el token del `.env`, que no se lee). El
  notificador envía un mensaje de arranque para comprobarlo la primera vez.
- **Aspecto visual del dashboard** en un navegador real (solo Vitest/jsdom, `tsc` y `vite build`), y una mutación de
  prueba del frontend (forzar `reveal = true` en `AccountTab`): el clasificador de permisos la bloqueó, se revirtió y
  se comprobó, y no se reintentó.
- **Fuentes de noticias:** el formato real de los artículos de GDELT y su límite efectivo desde este PC (respondió 429
  casi siempre), la política de acceso automatizado de la Fed, el límite de Alternative.me, los términos de CoinDesk
  (404) y de CryptoPanic, y un feed de anuncios de Bitunix.
- **Ejecución real en Bitunix** (`docs/PASO_DINERO_REAL.md`): latencia de órdenes, límites de tasa, modo de posición,
  mecánica de cobro del funding, base y tipo de comisión de la orden a mercado, mínimo en USDT, semántica de
  `priceProtectScope` y ejecución del SL/TP en el exchange ante saltos de precio. No hay `BitunixBackend`.
- **Revisión a mano de un cierre por SL, TP o trailing con ticks reales:** el monitor ya cierra sombras en vivo y las
  pruebas simulan todos los casos, pero nadie ha contrastado a mano el precio de salida, la comisión, el slippage y
  el funding de un cierre real contra los logs y la base (el recordatorio de la 3.4 sigue vigente). Hacerlo implica
  mirar resultados, así que **debe esperar al registro oficial del veredicto**.
- **Tareas del Programador de Windows:** se verificó la sintaxis construyendo los objetos; el registro puede exigir
  una terminal con privilegios.
- **Operación 24/7 sostenida:** la disponibilidad histórica fue de 23,6 %; no hay datos de semanas de ejecución
  continua, ni del crecimiento de la base a largo plazo.
- **Vigilancia automática del modelo:** el hash cubre el prompt y las features, **no el modelo**. Si
  `ANTHROPIC_SONNET_MODEL` cambiara, `/health` no lo señalaría. `llm_logs.model` lo registra y
  `scripts/measurement_identity.py` lo comprueba a mano (una vez por semana, incluso antes de un reinicio); no hay
  una guarda automática equivalente a la del hash.

## 6. Riesgos

| Riesgo | Impacto | Mitigación | Residual |
|---|---|---|---|
| **Sin ventaja estadística:** ninguna estrategia por reglas la demostró | Alto: una cuenta real perdería | No hay ejecución real; los criterios de paso a real exigen veredicto, ≥ 30 días y ≥ 100 operaciones | Es el hallazgo central, no un riesgo mitigado |
| **Potencia insuficiente:** σ ≈ 44,9 % del margen por operación; con 300 conglomerados por lado el diseño detecta (potencia 80 %) efectos de ~10 % del margen, y un efecto de 3 % requeriría del orden de 3.500 por lado | Alto: un `INCONCLUSO` con 300 sería lo esperable si el efecto es pequeño | El veredicto lo contempla y lo explica; la decisión de ampliar la muestra es explícita | Real |
| **Plazo:** ~237 días de calendario al ritmo medido; ~56 días en marcha | Alto | Maximizar la disponibilidad (PC 24/7); el manual documenta la mudanza | Depende de la disponibilidad |
| **Contaminación de la medición** (cambio de código, de prompt, de modelo o mirar resultados antes de tiempo) | Alto: invalida el veredicto | Hash vigilado, puerta de resultados, una sola mirada, `scripts/measurement_identity.py` cada semana | El modelo solo se vigila a mano |
| **Punto único de falla:** un proceso, un PC, Windows con apagado duro | Medio | Reconciliación al arrancar, alertas externas, ventana de reinicio, respaldos | Medición perdida mientras está apagado |
| **Brecha paper-real:** latencia, rechazos, profundidad de libro y comisiones reales no están modelados por completo; posiciones diminutas (5 USDT × 10) chocan con mínimos del exchange (p. ej. `TRXUSDT`) | Alto si se pasara a real | Slippage y comisiones configurables; el reporte de la 3.8 mide retrasos y símbolos no ejecutables | No verificado contra el exchange |
| **Fuentes gratuitas poco fiables** (GDELT con 429, cambios de API) | Bajo | Caché, backoff, salud por fuente, bloqueo de entradas con datos obsoletos, noticias fuera del prompt | Aceptado |
| **Costo de la API** | Bajo: tope de 1 USD/día con corte duro | Límite mensual en la consola de Anthropic (recomendado en el manual) | Un presupuesto corto sube los `SIN_LLM` y daña la medición |
| **Seguridad:** `.env` en claro en disco, sesión sin HTTPS (solo loopback), un único usuario | Medio | La API escucha en `127.0.0.1`, cookie `httpOnly` y `SameSite=Strict`, controles con confirmación y auditoría, `DASHBOARD_CONTROLS_ENABLED=false` | No exponer el puerto fuera del PC sin un proxy con TLS |
| **Fugas residuales de resultados:** los logs de nivel INFO dejan pasar el motivo de cierre de las sombras y el PnL neto de las posiciones paper; `closed_at` y los niveles permiten inferir algo con muchas operaciones; un breaker activo implica una racha perdedora | Medio para la ceguera | Declarado y aceptado; evitar abrir los logs | Pierde importancia al abrirse el reveal |
| **Concentración y muestra del brazo ejecutado:** ~2-3 aperturas por día en marcha; 100 cerradas son ~35-50 días en marcha | Medio | Los criterios exigen concentración ≤ 40 % y un IC | Plazo largo |

## 7. Hallazgos de la Fase 6

1. **Docker apagado en el PC de desarrollo:** no se encendió por iniciativa propia (servicio pesado en el PC del bot
   real). Se dejó todo listo y validado hasta donde no necesita daemon.
2. **`risk_engine.resume_drawdown_stop` no tenía por dónde ejecutarse:** los avisos prometían "se levanta aparte" y
   no existía ningún CLI, API ni comando. Se añadió `scripts/resume_drawdown_stop.py` (informativo por defecto, exige
   `--confirm`, no imprime el equity con el reveal cerrado, no reinicia el contador del criterio de dinero real).
   Esta adición **supera la lista pedida**; se hizo porque sin ella el stop duro era irreversible en la práctica.
3. **`/health` pasa a `stale` también durante una pausa deliberada:** el healthcheck del contenedor lo acepta cuando
   `control.paused` es verdadero.
4. **El aviso push no puede anunciar una pausa seguida de una reanudación dentro del mismo sondeo** (compara estados):
   la auditoría sí las registra. Documentado y con prueba.
5. **El código resuelve `frontend/dist` y `docs/` relativo al paquete:** dicta el diseño de la imagen (se ejecuta desde
   `/app`, no se instala en `site-packages`).
6. **Redes con inspección TLS** exigen el certificado raíz también dentro del contenedor (`docker/certs/`).
7. **El modelo del LLM no estaba bajo vigilancia** (el hash congelado no lo cubre; §5.2). Se añadió
   `scripts/measurement_identity.py` (solo lectura, solo conteos y fechas; sobre la copia real: una sola combinación
   —`claude-sonnet-5-5`, prompt `signal_review_v4`, `features_v2`, hash congelado—). Es otra adición **fuera de la
   lista pedida**, nacida de un hallazgo; una guarda automática dentro del bot (como la del hash) se deja para después
   del veredicto, porque tocaría el etiquetador de la medición.
8. **Las pruebas de punta a punta no encontraron fallos del producto.** Los problemas aparecieron en el propio arnés
   (series sintéticas que disparaban estrategias, falsos positivos del barrido de fugas por redondeos o subcadenas);
   conviene decirlo: pasar no prueba ausencia de defectos en lo que no se simula (red, exchange, navegador).
9. **`.env.example` estaba incompleto:** faltaban 31 variables que `Settings` sí lee, y siete variables documentadas no
   hacían nada (configuración vestigial que daba una falsa sensación de control, p. ej. `MARGIN_MODE`). Corregido y
   protegido por pruebas.
10. **El código muerto que vulture no ve:** el análisis automático solo encontró lo evidente (un nombre repetido en
    otro módulo oculta un campo sin uso, como `symbols`); hizo falta contrastar cada campo de configuración con sus
    usos reales.
11. **Sin secretos, y ahora vigilado:** ni en los archivos versionados ni en el historial; la protección ya no
    depende de la disciplina sino de una prueba.

## 8. Próximos pasos

### Inmediatos (operación)

1. **Un único reinicio del bot real** (`stop_bot.ps1` + `start_bot.ps1`, entre :10 y :40 de una hora UTC que no sea de
   cierre de 4 h) para cargar el backend de la Fase 5 (rutas nuevas, `control_audit`, notificador push y los cambios
   de `/pause`, `/resume` y `/kill`). El frontend compilado y el banner de pausa ya se sirven sin reiniciar.
2. **Probar Docker con un daemon real** en el PC 24/7, sin riesgo: `MINERVA_REAL_BOT=false` y un respaldo. Ver el
   manual.
3. **Migrar al PC 24/7** (nativo o con Docker) y poner las tareas de vigilancia: es la palanca que más acorta la
   medición.
4. **Respaldo semanal fuera del PC** y la comprobación semanal de la identidad de la medición
   (`scripts/measurement_identity.py`: un solo modelo, el prompt congelado, una sola versión de features).

### Cuando se abra el gate

Seguir `docs/VEREDICTO_3_6.md` al pie de la letra (resumido en el manual, §9): copia única → placebo oficial → solo si
sale CALIBRADO, el informe una única vez y su registro oficial. Después:

- **`APORTA_VALOR`:** revisar sensibilidad y desglose; entonces (decisiones separadas) evaluar encender noticias y
  memoria con el prompt v5 —que es un **experimento nuevo** con su propia constante congelada, no una continuación—,
  y recién con ≥ 30 días y ≥ 100 operaciones cerradas del brazo ejecutado correr `real_money_report.py`. Pasar a
  dinero real exigiría construir y verificar `BitunixBackend` contra la documentación oficial (§5.2), partir con un
  capital mínimo y un SL por posición, y mantener la pausa y el kill switch operativos.
- **`NO_APORTA_VALOR`:** detener el gasto en la API; las estrategias por reglas siguen sin ventaja, de modo que
  tampoco hay base para dinero real. Replantear la fuente de señales antes de invertir más.
- **`INCONCLUSO`:** leer el motivo y decidir explícitamente si ampliar la muestra (coste: meses) o cerrar el
  experimento.

### Mejoras técnicas, después del veredicto

Curvas de equity y PnL agrupado (son resultados), cierre manual de posiciones y edición de parámetros de riesgo desde
el dashboard, WebSocket/PWA, vigilancia del modelo (guardia equivalente a la del hash), redacción de resultados en los
logs INFO, un proxy con TLS si alguna vez se expone fuera del PC, y programar el informe de métricas de paper trading.

## 9. Estado de despliegue y qué requiere reinicio

| Pieza | ¿Requiere reiniciar el bot real? |
|---|---|
| Backend de la Fase 5 (rutas, `control_audit`, push, comandos de control) | **Sí, un solo reinicio** (pendiente desde la Fase 5) |
| Archivos de Docker, pruebas, documentación, scripts nuevos (`backup_db`, `restore_db`, `resume_drawdown_stop`, `measurement_identity`, `export_schema_doc`, `export_docker_requirements`) | No |
| Los 9 archivos de `app/` que cambió la Fase 6: solo docstrings (`core/http.py`, `llm/__init__.py`, `market/bitunix_ws.py`, `strategies/base.py`, `trading/signal_generator.py`) y retirada de código sin uso (`config.py`: 7 campos; `registry.py`, `backtest_repo.py` y `funding_repo.py`: 3 funciones) | No lo exigen por sí mismos: **se cargan con ese mismo reinicio** y no cambian el comportamiento. Un `.env` que aún tenga las variables retiradas sigue funcionando (se ignoran) |

El bot real (puerto 8000) **no se tocó** durante la Fase 6: no se detuvo ni se reinició (solo se comprobó que seguía
escuchando) y no se leyó ni se imprimió ninguna credencial.

## 10. Mapa de documentos

`README.md` (qué es y cómo arrancar) · `docs/MANUAL_OPERACION.md` (operar) · este informe · `docs/ESQUEMA_BD.md`
(generado) · `docs/SPEC.md` (especificación) · `docs/FASE0.md` … `docs/FASE6_PLAN.md` (plan y decisiones de cada fase)
· `docs/FASE3_6_LLM.md` (diseño de la medición) · `docs/VEREDICTO_3_6.md` (procedimiento del veredicto) ·
`docs/PASO_DINERO_REAL.md` (criterios y riesgos de un eventual paso a real) · `docs/MIGRACION_PC.md` (tareas de
Windows y mudanza nativa) · `PROGRESS.md` (bitácora completa).
