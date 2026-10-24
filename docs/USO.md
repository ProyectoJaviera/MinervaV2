# Uso diario

Manual para quien opera Minerva ya instalado: qué mirar cada día, cómo usar el dashboard y Telegram, cómo arrancar,
detener y reiniciar sin estropear la medición, cómo respaldar, actualizar y mudar el bot, y qué hacer cuando la
medición llegue a su punto de análisis. Si todavía no lo instalaste, empieza por la
[guía de instalación](GUIA_INSTALACION.md).

- **Minerva es 100 % paper trading, experimental y no es asesoría financiera.** Nada de lo que hay aquí envía órdenes
  reales.
- Los comandos asumen que estás en la **raíz del repositorio** (`<carpeta del repo>`) y que `python` es el de tu
  entorno virtual: `.\.venv\Scripts\python.exe` en Windows, `.venv/bin/python` en Linux y macOS. Se escriben con
  `/` en las rutas de Python (funciona también en Windows) y con `\` en los scripts de PowerShell.
- **Docker** es la forma recomendada de correr el bot. Con Docker, **la base viva está en un volumen**, no en `data\`:
  lo que *lee* la base (respaldos, estado de la medición, informes) corre en tu equipo sobre una **copia** que saca
  `scripts/docker_db.py`; lo que *escribe* en ella (levantar el stop por drawdown, restaurar) corre dentro del
  contenedor con `docker compose exec minerva python …` o `docker compose run`. Donde cambia con la instalación
  nativa de Windows, se indica.

## Las reglas que no se rompen

| Regla | Por qué |
|---|---|
| **Un solo proceso por base de datos.** Nunca corras Docker y `start_bot.ps1` a la vez, ni dos instancias nativas | Dos procesos escribiendo la misma base la corrompen; con el mismo token de Telegram solo uno recibe los comandos; y gastarías la clave de Anthropic dos veces |
| **Nunca copies el archivo `.db` a mano.** Usa `scripts/docker_db.py` (Docker) o `scripts/backup_db.py` (nativo) | La base va en modo WAL: un `.db` copiado sin su `-wal` puede quedar inconsistente |
| **Medición ciega.** Hasta que existan 300 conglomerados efectivos por lado **y** un registro oficial del veredicto, no mires PnL, tasas de acierto ni la comparación APROBADA/RECHAZADA | Mirar y decidir cuándo parar sesga el veredicto. Las herramientas lo impiden, pero un ojo humano sobre la base cruda también cuenta |
| **No toques el prompt congelado ni el modelo.** No edites `app/llm/prompts.py` y no cambies `ANTHROPIC_SONNET_MODEL` | El hash del prompt se comprueba en cada ciclo (si no coincide, el etiquetado se detiene solo). **El modelo no tiene esa vigilancia**: cambiarlo contaminaría la medición sin aviso |
| **Las credenciales viven solo en `.env`.** Nunca en el repositorio, en la imagen, en un chat, en un log ni en una captura | Un secreto filtrado hay que revocarlo ([Seguridad](SEGURIDAD.md)) |
| **Reinicia solo en la ventana segura** ([Reinicios seguros](#reinicios-seguros)) | Un reinicio en mal momento corta etiquetados en vuelo y deja sombras sin etiqueta |
| **`/pause` y `/kill` detienen la medición.** Úsalos solo si algo anda mal | Con el scheduler pausado no hay señales, sombras ni etiquetado; cada hora pausada es medición perdida |

## Qué es "sano"

`GET /health` (abre <http://127.0.0.1:8000/health>; no pide sesión) resume el estado. Cada 60 segundos el scheduler
evalúa el cierre de vela más reciente.

| Campo de `/health` | Señal sana |
|---|---|
| `status` | `ok`. Pasa a `stale` si no hubo un ciclo completo en 90 minutos (también durante una pausa deliberada) |
| `control.paused`, `control.kill_switch_active` | Ambos `false` (si no, la medición está detenida a propósito) |
| `llm_labeling.enabled` | `true` en el bot real |
| `llm_labeling.frozen_hash_ok` | **`true`**. Si es `false`, el etiquetado está detenido aunque el proceso responda: es la falla silenciosa más cara |
| `llm_labeling.cost_today_usd` | Por debajo de `LLM_DAILY_BUDGET_USD`; `budget_exceeded_today` en 0 |
| `llm_labeling.failed_today` | Bajo (algún fallo aislado de red es normal) |
| `paper_execution.active` | `true` en el bot real (brazo ejecutado armado) |
| `telegram_push`, `news` | Informativos: su caída no afecta a la medición |
| `log_dropped_count` | 0 o casi 0 (descartes de log por cola llena) |

El ritmo de la medición es lento y depende de que el bot **esté encendido**: el tiempo apagado no se recupera. En la
medición del autor (octubre de 2026) hubo ≈ 1,25 conglomerados por día de calendario y lado frente a ≈ 5,27 por día
en marcha, con un 23,6 % de disponibilidad. Cada hora de equipo apagado alarga el plazo.

## Rutina

### Cada día (≈ 2 minutos)

1. **Lee el resumen diario de Telegram** (si registraste la tarea; ver [Tareas programadas](#tareas-programadas)).
   Debe decir: bot OK, `frozen_hash_ok`, etiquetado activo, etiquetadas y fallidas de las últimas 24 h, costo del LLM, N
   efectivo por lado (x/300) y si el *gate* está abierto. Nunca trae PnL.
2. **Si no llegó:** el equipo estaba apagado a esa hora o el bot no responde. Mira <http://127.0.0.1:8000/health> o
   corre `python scripts/check_health.py` (códigos de salida: 0 OK, 1 STALE, 2 SIN_RESPUESTA).
3. **Si recibiste una alerta de caída**, ve a [Solución de problemas](SOLUCION_PROBLEMAS.md).
4. En el dashboard (<http://127.0.0.1:8000/>), la pestaña *Resumen* muestra salud, costo y avance; *Riesgo y
   controles* muestra el *circuit breaker*, el kill switch y la pausa.

### Cada semana (≈ 10 minutos)

1. **Respaldo** ([Respaldos](#respaldos)) y copia del último **fuera del equipo** (otro disco o nube privada).
2. **Espacio en disco:** la base crece con el tiempo (en la instalación del autor rondaba los 160 MB tras unas semanas) y `logs/minerva.log` rota solo
   (5 archivos de 10 MB). Con Docker, los volúmenes viven dentro del disco virtual de Docker: `docker system df -v`
   los mide. `logs/medicion_out.log` (arranque nativo de Windows) no rota.
3. **Avance por conteos**, sobre una copia (nunca sobre la base de trabajo):
   - Docker: `python scripts/docker_db.py run measurement_status.py` (saca una copia fresca, la mide y la borra).
   - Nativo: `python scripts/measurement_status.py --db data/backups/<el respaldo más reciente>.db`.
4. **Un solo modelo y el prompt congelado en la medición** (solo conteos y fechas), sobre la misma copia:
   `python scripts/docker_db.py run measurement_identity.py` (Docker) o
   `python scripts/measurement_identity.py --db data/backups/<el respaldo más reciente>.db` (nativo). Sale con código 0 si
   hay una única combinación (modelo, prompt congelado, *features*) y con 1 si detecta una anomalía, o si el modelo
   configurado en el `.env` ya no es el que midió (un cambio que se haría efectivo al reiniciar).
5. **Árbol del repositorio limpio:** `git status` sin cambios en `app/` y `git log -1` en el commit esperado. El proceso
   en marcha ya cargó su código, pero el **próximo reinicio** cargaría lo que haya en disco: un cambio accidental en
   `app/llm/` dejaría `frozen_hash_ok` en `false` justo después de reiniciar.

### Qué no mirar (mientras el *gate* esté cerrado)

PnL de sombras, tasa de acierto de aprobadas y rechazadas, lecciones activas, saldo, equity o el motivo de cierre de las
posiciones. Si abres una copia de la base, imprime **solo conteos y estructura**. Los logs de nivel INFO ya dejan pasar el
motivo de cierre de las sombras (`Monitor cerro … motivo=`) y el PnL neto de las posiciones (`Posicion cerrada …
pnl_neto=`): es una limitación conocida; evita abrir los logs sin necesidad.

## El dashboard

Se abre en <http://127.0.0.1:8000/> (solo desde el propio equipo) y pide el usuario y la contraseña de tu `.env`. La
sesión dura 24 horas. Arriba muestra tu sesión y *Cerrar sesión*; si el scheduler está en pausa o el kill switch
activo, aparece un **aviso rojo en todas las pestañas** ("LA MEDICION ESTA DETENIDA…"). Al pie, la atribución
"Data provided by CoinGecko". Los datos se consultan periódicamente; no hay tiempo real por WebSocket. Tiene cinco
pestañas:

| Pestaña | Qué muestra |
|---|---|
| **Resumen** | *Salud del bot* (estado, etiquetado en vivo, hash congelado, último ciclo, etiquetadas y fallidas hoy, notificaciones de Telegram). *Costo del LLM* (hoy, acumulado de la medición, promedio por fila, veces que se agotó el presupuesto). *Avance de la medición*: barras de APROBADA y RECHAZADA hacia los 300 conglomerados, la tasa de aprobación frente a la banda del 15–85 %, filas medidas, JSON reparado, omitidas sin fila, estado del *gate*, causas de las filas no OK (timeout, error HTTP, JSON inválido, presupuesto agotado) y los días estimados para llegar a 300 *por calendario* y *en marcha*. Además: noticias y memoria de lecciones, decisiones recientes del LLM (con su razonamiento y costo) y sombras recientes |
| **Cuenta paper** | Capital inicial, posiciones abiertas frente al máximo (3 por defecto), operaciones cerradas y margen en uso. Tablas de posiciones abiertas (entrada, SL, TP, liquidación, margen) y de las 100 cerradas más recientes. **Mientras la puerta de resultados esté cerrada, no se muestran precio de salida, PnL, motivo de cierre ni saldo** |
| **Brazo ejecutado** | Estado del brazo (activo, armado desde cuándo), abiertas y omitidas hoy y en total, margen por operación, retraso máximo de la decisión, estrategias elegibles, las **omitidas por motivo** (con su explicación) y las decisiones recientes del brazo. Nunca lleva PnL |
| **Riesgo y controles** | Estado del scheduler, kill switch, *circuit breaker*, stop por drawdown, pérdida diaria, posiciones abiertas por dirección, último ciclo y límites; por qué están bloqueadas las entradas nuevas; los rechazos recientes del motor de riesgo; los **botones de control** (Pausar medición, Reanudar, Kill switch) y el registro de acciones de control. La racha de pérdidas y los cruces de drawdown quedan ocultos hasta el veredicto |
| **Universo y backtest** | El universo vigente (candidatos de CoinGecko, cuáles se incluyen y por qué se excluyeron los demás, antigüedad y cambios recientes; un aviso rojo si está obsoleto) y los **veredictos del backtest por estrategia**. En una instalación nueva esa tabla de veredictos está vacía: los resultados del backtest de la Fase 2 se calcularon aparte y no vienen con el repositorio |

**Los controles** piden confirmación explícita: se abre un diálogo con la advertencia y hay que **escribir la palabra
exacta** (`PAUSAR`, `REANUDAR` o `KILL`). Pausar o activar el kill switch **detiene la medición**. No abren ni cierran
posiciones. Si pones `DASHBOARD_CONTROLS_ENABLED=false`, los botones quedan desactivados.

## Telegram

Si configuraste el bot (ver la [guía](GUIA_INSTALACION.md#32-bot-de-telegram-opcional-token-y-chat_id)), solo responde
a tu chat privado. Todos los mensajes empiezan con "MODO PAPER TRADING".

| Comando | Qué hace |
|---|---|
| `/status` | Capital inicial; posiciones abiertas (x/3); si el kill switch o la pausa están activos; *circuit breaker* y stop por drawdown; gasto del LLM hoy frente al tope; antigüedad del último ciclo (con `STALE` si pasó de 90 min); etiquetado en vivo y `hash congelado OK`; estado del brazo ejecutado; avance de la medición |
| `/positions` | Posiciones abiertas de la cuenta paper: símbolo, lado, cantidad, entrada, SL, TP y estrategia |
| `/balance` | Capital inicial y posiciones abiertas. El balance, el equity y el PnL aparecen como "ocultos hasta el registro oficial del veredicto" |
| `/metrics` | Avance de la medición: N efectivo por lado y lo que falta, ritmo, tasa de aprobación frente a la banda, filas OK y no OK por causa, omitidas, costo acumulado y estado del *gate*; y el número de operaciones cerradas de la cuenta (el factor de beneficio y la esperanza, ocultos) |
| `/pause` | **Detiene la medición**: pausa el scheduler (sin señales, sombras ni etiquetado). Los cierres de posiciones siguen |
| `/resume` | Quita la pausa y el kill switch. Si el stop por drawdown sigue activo, avisa: se levanta aparte |
| `/kill` | Activa el kill switch (no se abren entradas) **y** pausa el scheduler: también detiene la medición |
| cualquier otro (`/start`, `/help`…) | Lista de comandos |

**Confirmación de dos pasos:** `/pause`, `/resume` y `/kill` primero advierten y piden confirmar. Envía el mismo
comando seguido de `SI` **dentro de 60 segundos** (por ejemplo `/pause` y luego `/pause SI`). Una solicitud nueva
cancela la anterior y se descarta si el bot se reinicia. Las acciones quedan auditadas, igual que las del dashboard.

**Avisos automáticos** (si `TELEGRAM_PUSH_ENABLED` está encendido): posición abierta o cerrada (el cierre dice solo
"posición cerrada" mientras los resultados estén ocultos), cambios del universo, y cambios del estado de riesgo (kill
switch, pausa, *circuit breaker*, cruce del stop por drawdown, pérdida diaria), más un mensaje al arrancar. El
notificador compara estados cada `TELEGRAM_PUSH_POLL_SECONDS`; si pausas y reanudas entre dos consultas, el aviso no
verá el cambio (la auditoría sí). **El resumen diario** y la **alerta de caída** son tareas aparte
([Tareas programadas](#tareas-programadas)).

## Arrancar y detener

### Docker (recomendado)

Requisitos: Docker Desktop (o el servicio de Docker) **abierto** y el `.env` junto a `docker-compose.yml`, **como
archivo**.

```bash
docker compose up -d                 # la primera vez construye la imagen (~1 min) y arranca el bot
docker compose ps                    # STATUS pasa a "healthy" en ~1 minuto
docker compose logs -f minerva       # evítalo si quieres mantener la medición ciega
docker compose stop                  # parada ordenada: SIGTERM y hasta 30 s para cerrar
docker compose restart minerva       # reinicio ordenado (solo en la ventana segura)
```

- **Un cambio del `.env` o del compose se aplica solo al crear el contenedor:** tras editar uno,
  `docker compose up -d --force-recreate` (los volúmenes se conservan; un simple `restart` no garantiza que el
  contenedor vea un `.env` reemplazado).
- `restart: unless-stopped` es el valor por defecto: el contenedor vuelve solo tras reiniciar Docker o el equipo, salvo
  que lo detengas tú con `docker compose stop`. En Windows, **Docker Desktop debe iniciar con tu sesión** para que eso
  ocurra ([cómo](MUDANZA_DOCKER.md#dejar-docker-desktop-iniciando-con-windows)).
- Docker **no reinicia** un contenedor "unhealthy" (proceso vivo pero colgado): lo avisa la alerta externa de caída.
- El puerto se publica **solo en `127.0.0.1`**. Variables opcionales del compose: `MINERVA_PORT`, `MINERVA_REAL_BOT`,
  `MINERVA_RESTART` y `MINERVA_ENV_FILE` ([Configuración](CONFIGURACION.md#variables-de-docker-compose)).
- **Redes con inspección TLS corporativa:** deja el certificado raíz de esa red como `docker/certs/<nombre>.crt` antes de
  `docker compose build` (ver `docker/certs/README.md`). Los `.crt` no se suben al repositorio.

### Nativo en Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
```

`start_bot.ps1` abre el bot en el puerto 8000 sin consola, sobre `data\minerva_estabilidad.db`, con las variables fijas
del "bot real" **con independencia de lo que diga el `.env`**. Se niega a arrancar si el puerto ya está ocupado o si
existe la marca `data\MIGRADO_A_DOCKER.txt`, y espera hasta 180 s a que `/health` responda. `stop_bot.ps1` termina solo
el proceso que escucha el puerto 8000 y sus lanzadores. **Es un corte duro** (Windows no da una señal de apagado
cooperativo a un proceso sin consola): SQLite en modo WAL lo tolera, pero un etiquetado en vuelo se pierde; por eso se
reinicia en la ventana segura. La parada de Docker, en cambio, es ordenada.

### Nunca los dos a la vez

El puerto 8000 publicado por Docker choca **a propósito** con el del bot nativo: si lo levantas por error con el bot
encendido, Docker falla al enlazar el puerto *antes* de arrancar el proceso. Si cambias `MINERVA_PORT` para evitarlo,
tendrás dos bots con el mismo token y la misma clave: no lo hagas salvo para pruebas (`MINERVA_REAL_BOT=false`).

## Reinicios seguros

Reinicia **entre los minutos :10 y :40** de una hora UTC que **no** sea de cierre de vela de 4 h.

- Todas las filas de la medición caen en el minuto :00/:01 (las velas de 1 h cierran cada hora; las de 4 h a las 00, 04,
  08, 12, 16 y 20 UTC; la de 1 d a las 00 UTC). A partir de :10 ya no queda etiquetado en vuelo que el apagado pueda
  cortar, y la siguiente tanda llega al menos 20 minutos después.
- En las horas de cierre de 4 h la tanda es mayor y su evaluación puede alargarse (hubo evaluaciones hasta 15 min después
  del cierre): un reinicio ahí puede cortar etiquetados.
- La vela ya cerrada se vuelve a evaluar al arrancar, pero el registro es idempotente (no duplica grupos).

Horas UTC válidas (minutos :10–:40): **01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19, 21, 22 y 23**. A evitar:
00, 04, 08, 12, 16 y 20. Para tu zona horaria, suma el desfase respecto de UTC a esas horas: son las horas locales que
debes evitar. Ejemplo: con UTC−3 (y también con UTC+1, que difiere en 4 horas) hay que evitar las 21, 01, 05, 09, 13 y 17
horas locales, y las demás son válidas entre los minutos :10 y :40. Si tu zona tiene un desfase de media hora, los minutos
válidos cambian: trabaja en UTC.

Para saber si **ahora** estás dentro de la ventana:

```bash
python -c "from datetime import datetime, UTC; n = datetime.now(UTC); print(n.strftime('%H:%M UTC'), 'ventana segura' if n.hour % 4 and 10 <= n.minute <= 40 else 'FUERA de la ventana')"
```

**Procedimiento:** con Docker, `docker compose restart minerva` (cierre ordenado: SIGTERM y hasta 30 s para que el
etiquetado en vuelo y el monitor se cierren; mismo contenedor, mismo volumen). En Windows nativo, `stop_bot.ps1` y
después `start_bot.ps1`.

**Verifica después (≤ 2 min):** `GET /health` con `status: ok`, `llm_labeling.frozen_hash_ok: true`,
`llm_labeling.enabled: true` y `paper_execution.active: true`; y, pasada la siguiente hora en punto, que suben las filas
de la medición (`measurement_status.py` sobre una copia nueva; "Grupos omitidos" no debe crecer).

**Qué pasa al reiniciar:** las posiciones paper abiertas y las sombras abiertas se **reconcilian** con velas de 1 minuto: si
el precio tocó un SL, un TP o la liquidación mientras el bot estaba apagado, se cierran con ese resultado y no a precio de
mercado de hoy. Las pausas pedidas por Telegram o el dashboard **persisten en la base** (si tras un reinicio ves `stale`
sin causa, revisa `control.paused`). Lo que no se recupera es la medición de las velas que cerraron mientras estaba apagado.

## Respaldos

### Hacer un respaldo

**Con Docker** la base está en un volumen y el respaldo lo hace `scripts/docker_db.py`, que usa la API de backup de
SQLite **dentro del contenedor** (es segura con el bot escribiendo) y trae el resultado a tu equipo:

```bash
python scripts/docker_db.py backup            # --keep 14 por defecto
```

Deja una copia en el volumen (`/app/data/backups/docker_AAAAMMDD_HHMMSS.db`) y otra en tu equipo
(`data/backups/docker_AAAAMMDD_HHMMSS.db`, hora UTC), **verifica el SHA-256 al sacarla** y conserva las 14 más recientes de
cada lado (`--keep N`). Funciona también con el contenedor detenido. Códigos de salida: 0 ok, 2 uso, 5 fallo de Docker o
de la copia. Imprime rutas y tamaño, **nunca datos de operaciones**.

**Nativo (Windows):**

```bash
python scripts/backup_db.py --keep 14
```

Copia `data/minerva_estabilidad.db` con la API de backup de SQLite (origen abierto en **solo lectura**: es seguro con el
bot en marcha), verifica la copia con `PRAGMA integrity_check` y solo entonces le da su nombre definitivo,
`data/backups/minerva_AAAAMMDD_HHMMSS.db` (hora UTC). `--keep N` borra los respaldos *más antiguos con ese prefijo* hasta
dejar N (por defecto no borra nada). Códigos de salida: 0 ok, 2 uso, 3 el origen no existe, 4 la copia no pasa la
verificación, 5 el destino ya existe.

Los respaldos se guardan en modo clásico (un solo archivo, sin `-wal` ni `-shm`).

### Programarlo

Docker **no se puede respaldar copiando archivos del volumen**, y reiniciar o reinstalar Docker Desktop puede borrarlo
(ver más abajo): el respaldo diario es tu red de seguridad. Ver [Tareas programadas](#tareas-programadas).

### Dónde guardarlos

`data/backups/` está dentro de `data/`, que **no se versiona** (y no debe): contiene la medición completa. Guarda al menos
un respaldo semanal **fuera del equipo**. No los pongas en git ni los compartas: aunque no llevan credenciales, son el dato
crudo de una medición ciega.

### Comprobar que un respaldo sirve

Un respaldo que nunca se probó no es un respaldo. Una vez al mes (y siempre antes de una mudanza), restáuralo en una ruta
temporal **distinta** de la base de trabajo y mide con él:

```bash
python scripts/restore_db.py data/backups/docker_20261101_033000.db --db /tmp/prueba_restauracion.db
python scripts/measurement_status.py --db /tmp/prueba_restauracion.db
```

(En Windows usa una ruta como `C:\temp\prueba_restauracion.db`.) Los conteos deben coincidir con los del día del respaldo.

### Restaurar

Con el **bot detenido** (nunca con un proceso escribiendo la base).

**Docker** (el contenedor escribe como el usuario `minerva`, así que el volumen queda con el dueño correcto; el respaldo
entra por un montaje de solo lectura):

```powershell
docker compose stop
docker compose run --rm --no-deps -v "${PWD}/data/backups/docker_20261101_033000.db:/in/respaldo.db:ro" minerva python scripts/restore_db.py /in/respaldo.db --force
docker compose up -d
```

```bash
docker compose stop
docker compose run --rm --no-deps -v "$PWD/data/backups/docker_20261101_033000.db:/in/respaldo.db:ro" minerva python scripts/restore_db.py /in/respaldo.db --force
docker compose up -d
```

`--force` es necesario si ya hay una base en el volumen; no la borra: la aparta como `*.previo_AAAAMMDD_HHMMSS` dentro del
volumen. Después de restaurar, comprueba `frozen_hash_ok` en `/health`.

**Nativo:** `python scripts/restore_db.py data/backups/minerva_20261101_030000.db`. El script verifica el respaldo
(integridad y que tenga las tablas de Minerva), **se niega a tocar una base que ya existe** salvo `--force`, y con
`--force` no la borra: la mueve a `<nombre>.previo_AAAAMMDD_HHMMSS` (con sus `-wal` y `-shm`) para poder deshacer.
Códigos: 0 ok, 2 uso, 3 el respaldo no existe, 4 no es válido, 5 el destino existe (usa `--force`), 6 no se pudo escribir
(la base anterior queda como estaba).

### Qué borra el volumen de Docker

El volumen `minerva_data` es la medición completa. Lo borran: `docker compose down -v`, `docker volume rm minerva_data`,
`docker volume prune --all` (sin `--all` solo borra volúmenes anónimos), `docker system prune --volumes -a` y, en Docker
Desktop, *Troubleshoot → Clean / Purge data*, *Reset to factory defaults* y desinstalar borrando los datos. **Nunca uses
esas acciones sin un respaldo reciente fuera del equipo.** `docker compose down` (sin `-v`), `stop`, `restart`,
reconstruir la imagen y actualizar Docker Desktop **no** lo tocan.

## Actualizar a una versión nueva

Antes de actualizar, lee el [registro de cambios](../CHANGELOG.md) y decide cuándo: la actualización reinicia el bot, así
que hazla en la [ventana segura](#reinicios-seguros). La base se actualiza sola con migraciones aditivas (agregan tablas y
columnas, no destruyen nada).

```bash
git fetch origin
git diff --stat HEAD origin/master -- app/llm/prompts.py app/llm/market_features.py app/trading/shadow_live_labeler.py
```

El segundo comando debe **no imprimir nada**: significa que el prompt congelado, las *features* y su hash no cambian. Si
imprime archivos, mira qué cambió antes de actualizar: `prompts.py` y `market_features.py` definen **lo que se mide**, y
un cambio ahí invalida una medición en curso (sería una medición nueva), así que **no actualices a mitad de una
medición** sin entenderlo. Si todo está igual:

```bash
git pull
docker compose up -d --build
```

Verifica en `/health` que `status` sea `ok` y `frozen_hash_ok` sea `true`, y haz un respaldo. Para una instalación nativa
de Windows: `git pull`, reinstala dependencias si cambiaron (`.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`),
recompila el dashboard (`npm --prefix frontend ci` y `npm --prefix frontend run build`) y reinicia con `stop_bot.ps1` y
`start_bot.ps1`.

## Mudar el bot a otro equipo

La medición depende de dos cosas: **la base** (todo lo medido) y **el mismo código** (para que el hash del prompt
coincida). El orden importa: primero el código, después los datos. Mientras dura la mudanza la medición se detiene: hazla
en la ventana segura y no la alargues.

1. **Equipo nuevo:** clona el repositorio y deja el mismo commit que el equipo viejo (`git log -1` debe coincidir).
   Instala Docker y crea el `.env` **a mano** (nunca por git ni pegado en un chat), con el hash de la contraseña entre
   comillas simples. Construye la imagen: `docker compose build`.
2. **Equipo viejo:** detén el bot (`docker compose stop`) y haz un respaldo (`python scripts/docker_db.py backup`). Con el
   bot detenido no hay carrera entre respaldo y escritura. Lleva el archivo al equipo nuevo (USB, red).
3. **Equipo nuevo:** importa el respaldo con el comando de [Restaurar](#restaurar) (sobre un volumen vacío no hace falta
   `--force`) y arranca con `docker compose up -d`.
4. **Verifica:** `frozen_hash_ok: true`, y una copia nueva con `measurement_status.py` cuyos conteos sigan la tendencia de
   antes (ni a cero ni duplicados).
5. Recrea en el equipo nuevo las [tareas programadas](#tareas-programadas) y **desactiva las del equipo viejo**. Si dejas
   el bot viejo encendido, tendrías dos bots.

`real_arm_started_at` y `real_arm_drawdown_baseline` (el inicio del criterio de paso a dinero real) viajan con la base: no
se reinician solos. Si quieres pasar de la instalación nativa de Windows a Docker en el mismo equipo, hay un script
dedicado: [Pasar un bot nativo a Docker](MUDANZA_DOCKER.md).

## Tareas programadas

Viven **fuera** del bot a propósito: si el bot se cuelga, siguen avisando. Todas son opcionales.

| Tarea (nombre sugerido) | Qué hace | Cuándo |
|---|---|---|
| `MinervaHealthAlert` | `scripts/telegram_alert.py`: avisa por Telegram si `/health` está `stale` o sin respuesta. Avisa en cada **cambio** de estado y, mientras sigue caído, con un recordatorio cada 60 minutos | Cada 10 min |
| `MinervaDailySummary` | El resumen diario por Telegram (solo conteos, nunca PnL) | Una vez al día |
| `MinervaBackup` | Respaldo diario consistente (ver [Respaldos](#respaldos)) | Una vez al día |
| `MinervaStartBot` | Solo para el arranque **nativo** en Windows: lanza `start_bot.ps1` al iniciar sesión. Con Docker no aplica (lo reemplaza `restart: unless-stopped`) | Al iniciar sesión |

### Windows (Programador de tareas)

**Que ninguna tarea abra una ventana de consola.** Medido en el equipo de desarrollo con tareas de prueba y un monitor
de ventanas: una acción que ejecute el `pythonw.exe` de un entorno virtual creado con `uv` **abre una consola visible cada
vez** (ese `pythonw.exe` es un trampolín de *consola*), y `powershell.exe -WindowStyle Hidden` solo la hace parpadear.
`conhost.exe --headless <programa> …` no abre nada y el programa corre igual (requiere Windows 10 1809 o posterior). Por
eso los comandos de abajo usan `conhost.exe --headless` con el `python.exe` del entorno virtual.

**Alerta de caída**, desde `cmd.exe` parado en la raíz del repositorio (el `%cd%` se expande *antes* de que `schtasks`
lo vea, así que la tarea guarda la ruta absoluta):

```bat
schtasks /Create /SC MINUTE /MO 10 /TN "MinervaHealthAlert" /ST 00:00 /F ^
    /TR "conhost.exe --headless %cd%\.venv\Scripts\python.exe %cd%\scripts\telegram_alert.py"
```

Pruébalo antes a mano: `python scripts/telegram_alert.py`.

**Resumen diario y respaldo diario** (PowerShell, en la raíz del repositorio; ajusta la hora). Con **Docker**, el resumen
usa `docker_db.py run` para sacar una copia fresca de la base del contenedor; en una instalación nativa, sustituye ese
fragmento por `scripts\telegram_daily.py`:

```powershell
$RepoRoot = (Get-Location).Path     # la raíz del repositorio
$python = "$RepoRoot\.venv\Scripts\python.exe"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# Resumen diario a las 09:15, hora local del equipo
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless $python `"$RepoRoot\scripts\docker_db.py`" run telegram_daily.py" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 09:15
# El cmdlet guarda la hora en UTC; se fija la hora LOCAL (sin "Z") para que siga siendo 09:15 con el cambio de horario.
$trigger.StartBoundary = (Get-Date -Hour 9 -Minute 15 -Second 0).ToString("yyyy-MM-ddTHH:mm:ss")
Register-ScheduledTask -TaskName "MinervaDailySummary" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Resumen diario de Minerva por Telegram."

# Respaldo diario a las 03:30
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless $python `"$RepoRoot\scripts\docker_db.py`" backup --keep 14" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 03:30
Register-ScheduledTask -TaskName "MinervaBackup" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Respaldo diario consistente de la base de Minerva (Docker)."
```

`-StartWhenAvailable` corre la tarea en cuanto se pueda si el equipo estaba apagado a esa hora. Si Docker Desktop no está
listo, `docker_db.py` espera hasta 60 s al motor y, si no responde, termina con código 5 (la tarea aparece como fallida).
El registro puede exigir una terminal con privilegios: si da "Acceso denegado", abre PowerShell como administrador.
Para ver errores, prueba antes a mano con `python scripts/telegram_daily.py --dry-run` (o `python scripts/docker_db.py
run telegram_daily.py --dry-run`), que imprime el resumen sin enviarlo.

**Arranque al iniciar sesión** (solo para el bot **nativo**):

```powershell
$RepoRoot = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\scripts\start_bot.ps1`"" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT60S"   # 60 s de retraso: tiempo para que haya red
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "MinervaStartBot" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Arranca el bot nativo de Minerva al iniciar sesion."
```

Para eliminar cualquiera: `Unregister-ScheduledTask -TaskName "<nombre>" -Confirm:$false`. Para verlas:
`schtasks /Query /FO CSV /NH` y busca "Minerva". Si una alerta ya registrada con `pythonw.exe` abre una consola cada 10
minutos, corrígela sin recrearla:

```powershell
$t = Get-ScheduledTask -TaskName MinervaHealthAlert
$t.Actions[0].Execute = "conhost.exe"
$t.Actions[0].Arguments = "--headless $RepoRoot\.venv\Scripts\python.exe scripts\telegram_alert.py"
Set-ScheduledTask -InputObject $t
```

### Linux y macOS (cron)

**No verificado:** los scripts de Python que usan estas tareas no dependen de Windows, pero estas líneas no se
ejecutaron en Linux ni en macOS.

```cron
*/10 * * * * cd /ruta/a/la/carpeta/del/repo && .venv/bin/python scripts/telegram_alert.py
15 9 * * *   cd /ruta/a/la/carpeta/del/repo && .venv/bin/python scripts/docker_db.py run telegram_daily.py
30 3 * * *   cd /ruta/a/la/carpeta/del/repo && .venv/bin/python scripts/docker_db.py backup --keep 14
```

## Controles y riesgo

### Pausa, reanudar y kill

| Acción | Efecto | Se levanta con |
|---|---|---|
| `/pause` (Telegram) o "Pausar medición" (dashboard) | Scheduler pausado: **sin señales, sombras ni etiquetado**; los cierres de posiciones siguen | `/resume` |
| `/kill` o "Kill switch" | Kill switch (no se abren entradas) **y** pausa del scheduler; los cierres siguen | `/resume` |
| `/resume` o "Reanudar" | Quita la pausa y el kill switch | — |

Durante una pausa, `/health` pasa a `stale` a los 90 minutos **a propósito** y la alerta externa te avisará. Cada acción
queda auditada (`control_audit`).

### Frenos automáticos del motor de riesgo

| Freno | Cuándo salta | Cómo se levanta |
|---|---|---|
| *Circuit breaker* | `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER` (4) pérdidas seguidas; genera un *post-mortem* | Solo, tras `CIRCUIT_BREAKER_COOLDOWN_HOURS` (8 h) |
| Límite de pérdida diaria | Pérdida del día ≥ `MAX_DAILY_LOSS_PCT` (5 %) | Solo, al cambiar el día (zona `REPORT_TIMEZONE`) |
| **Stop por drawdown** (modo `duro`) | Equity −`MAX_DRAWDOWN_PCT` (20 %) desde su pico | **A mano**: `scripts/resume_drawdown_stop.py` |
| Datos obsoletos | Sin latido del WebSocket, vela o funding vencidos | Solo, cuando vuelven los datos |
| Cupos y tope de SL | 3 posiciones, 2 por dirección, `LIVE_SL_MARGIN_CAP_PCT` | Solo |

Cada entrada rechazada queda en `risk_rejections` con su motivo (y las decisiones del brazo en `paper_execution_log`).

### Levantar el stop por drawdown

El stop duro es la señal de que algo anda mal y debe mirarlo una persona: **no** se levanta desde Telegram ni desde el
dashboard. Primero investiga por qué, y entonces:

```bash
# Docker: la base viva está en el volumen, así que se corre DENTRO del contenedor
docker compose exec -T minerva python scripts/resume_drawdown_stop.py             # solo muestra el estado
docker compose exec -T minerva python scripts/resume_drawdown_stop.py --confirm   # lo levanta

# Nativo (Windows)
python scripts/resume_drawdown_stop.py             # solo muestra el estado
python scripts/resume_drawdown_stop.py --confirm   # lo levanta
```

Reinicia el pico de equity al equity actual (capital inicial + PnL realizado; `--equity X` fija otro valor) para que una
caída *nueva* pueda volver a activar el stop; **no** reinicia el contador del criterio de paso a dinero real y no
necesita reiniciar el bot. Mientras los resultados estén ocultos **no imprime saldo ni equity**. Sin `--confirm` no
escribe nada; con `--confirm` solo actúa si el stop está activo (si no, sale con código 3).

## Cuando se abre el gate

El *gate* se abre cuando hay 300 conglomerados efectivos por lado (`gate_open` en el dashboard, en
`measurement_status.py` y en el resumen diario: "GATE ABIERTO"). Abrirlo **no desbloquea resultados**: eso requiere,
además, el registro oficial del veredicto. El procedimiento completo y sus razones están en
[`VEREDICTO_3_6.md`](VEREDICTO_3_6.md) y en la [metodología](METODOLOGIA.md); este es el camino operativo. La regla se fijó
antes de ver datos: **una sola mirada**.

1. **Haz una sola copia consistente** y úsala para todo lo que sigue. Docker:
   `python scripts/docker_db.py export --out-dir data/backups --prefix veredicto` (hace la copia con la API de backup dentro del
   contenedor, verifica su SHA-256 al sacarla e imprime solo su ruta). Nativo:
   `python scripts/backup_db.py --out-dir data/backups --prefix veredicto`. En ambos casos queda
   `data/backups/veredicto_AAAAMMDD_HHMMSS.db`. Anota su nombre.
2. **Placebo oficial** sobre esa copia, 1.000 repeticiones (unos 12 a 20 minutos; déjalo en segundo plano). El nombre de
   salida **no debe** cumplir el patrón del registro del veredicto (si lo cumpliera, desbloquearía resultados sin
   veredicto):
   `python scripts/placebo_check.py --db data/backups/veredicto_<sello>.db --out docs/PLACEBO_<fecha>.txt`.
   Pega el bloque final en [`FASE3_6_LLM.md`](FASE3_6_LLM.md), sección (f).
3. **Según el código de salida del placebo:**

   | Código | Resultado | Qué haces |
   |---|---|---|
   | 7 | NO INFORMATIVO (< 50 % de las repeticiones llega al punto de análisis) | **No corras el veredicto.** Espera **7 días** más de datos y repite desde el paso 1 con una copia nueva |
   | 0 | CALIBRADO | Sigue con el paso 4 |
   | 6 | NO calibrado | El criterio no se usa para decidir; se revisa el diseño |
   | 3 | Nada que calibrar todavía | Espera |

4. **Solo si salió CALIBRADO**, el informe, **una sola vez**, sobre la **misma copia**. El nombre **debe** cumplir
   `docs/VEREDICTO_3_6_REGISTRO*.txt` (con fecha): ese archivo es el **registro oficial**:
   `python scripts/ai_value_report.py --db data/backups/veredicto_<sello>.db --out docs/VEREDICTO_3_6_REGISTRO_<fecha>.txt`.
   Solo puede existir **un** registro oficial: `--out` se rechaza (código 5) si ya hay cualquier
   `VEREDICTO_3_6_REGISTRO*.txt` con un veredicto válido. Volver a correr con más datos **no** es un veredicto nuevo.
5. **Qué desbloquea el registro** (sin reiniciar el bot; se lee en cada petición): los resultados de la API, el dashboard,
   Telegram (`/status`, `/balance`, `/positions`, `/metrics`), los avisos push de posiciones cerradas y los informes
   (`shadow_report.py`, `lessons_report.py`, `real_money_report.py`). El resumen diario **nunca** trae resultados. Para
   **volver a ocultarlos** basta mover o renombrar el registro. Conviene versionar el registro (`git add` y un commit)
   para que quede fechado.
6. **Qué hacer con el veredicto** (decisiones tuyas, ninguna automática):
   - `APORTA_VALOR`: revisa la sensibilidad y el desglose del informe **antes** de cualquier decisión. Pasar a dinero real
     es una decisión aparte con criterios propios ([`PASO_DINERO_REAL.md`](PASO_DINERO_REAL.md),
     `scripts/real_money_report.py`): ≥ 30 días y ≥ 100 operaciones cerradas del brazo ejecutado, factor de beneficio
     mayor que 1,3 con límite inferior del IC mayor que 1, concentración ≤ 40 %, cero fallos de reconciliación, entre
     otros. Y esta versión no tiene ejecución real: habría que construirla y verificarla contra la documentación oficial
     de Bitunix.
   - `NO_APORTA_VALOR`: la IA no compensa su costo ni la dependencia de la API.
   - `INCONCLUSO`: lee el motivo (`SIN_LLM` > 10 %, o un IC que cruza entre 0 y δ = 3 %).

Con Docker, los informes (`placebo_check.py`, `ai_value_report.py`) corren en tu equipo, con el entorno virtual, sobre
esa **copia**, y escriben su `--out` en `docs/` de tu equipo. El contenedor ve `./docs` (montado de solo lectura) y
reconoce el registro oficial al instante. Los informes **nunca** abren la base viva: para un informe suelto,
`python scripts/docker_db.py run <informe>.py` saca una copia fresca, la pasa con `--db` y la borra al terminar.

## Referencia de scripts

"Seguro" significa que con el *gate* cerrado solo muestra conteos o se niega a mostrar resultados.

| Script | Para qué | Con el gate cerrado |
|---|---|---|
| `docker_db.py` | Con el bot en Docker: `backup` (respaldo), `export` (una copia) y `run <informe>.py` (copia fresca → informe → se borra) | Seguro (copias; el informe decide qué muestra) |
| `migrate_to_docker.ps1` | Mudanza del bot nativo de Windows a Docker (`-DryRun` para ensayar; vuelta atrás automática si falla) | — |
| `rollback_from_docker.ps1` | Vuelta atrás: la base del volumen de vuelta a `data\` y el arranque nativo | — |
| `db_fingerprint.py` | Integridad y filas por tabla de una base (la usa la mudanza para comprobar el volumen) | Seguro (solo conteos) |
| `docker_common.ps1`, `docker_support.py` | Piezas compartidas, no se ejecutan solas: `docker_common.ps1` (ventana segura, huellas de la base, tareas programadas) lo cargan los dos scripts de la mudanza; `docker_support.py` (hablar con Docker, sacar una copia con SHA-256, la marca) lo usan `docker_db.py`, `telegram_daily.py` y los scripts que respetan la marca | — |
| `start_bot.ps1`, `stop_bot.ps1` | Arrancar y detener el bot nativo de Windows (`start_bot.ps1` se niega si existe la marca de la mudanza) | — |
| `check_health.py` | Consulta `/health` (0 OK, 1 STALE, 2 SIN_RESPUESTA) | Seguro |
| `telegram_alert.py`, `telegram_daily.py` | Alerta de caída y resumen diario | Seguro |
| `backup_db.py`, `restore_db.py` | Respaldar y restaurar la base **nativa** (con la marca de la mudanza, se niegan a operar sobre la base por defecto) | Seguro |
| `resume_drawdown_stop.py` | Levantar el stop por drawdown | Seguro (no imprime el equity) |
| `measurement_status.py` | Avance de la medición (sobre una copia) | Seguro (solo conteos) |
| `measurement_identity.py` | ¿Un solo modelo, el prompt congelado y una sola versión de *features* en la medición? (sobre una copia) | Seguro (solo conteos y fechas) |
| `paper_execution_status.py` | Aperturas y omisiones del brazo por motivo (sobre una copia) | Seguro (sin PnL) |
| `news_status.py` | Salud de la recolección de noticias (sobre una copia) | Seguro |
| `shadow_report.py`, `lessons_report.py`, `real_money_report.py` | Informes de resultados | Se niegan a mostrar resultados |
| `placebo_check.py`, `ai_value_report.py` | Procedimiento del veredicto | El placebo puede correr antes; el informe se niega |
| `hash_password.py` | Hash bcrypt para `AUTH_PASSWORD_HASH` | — |
| `run_dev_server.py` | Servidor de pruebas en el puerto 8001 sobre una **copia** de una base (`--source-db`), con LLM, Telegram, brazo, noticias, memoria y avisos apagados por construcción | — |
| `llm_smoke.py`, `llm_pilot.py` | Prueba mínima y piloto del LLM (**gastan API** con `--real --confirm-real`) | No son de la rutina |
| `download_history.py`, `run_backtest.py`, `analyze_risk.py`, `compare_fase2_runs.py`, `repair_ohlcv_gaps.py` | Herramientas de la Fase 2 (backtest histórico); no son de la rutina | — |
| `export_schema_doc.py`, `export_docker_requirements.py` | Regeneran `docs/ESQUEMA_BD.md` y `docker/requirements.lock` (`--check` verifica que estén al día) | — |
| `build_dev_docs.py`, `dev_docs_facts.py` | Para quien modifica el código: regeneran la documentación técnica de `docs/dev/` (referencia pdoc de `app/` y `scripts/`, hechos derivados de cada archivo y grafo de paquetes); `--check` verifica que esté al día. `dev_docs_facts.py` es una pieza compartida, no se ejecuta sola | — |

## Archivos y carpetas

| Ruta | Contenido | Se versiona |
|---|---|---|
| `.env` | Credenciales y configuración local (el hash de la contraseña, entre comillas simples) | **No, nunca** |
| `data/minerva_estabilidad.db` | Base del bot **nativo** de Windows. Tras pasar a Docker es una copia vieja | **No** |
| `data/MIGRADO_A_DOCKER.txt` | Marca de la mudanza: la escribe `migrate_to_docker.ps1` y la quita `rollback_from_docker.ps1` | **No** |
| `data/backups/` | Respaldos y copias de trabajo (`minerva_*`, `docker_*`, `veredicto_*`, y los de la mudanza) | **No** |
| `logs/minerva.log` | Log principal (rota: 5 × 10 MB). Con Docker vive en el volumen `minerva_logs`: `docker compose logs minerva` | No |
| `logs/medicion.log` | Bitácora de `start_bot.ps1` (cada arranque, rechazo o error) | No |
| `logs/mudanza_docker.log` | Registro de `migrate_to_docker.ps1` y `rollback_from_docker.ps1` | No |
| `docs/VEREDICTO_3_6_REGISTRO*.txt` | Registro oficial del veredicto | Sí (recomendado) |
| Volúmenes `minerva_data`, `minerva_logs` | **Base viva** y logs del contenedor | No |

Claves de `system_state` (estado persistente del sistema): `scheduler_paused`, `kill_switch_active`,
`drawdown_stop_active`, `circuit_breaker_until`, `equity_peak_usdt`, `real_arm_started_at`, `real_arm_drawdown_baseline` y
otras; el esquema completo está en [`ESQUEMA_BD.md`](ESQUEMA_BD.md).

## Limitaciones conocidas

- Los logs de nivel INFO dejan pasar el motivo de cierre de las sombras y el PnL neto de las posiciones paper. No se
  tocaron para no alterar el comportamiento del bot en medición; cuando los resultados se desbloquean deja de importar.
- El aviso push compara estados entre consultas: una pausa seguida de una reanudación dentro del mismo intervalo
  (`TELEGRAM_PUSH_POLL_SECONDS`) no se anuncia, aunque la auditoría sí la registra.
- `stop_bot.ps1` es un corte duro; la parada de Docker es ordenada (SIGTERM, 30 s).
- Un contenedor `unhealthy` (proceso vivo pero colgado) no se reinicia solo; lo avisa la alerta externa.
- `docker compose down -v`, `docker volume prune --all` y *Reset to factory defaults* de Docker Desktop borran la base viva:
  el respaldo diario fuera del equipo es obligatorio.
- Las instalaciones nativas y los scripts de PowerShell son solo de Windows; en Linux y macOS usa Docker.
