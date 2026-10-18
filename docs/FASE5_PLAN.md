# Fase 5: dashboard completo y Telegram enriquecido (plan, 2026-10-18)

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
de 4 h. El detalle definitivo se confirma al terminar.
