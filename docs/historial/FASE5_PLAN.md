# Fase 5: dashboard completo y Telegram enriquecido (plan e implementación, 2026-10-18)

> **Estado: IMPLEMENTADA (2026-10-18).** El plan de abajo se escribió antes de programar; lo que cambió al
> implementar, la verificación y el procedimiento de activación están en "Implementación" (al final).

Alcance: `docs/SPEC.md` ("FRONTEND", "NOTIFICACIONES TELEGRAM"), `docs/FASE2_PLAN.md` (secciones D y E:
mostrar universo y veredictos de backtest, notificar cambios de universo, atribución de CoinGecko) y
`docs/FASE3_PLAN.md` ("Hoja de ruta posterior": frontend, autenticación y Telegram enriquecido).
Autenticación ya existe (pre-Fase 5). Lo que sigue es lo que pediste en la revisión de `b62d261`.

## Reglas (de tu revisión de b62d261)

1. **Nada toca el prompt congelado, las sombras, las etiquetas ni `measurement_keys`.** Esta fase no
   modifica `app/llm/*`, `shadow_live_labeler.py`, `shadow_book.py`, `scheduler.py` ni `paper_execution.py`.
   Las huellas de la Fase 4 (hash `0fa0dd75...`, `prompts.py`, `market_features.py`) siguen fijadas por
   pruebas; el notificador lee la base y no se engancha a nada del núcleo.
2. **La cuenta paper no muestra resultados mientras `is_outcome_reveal_allowed` sea falso**: ni PnL, ni
   precio de salida, ni motivo de cierre (ver la matriz de exposición). La puerta falla cerrada.
3. **Controles del dashboard** (pausar, reanudar, kill switch): confirmación explícita, la misma
   advertencia que Telegram de que detienen la medición, y registro de quién y cuándo.
4. **Notificaciones push de Telegram**: aisladas (un fallo de Telegram no afecta a nada), con interruptor
   propio, encendido solo en el puerto 8000.
5. Sin costo nuevo: ningún servicio nuevo de pago; Telegram y CoinGecko ya se usaban.

## Verificación de fuentes externas (2026-10-18)

Leyenda: **[V]** verificado en la documentación oficial · **[NV]** no pude verificarlo.

| Tema | Resultado |
|---|---|
| **Atribución de CoinGecko** | [V] Términos de la API (`coingecko.com/en/api_terms`): hay que mostrar atribución de forma prominente, con enlace, y remiten a la guía de marca. [V] La guía (`brand.coingecko.com/resources/attribution-guide`) acepta literalmente **"Data provided by CoinGecko"** (también "Price data by CoinGecko", "Source: CoinGecko", "Powered by CoinGecko API"), con enlace a `https://www.coingecko.com`, "en un lugar visible, cerca de donde se muestran los datos (arriba o abajo del conjunto)". Los términos piden letra legible de al menos 10 px. **Decisión**: el texto de CLAUDE.md ("Data provided by CoinGecko") es una frase aceptada; va enlazado, bajo la tabla del universo (junto al dato) y en el pie de todo el dashboard |
| **Límites de envío de Telegram** | [V] FAQ de la Bot API: "evita enviar más de un mensaje por segundo" a un chat; grupos hasta 20 por minuto; difusión ~30 por segundo; al excederlo, errores 429. [V] `ResponseParameters.retry_after`: "segundos que faltan antes de repetir la petición" ante flood control. [V] `sendMessage.text`: 1 a 4096 caracteres. **Decisión**: separación mínima de 1,2 s entre mensajes, respeto de `retry_after`, máximo 10 mensajes por ciclo de sondeo |
| Que el chat real reciba los mensajes | [NV] requiere el token real (`.env`, que no leo) y reiniciar el bot: lo verificas tú. El notificador manda un mensaje de arranque ("notificaciones activas") para que lo compruebes a la primera |

## Qué se ve y cuándo (matriz de exposición)

| Dato | Reveal cerrado | Reveal abierto |
|---|---|---|
| Posición: id, símbolo, lado, estrategia, estado, apalancamiento, margen, nocional, cantidad, precio de entrada, SL, TP, trailing, liquidación estimada, riesgo del SL, origen de la decisión, hora de apertura **y de cierre** | visible | visible |
| Precio de salida, motivo de cierre, PnL bruto y neto, comisiones, funding, slippage, mejor precio, stop efectivo, origen del fill | **no viaja en el JSON** | visible |
| Saldo, equity, PnL realizado y flotante, ganadas/perdidas, profit factor | oculto | visible |
| Registro del brazo ejecutado (abiertas y omitidas con motivo, retraso de decisión, precios de entrada) | visible | visible |
| Estado de riesgo: pausa, kill switch, breaker (hasta cuándo), stop por drawdown (activo/inactivo), pérdida diaria (bloqueada sí/no), cupos y límites, rechazos recientes (motivo, símbolo, lado, estrategia) | visible | visible |
| Racha de pérdidas actual y contador de cruces de drawdown | oculto | visible |
| Universo vigente, cambios de universo y veredictos de backtest por estrategia (histórico de la Fase 2, no es la medición en vivo) | visible | visible |
| Push de apertura | completo (sin resultados) | igual |
| Push de cierre | solo "posición cerrada" con símbolo, lado, estrategia y horas | agrega motivo, precio de salida y PnL neto |
| Push de riesgo | estado y hasta cuándo, sin cifras | igual |

`closed_at` se muestra con el reveal cerrado porque pediste "horas" en la vista; sugiere cuánto duró la
operación pero no su resultado. Los **detalles** del registro del brazo y de los rechazos de riesgo pasan por
una lista blanca de claves: `MARGIN_UNAVAILABLE` guarda el `equity` (que sí es un resultado) y nunca sale.

## Diseño

### 5.A API de lectura (todas bajo `/api/`, con login, router con `require_auth`)

- `GET /api/account`: resumen (capital inicial, abiertas, cerradas, margen en uso), posiciones abiertas y
  cerradas con la lista blanca de arriba. Con el reveal abierto agrega `results` (saldo realizado, PnL
  realizado y flotante con los marks del monitor, equity, ganadas, perdidas, profit factor); cerrado, `results`
  es `null` y trae el mensaje de "ocultos hasta el registro oficial".
- `GET /api/arm`: estado del brazo (interruptor, activo, desde cuándo), conteos totales y de hoy por
  resultado, omitidas por motivo, y las últimas decisiones con su `trade_id`.
- `GET /api/risk`: pausa, kill switch, breaker, stop por drawdown (modo y umbral), pérdida diaria, cupos
  (abiertas/máximo, misma dirección), último ciclo del scheduler, `measurement_stopped`, últimos rechazos de
  riesgo y sus conteos de hoy; `numbers` solo con el reveal abierto.
- `GET /api/universe`: último snapshot (30 candidatos: ranking, capitalización, exclusión, perpetuo en
  Bitunix, incluido), edad, si está obsoleto (`UNIVERSE_STALENESS_HOURS`), último error de refresco, mark
  más reciente que ya tiene el monitor por símbolo y los últimos cambios del universo (entran/salen), más la
  atribución de CoinGecko.
- `GET /api/backtest/verdicts`: veredicto por estrategia de `backtest_verdicts` (descartada o no, motivos con
  los números redondeados, PF OOS, PF con estrés, % de símbolos y folds positivos, drawdown OOS, concentración,
  combinaciones y operaciones) y si la estrategia es elegible hoy para la cuenta (`REAL_ACCOUNT_ELIGIBLE_STRATEGIES`).
  Es un histórico: no pasa por la puerta de resultados.

### 5.B Controles (escritura): `POST /api/control/{pause|resume|kill}`

- **Misma semántica que Telegram** (código compartido en `app/trading/control_actions.py`, que Telegram también
  usa): pausar = scheduler pausado; kill = kill switch + scheduler pausado; reanudar = levanta pausa Y kill switch
  (no levanta el stop por drawdown, y la respuesta lo avisa). Ningún cierre manual de posiciones ni edición de
  parámetros: no lo pediste y tocarían la cuenta paper.
- **Misma advertencia**: el texto central `STOPS_MEASUREMENT` es la constante de Telegram, movida al módulo
  compartido; el dashboard la muestra completa en el diálogo y una prueba verifica que coinciden.
- **Confirmación explícita en dos capas**: el diálogo exige escribir la palabra (`PAUSAR`, `KILL`, `REANUDAR`) y
  el servidor rechaza (422) cualquier petición cuyo cuerpo no traiga exactamente esa palabra.
- **Seguridad**: login obligatorio (cookie httpOnly, SameSite=Strict); si la petición trae `Origin`, debe ser el
  mismo host (403 si no); solo `application/json`; un candado serializa las acciones; `DASHBOARD_CONTROLS_ENABLED`
  (por defecto verdadero) permite apagar los POST sin tocar código (403). El servidor sigue escuchando solo en
  127.0.0.1.
- **Registro**: tabla nueva `control_audit` (fecha, canal DASHBOARD/TELEGRAM, quién, acción y estado antes/después de
  pausa y kill). Quien actúa por el dashboard queda como el usuario autenticado; por Telegram, como "telegram" (sin
  datos del chat, como ya hacía). Cada petición válida se registra, también si no cambió nada. `GET /api/control/audit`
  muestra las últimas acciones y `GET /api/control/info` las advertencias y palabras de confirmación.

### 5.C Notificaciones push de Telegram (`app/telegram/push.py`)

- **Sin ganchos en el núcleo**: el notificador sondea la base de solo lectura cada `TELEGRAM_PUSH_POLL_SECONDS`
  (10 s) y compara con el último estado notificado. Así nada de `PaperBackend`, `PositionMonitor`, el brazo ni el
  motor de riesgo sabe que existe; una falla suya no puede alcanzarlos. Latencia máxima: el periodo de sondeo.
- **Eventos**: posición abierta y posición cerrada de la cuenta paper (diferencia de estado de las posiciones:
  robusto a cierres retroactivos de la reconciliación); cambio del universo (entran/salen); alertas de riesgo
  (kill switch, pausa, breaker, stop por drawdown, pérdida diaria) en ambos sentidos, con el origen
  (dashboard/Telegram y quién) cuando hay un registro de control reciente.
- **Estado persistido** (`system_state.push_state`, un JSON): al primer arranque se toma como línea base el
  estado actual **sin notificar el historial** (no inunda el chat); después se avanza evento por evento, solo tras un
  envío correcto o un error permanente, para no repetir ni perder mensajes.
- **Envío**: texto plano, primera línea "MODO PAPER TRADING"; 1,2 s entre mensajes, `retry_after` respetado,
  reintentos con espera creciente para errores transitorios (red, 5xx, 429) y descarte registrado para los permanentes
  (400/401/403/404); máximo 10 mensajes por ciclo (el resto se resume en uno). Un mensaje de arranque confirma el canal.
- **Reveal**: el texto de cierre y de riesgo se arma según la puerta (falla cerrada); sin ella, solo "posición
  cerrada".
- **Aislamiento**: tarea propia (`run_isolated`), construirlo no puede lanzar, interruptor `TELEGRAM_PUSH_ENABLED`
  apagado por defecto (`start_bot.ps1` lo enciende solo con el puerto 8000; `run_dev_server.py` lo fuerza a falso),
  necesita token y chat, y solo escribe la clave `push_state`. `/health.telegram_push` informa enviados, fallos y
  último error (solo el tipo, nunca el token).

### 5.D Dashboard (frontend)

- Pestañas: **Resumen** (todo lo actual, sin cambios), **Cuenta paper**, **Brazo ejecutado**, **Riesgo y controles**,
  **Universo y backtest**. Las pestañas nuevas solo piden sus datos al abrirse; si el backend aún no las tiene (antes
  del reinicio) muestran "no disponible" en vez de romper el resto.
- Un banner permanente "LA MEDICIÓN ESTÁ DETENIDA" cuando `/health` dice pausa o kill switch.
- Diálogo de confirmación accesible (`role="dialog"`, foco en el campo, Escape cancela) para los tres controles.
- Atribución de CoinGecko enlazada: junto a la tabla del universo y en el pie.
- Sin gráficos de equity (ver "Fuera de alcance").

## Fuera de alcance (y por qué)

- **Cerrar posición, cerrar todo y editar parámetros de riesgo desde el dashboard** (SPEC): no los pediste; modifican la
  cuenta paper o los límites durante la medición. Se pueden agregar después con el mismo patrón de confirmación y auditoría.
- **Curva de equity, winrate, drawdown y PnL agrupado por día/semana/mes/año**: son resultados; con el reveal cerrado no se
  pueden mostrar y no habría datos reales para probarlos. Con el reveal abierto, la vista de cuenta ya muestra el PnL de cada
  operación y el resumen; los gráficos quedan para después del veredicto.
- **Tiempo real por WebSocket**: se usa sondeo (cada 45 s, como el resto del dashboard).
- **PWA/APK (Capacitor)**: opcional en el SPEC, no pedido.
- **Vista de "avance hacia los criterios de paso a dinero real"**: ya existe como `scripts/real_money_report.py`, que depende de
  resultados.

## Pruebas

Matriz de exposición (con operaciones centinela cuyo PnL, salida y motivo no pueden aparecer en ningún JSON ni mensaje mientras el
reveal esté cerrado, ni siquiera dentro de los detalles); puerta que falla cerrada; controles (confirmación exacta, origen,
interruptor, auditoría, mismo efecto que Telegram, el texto de la advertencia coincide); notificador (línea base sin inundar,
cada evento, cierre retroactivo, orden y no repetición tras un fallo parcial, errores permanentes/transitorios/429, ritmo, que solo
escriba `push_state`, aislamiento con un Telegram hostil frente a scheduler/etiquetado/monitor/brazo, AST: ningún módulo del núcleo
lo importa); migración aditiva sobre una copia de la base real; frontend (Vitest: pestañas, degradación, diálogo de confirmación,
atribución, falla cerrada); humo de punta a punta con una copia real y arranque en el 8001 con el notificador apagado.

## Subfases y commits

| Subfase | Contenido |
|---|---|
| 5.1 | Ajustes (`TELEGRAM_PUSH_ENABLED`, `TELEGRAM_PUSH_POLL_SECONDS`, `DASHBOARD_CONTROLS_ENABLED`), `start_bot.ps1`, `run_dev_server.py`, tabla `control_audit`, puerta compartida `app/trading/reveal.py`, `app/trading/control_actions.py` y Telegram usándolo |
| 5.2 | API de lectura: cuenta, brazo, riesgo, universo y veredictos de backtest |
| 5.3 | API de controles con confirmación, origen, interruptor y auditoría |
| 5.4 | Notificador push de Telegram, cableado aislado y `/health.telegram_push` |
| 5.5 | Frontend: pestañas, vistas, controles con diálogo y atribución |
| 5.6 | Documentación, verificación completa, humo con copia real y `git push origin master` |

## Qué requiere reinicio

Todo lo de backend (rutas nuevas, tabla `control_audit`, notificador, cambios de Telegram y de `start_bot.ps1`) se carga solo al
reiniciar el bot real. El frontend se compila a `frontend/dist`, que el 8000 sirve en caliente: apenas se compile, las pestañas nuevas
aparecen pero dicen "no disponible" hasta el reinicio. Un solo reinicio en los minutos :10 a :40 de una hora UTC que no sea de cierre
de 4 h. La lista definitiva está en "Implementación".

## Implementación (2026-10-18)

Seis subfases, todas en `master`: `4aa542d` (este plan), `efa9e94` (5.1), `aab88f9` (5.2), `cebf301` (5.3), `c5c5111` (5.4),
`a456961` (5.5) y el commit de esta documentación (5.6). Se hizo todo el alcance del plan, sin recortes. **El bot real (8000) sigue
sin esta fase hasta que lo reinicies tú; no lo toqué.**

### Qué se construyó

| Parte | Dónde |
|---|---|
| Ajustes | `TELEGRAM_PUSH_ENABLED` (falso por defecto), `TELEGRAM_PUSH_POLL_SECONDS` (10), `DASHBOARD_CONTROLS_ENABLED` (verdadero por defecto) en `app/config.py` y `.env.example`; `start_bot.ps1` enciende el push **solo con el puerto 8000** (`$telegramPush`) y `run_dev_server.py` lo fuerza a falso |
| Persistencia | tabla aditiva `control_audit` (+ índice) y `control_audit_repo`; la migración sobre una copia fresca de la base real agrega solo esa tabla |
| Puerta de resultados | `app/trading/reveal.py::reveal_allowed(db)`: compuerta abierta **y** registro oficial; ante cualquier error responde "cerrada" |
| Controles | `app/trading/control_actions.py` (pausar, reanudar, kill; lo usan Telegram y el dashboard), candado por base, auditoría de quién/cuándo/antes/después |
| API de lectura | `GET /api/account`, `/api/arm`, `/api/risk`, `/api/universe`, `/api/backtest/verdicts` (todas con login) |
| API de controles | `GET /api/control/info`, `GET /api/control/audit`, `POST /api/control/{pause\|resume\|kill}` |
| Push de Telegram | `app/telegram/push.py` (sondeo, línea base, eventos, envío con ritmo y reintentos) y `push_runtime.py` (construcción aislada y arranque), `retry_after` en `client.py`, bloque `telegram_push` en `/health` |
| Dashboard | pestañas Resumen · Cuenta paper · Brazo ejecutado · Riesgo y controles · Universo y backtest; diálogo de confirmación; banner de medición detenida; atribución de CoinGecko junto a los datos y en el pie |

Las únicas rutas de escritura de toda la API son login, logout y los tres POST de control: una prueba lo afirma leyendo
`app.openapi()["paths"]` (ver abajo por qué ahí).

### Ajustes durante la implementación (respecto al plan)

1. **Las defensas del POST son dependencias de la ruta**, no código dentro del endpoint: FastAPI valida el cuerpo *antes* de ejecutar el
   endpoint, así que una comprobación interna llegaría tarde (un `text/plain` habría dado 422 en vez de 415). Orden: controles habilitados
   (403) → mismo origen (403) → solo JSON (415) → palabra exacta (422). El origen se compara por **esquema y host** (un `https://` contra
   un host `http` se rechaza) y se respeta `Sec-Fetch-Site` (`cross-site` y `same-site` se rechazan).
2. **Lista blanca clave por clave** en los serializadores en vez de "quitar lo prohibido": una columna nueva de `trades` no puede salir por
   olvido. Los detalles del brazo pasan por `ARM_DETAIL_KEYS` y los rechazos de riesgo salen sin detalles.
3. **El PnL ni siquiera se consulta** con la puerta cerrada (una prueba lo verifica), no solo se oculta al serializar.
4. **El notificador avanza su estado evento por evento**, solo tras un envío correcto o un error permanente: un fallo transitorio a mitad
   de un ciclo no repite lo ya enviado ni pierde lo pendiente. Un estado guardado dañado se reemplaza por una línea base nueva (sin
   notificar historial).
5. **Desempate `id DESC`** en las lecturas ordenadas por fecha (`get_rejections`, `get_closed_trades`): en Windows el reloj tiene ~15 ms de
   resolución y dos filas pueden compartir marca de tiempo.
6. **El frontend pide los datos de cada pestaña solo al abrirla** y, ante un 404 (backend sin reiniciar), dice "no disponible todavía". El
   banner de pausa/kill lee `/health.control`, que existe desde la subfase 3.7 (lectura de solo lectura al 8000 el 2026-10-18: el bloque
   está), así que **el banner ya funciona con el bot actual sin reiniciar**; un backend anterior a la 3.7 no lo manda y entonces no hay
   banner (nunca uno falso).
7. **Falla cerrado también en el navegador**: la cuenta y el riesgo muestran cifras de resultado solo si el JSON trae `reveal_allowed: true`
   **y** los campos; un backend que mande campos con la bandera falsa no los muestra.
8. La prueba de migración de la Fase 4 sobre la base real tolera las tablas aditivas de fases posteriores.

### Verificación (2026-10-18)

- **Python**: `pytest` **1552 passed, 4 skipped** (antes 1359; las 193 de diferencia son las pruebas nuevas de
  `test_phase5_{base,read_api,control_api,push}.py`); `ruff check .` limpio.
- **Frontend**: Vitest **106/106** (antes 29), `tsc --noEmit` y `vite build` limpios (237 kB, 71 kB comprimido).
- **Humo con copias frescas de la base real** (backup en solo lectura, ruta distinta de `settings.database_path` verificada con `assert`):
  `connect()` agrega solo `control_audit` (las otras 27 tablas con el mismo número de filas), `integrity_check` ok; las cinco rutas de
  lectura responden 200 con la compuerta **real** cerrada y ningún JSON contiene campos ni palabras de resultado; universo de 30
  candidatos (10 incluidos, 3 cambios históricos), 6 veredictos de backtest (3 estrategias elegibles); brazo con 2 omitidas
  (`RISK_STRATEGY_NOT_ELIGIBLE`), 0 abiertas. Los controles contra el esquema real: palabra incorrecta → 422 sin cambios, origen ajeno
  → 403, pausar y reanudar quedan auditados con `admin`. Con **operaciones sintéticas** (una abierta y una cerrada, con valores
  centinela de PnL, salida y motivo) en la copia, esos valores no aparecen en ninguna ruta y la cerrada solo trae las claves de la
  lista blanca más `closed_at`.
- **Arranque real en el 8001** (`scripts/run_dev_server.py` sobre una copia, red real de solo lectura a Bitunix, push forzado a apagado):
  "Application startup complete"; `/health` trae `control`, `news` y `telegram_push` con `enabled: false`, y `frozen_hash_ok: true`; las
  8 rutas nuevas figuran en OpenAPI (7 de lectura y la plantilla del POST); las 7 de lectura y el POST de pausa responden 401 sin sesión, y las únicas rutas de escritura son login,
  logout y `/api/control/{action}`; el frontend nuevo se sirve. (`status: stale` hasta el primer ciclo del scheduler, como siempre en una copia.)
  Lo detuve al terminar; el 8000 siguió vivo y en `ok` todo el tiempo.
- **Humo del notificador** (copia real y cliente falso, cero red): línea base sin avisar el historial (3 cambios de universo antiguos
  no se anuncian); apertura, cierre y kill sintéticos producen un mensaje cada uno y el cierre sale como "posición cerrada" sin PnL ni motivo
  porque la puerta real está cerrada.
- **Mutaciones de prueba (backend)**: quitar el candado de los controles, dejar salir el PnL en el serializador de cuenta y dejar salir el
  resultado en el texto de cierre del push hacen fallar las pruebas correspondientes. **No pude hacerla en el frontend**: el intento de
  forzar `reveal = true` en el componente fue bloqueado por el clasificador de permisos, lo revertí al instante (verificado) y no lo
  reintenté. Esa defensa está cubierta por las pruebas "falla cerrado" de `AccountTab` y `RiskTab`, pero sin la comprobación por mutación.

### Qué no pude verificar

- **Que Telegram entregue los mensajes en tu chat**: necesita el token real (`.env`, que no leo). El notificador manda un mensaje de
  arranque ~15 s después de iniciar para que lo compruebes; si no llega, `/health.telegram_push.last_error` da el tipo de error.
- **El aspecto visual real** del dashboard en un navegador: lo probé con Vitest/jsdom, `tsc` y `vite build`, no lo abrí en pantalla.
- **El comportamiento con la compuerta abierta sobre datos reales**: no existe todavía (5/300 y 6/300); está cubierto con pruebas
  sintéticas que simulan la puerta abierta.

### Riesgos residuales (declarados)

- `closed_at` junto con los niveles SL/TP/entrada se muestra aun con el reveal cerrado (lo pediste: "horas"). Da la duración, no el
  resultado, pero con muchas operaciones podría inferirse algo de ellas.
- Las alertas de riesgo y el estado del breaker son datos operativos: un breaker activo implica que hubo una racha de pérdidas aunque
  el mensaje no traiga cifras.
- Los logs de INFO siguen mostrando motivos de cierre y PnL (`Monitor cerro ... motivo=`, `Posicion cerrada ... pnl_neto=`); ya ocurría
  antes de esta fase y no se tocó.
- Con `DASHBOARD_CONTROLS_ENABLED=true` (valor por defecto) quien tenga sesión puede pausar la medición. Mitigación: login, mismo origen,
  JSON, palabra exacta, auditoría, y el servidor escucha solo en 127.0.0.1. Para desactivarlo: `DASHBOARD_CONTROLS_ENABLED=false`.
- Latencia de las notificaciones: hasta el periodo de sondeo (10 s); con el bot apagado no hay avisos, y al volver a iniciar se avisa lo
  ocurrido mientras estuvo caído (hay prueba).

### Qué requiere reinicio (lista definitiva)

**Todo el backend**: las rutas nuevas (`/api/account`, `/api/arm`, `/api/risk`, `/api/universe`, `/api/backtest/verdicts`,
`/api/control/*`), la tabla `control_audit`, el notificador push con `TELEGRAM_PUSH_ENABLED` (lo exporta `start_bot.ps1` con el puerto
8000), los cambios de los comandos de Telegram (`/pause`, `/resume` y `/kill` ahora auditan) y el bloque `/health.telegram_push`.
**El frontend no necesita reinicio**: `frontend/dist` ya está compilado y el 8000 lo sirve en caliente. Hasta el reinicio las pestañas
nuevas dicen "no disponible todavía" (el resumen sigue igual); el banner de pausa/kill sí funciona desde ya, porque `/health.control`
existe desde la 3.7. Un solo reinicio con `stop_bot.ps1` + `start_bot.ps1`, en los minutos **:10 a :40** de una hora UTC que **no** sea de
cierre de 4 h (01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19, 21, 22, 23 UTC; en hora de Chile, UTC-3: 10, 11, 12, 14, 15, 16,
18, 19, 20 h). Las noticias de la Fase 4 ya están activas en el bot real (`/health.news` presente el 2026-10-18), así que este reinicio
es solo para la Fase 5.

Verificación después del reinicio (solo lectura):

1. `GET /health` (con `Invoke-RestMethod`, puerto 8000): `status: ok`, `llm_labeling.frozen_hash_ok: true`, `control.paused: false`,
   `telegram_push.enabled: true` (bloque nuevo: prueba de que corre el código de la Fase 5) y, ~15 s después, `telegram_push.active: true`.
2. `logs/minerva.log`: "Notificaciones push de Telegram iniciadas" y, tras ~15 s, "Notificaciones push de Telegram ACTIVAS (sondeo
   cada 10 s)"; ningún "NO iniciadas".
3. En tu chat de Telegram: el mensaje "MODO PAPER TRADING / Notificaciones de Minerva ACTIVAS ..." (confirma el canal). Si no llega,
   mira `telegram_push.last_error`.
4. En el dashboard: cada pestaña ya no dice "no disponible todavía"; "Riesgo y controles" muestra los tres botones.
5. La medición sigue: tras el siguiente :00 suben las filas de medición (`python scripts/measurement_status.py --db <copia>`) y el hash
   congelado no cambia (esta fase no toca nada de eso).

Criterios de reversa: si el push da problemas, `$telegramPush = "false"` en `start_bot.ps1` y reiniciar (misma ventana horaria); si
quieres los botones fuera, `DASHBOARD_CONTROLS_ENABLED=false`. **No uses `/kill` ni pausa como "freno" del notificador**: detienen la
medición.

### Fuera de alcance (confirmado)

Cerrar posiciones y editar parámetros de riesgo desde el dashboard, curvas de equity y PnL agrupado, WebSocket y PWA siguen fuera, por las
razones del apartado "Fuera de alcance" de arriba.
