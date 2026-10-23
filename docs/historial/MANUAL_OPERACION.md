# Manual de operación

Guía para quien opera Minerva día a día: qué mirar, cómo arrancar, detener y reiniciar, cómo respaldar y
mudar el bot, cómo cuidar las claves y qué hacer cuando la medición llegue a su punto de análisis.

- **Minerva es 100 % paper trading.** Nada de lo que hay aquí envía órdenes reales.
- Las rutas son relativas a la raíz del repositorio. **Nativo** = Windows con `scripts/start_bot.ps1`
  (como corre el bot real hasta la mudanza, puerto 8000). **Docker** = `docker compose`, **probado con un
  daemon real** (Docker Desktop en este PC, Fase 7: [`docs/MUDANZA_DOCKER.md`](MUDANZA_DOCKER_ENSAYO.md)); el bot real
  pasa de uno a otro con `scripts/migrate_to_docker.ps1` y vuelve con `scripts/rollback_from_docker.ps1`.
- Los comandos de PowerShell llevan `.\.venv\Scripts\python.exe`. **Con Docker la base viva está en un volumen**,
  no en `data\`: lo que lee la base (respaldos, informes, estado de la medición) corre en el host sobre una
  **copia** que saca `scripts\docker_db.py` (§5.1 y §9); lo que escribe en ella (levantar el stop por drawdown,
  restaurar) corre dentro del contenedor con `docker compose exec minerva python ...`.

## 0. Las reglas que no se rompen

| Regla | Por qué |
|---|---|
| **Un solo proceso por base de datos.** Nunca levantes Docker y `start_bot.ps1` a la vez, ni dos instancias nativas. | Dos procesos escribiendo la misma base la corrompen; con el mismo token de Telegram solo uno recibe los comandos; y gastarías la clave de Anthropic dos veces. |
| **Tras la mudanza, la base viva es la del volumen de Docker.** `data\minerva_estabilidad.db` es una copia vieja y la marca `data\MIGRADO_A_DOCKER.txt` lo dice: `start_bot.ps1` y los scripts de respaldo/restauración se niegan a usarla. | Arrancar el bot nativo sobre esa copia bifurcaría la medición en dos historias. |
| **Nunca copies el archivo `.db` a mano.** Usa `scripts/backup_db.py` (nativo) o `scripts/docker_db.py` (Docker). | La base va en modo WAL: un `.db` copiado sin su `-wal` puede quedar inconsistente. |
| **Medición ciega.** Hasta que existan 300 conglomerados efectivos por lado **y** un registro oficial del veredicto, no mires PnL, tasas de acierto ni la comparación APROBADA/RECHAZADA, ni tú ni las herramientas. | Mirar y decidir cuándo parar sesga el veredicto. Las herramientas lo imponen, pero un ojo humano sobre la base cruda también cuenta. |
| **No toques el prompt congelado ni el modelo.** No edites `app/llm/prompts.py` y no cambies `ANTHROPIC_SONNET_MODEL`. | El hash del prompt se comprueba en cada ciclo (si no coincide, el etiquetado se detiene solo). **El modelo no tiene esa vigilancia**: cambiarlo contaminaría la medición sin que nada avise (ver §7.4). |
| **Las credenciales viven solo en `.env`.** Nunca en el repositorio, en la imagen, en un chat, en un log ni en una captura. | Un secreto filtrado hay que revocarlo, y en un repositorio queda en el historial aunque se borre después. |
| **Reinicia solo en la ventana segura** (§4). | Un reinicio en mal momento corta etiquetados en vuelo y deja sombras sin etiqueta. |
| **`/pause` y `/kill` detienen la medición.** Úsalos solo si algo anda mal. | Con el scheduler pausado no hay señales, sombras ni etiquetado; cada hora pausada es medición perdida. |

## 1. Qué es "sano"

El bot es un solo proceso (FastAPI + asyncio). Cada 60 s el scheduler evalúa el cierre de vela más
reciente; `GET /health` (sin sesión) lo resume:

| Campo de `/health` | Señal sana |
|---|---|
| `status` | `ok`. Pasa a `stale` si no hubo un ciclo completo en 90 min (también durante una pausa deliberada) |
| `control.paused`, `control.kill_switch_active` | ambos `false` (si no, la medición está detenida a propósito) |
| `llm_labeling.enabled` | `true` en el bot real |
| `llm_labeling.frozen_hash_ok` | **`true`**. Si es `false` el etiquetado está detenido aunque el proceso responda: es la falla silenciosa más cara |
| `llm_labeling.cost_today_usd` | por debajo de `LLM_DAILY_BUDGET_USD`; `budget_exceeded_today` en 0 |
| `llm_labeling.failed_today` | bajo (algún fallo aislado de red es normal) |
| `paper_execution.active` | `true` en el bot real (brazo ejecutado de la cuenta paper armado) |
| `telegram_push`, `news` | informativos: su caída no afecta la medición |
| `log_dropped_count` | 0 o casi 0 (descartes de log por cola llena) |

El ritmo de la medición es lento y depende de que el bot **esté encendido**: el tiempo apagado no se
recupera. En la última medición (octubre de 2026) había ~1,25 conglomerados por día de calendario y lado frente a
~5,27 por día en marcha, con un 23,6 % de disponibilidad. Cada hora de PC apagado alarga el plazo.

## 2. Rutina

### Cada día (≈ 2 minutos)

1. **Lee el resumen de las 09:15 en Telegram** (tarea `MinervaDailySummary`). Debe decir: bot OK,
   `frozen_hash_ok`, etiquetado activo, etiquetadas y fallidas de las últimas 24 h, costo del LLM, N efectivo por
   lado (x/300) y si el gate está abierto. Nunca trae PnL.
2. **Si no llegó**: el PC estaba apagado a esa hora (la tarea corre al volver, `-StartWhenAvailable`) o el bot no
   responde. Mira `GET http://127.0.0.1:8000/health` o `.\.venv\Scripts\python.exe scripts\check_health.py`
   (códigos de salida: 0 OK, 1 STALE, 2 SIN_RESPUESTA).
3. **Si recibiste una alerta** de `MinervaHealthAlert` (cada 10 min, solo avisa en cambios de estado y con un
   recordatorio cada 60 min), ve a la sección de solución de problemas (§10).
4. En el dashboard (`http://127.0.0.1:8000/`) la pestaña *Resumen* muestra salud, costo y avance; *Riesgo y
   controles* muestra breaker, kill switch y pausa. Con el reveal cerrado ninguna pestaña trae resultados.

### Cada semana (≈ 10 minutos)

1. **Respaldo** (§5) y copia del último fuera del PC (otro disco o nube privada).
2. **Espacio en disco**: la base ronda los 160 MB y crece; `logs/minerva.log` rota solo (5 archivos de 10 MB);
   `logs/medicion_out.log` (salida de consola del arranque nativo) **no rota** (235 KB tras varios días hoy).
   Con Docker, los volúmenes viven dentro del disco virtual de Docker Desktop: `docker system df -v` los mide.
3. **Avance por conteos**, sobre una copia (nunca sobre la base de trabajo):
   `.\.venv\Scripts\python.exe scripts\measurement_status.py --db data\backups\<el respaldo más reciente>.db`
   (con Docker: `.\.venv\Scripts\python.exe scripts\docker_db.py run measurement_status.py`, que saca una copia
   fresca, la mide y la borra).
4. **Un solo modelo y el prompt congelado en la medición** (solo conteos y fechas, sin resultados), sobre la misma
   copia: `.\.venv\Scripts\python.exe scripts\measurement_identity.py --db data\backups\<el respaldo más reciente>.db`
   (con Docker, `docker_db.py run measurement_identity.py`).
   Sale con código 0 si hay una única combinación (modelo, prompt congelado, features) y con 1 si detecta una
   anomalía o si el modelo configurado en el `.env` ya no es el que midió (un cambio que se haría efectivo al
   reiniciar).
5. **Árbol del repositorio limpio y commit esperado**: `git status` sin cambios en `app/` y `git log -1`. El proceso en
   marcha ya cargó su código, pero el **próximo reinicio** cargaría lo que haya en disco: un cambio accidental en
   `app/llm/` dejaría `frozen_hash_ok` en `false` justo después de reiniciar.

### Qué no mirar (mientras el gate esté cerrado)

PnL de sombras, tasa de acierto de APROBADA/RECHAZADA, lecciones activas, saldo, equity o el motivo de cierre de las
posiciones. Si abres una copia de la base, imprime **solo conteos y estructura**. Los logs de nivel INFO ya dejan
pasar el motivo de cierre de las sombras (`Monitor cerro ... motivo=`) y el PnL neto de las posiciones paper
(`Posicion cerrada ... pnl_neto=`): es una limitación conocida (§11.6); evita abrir `logs/minerva.log` sin necesidad.

## 3. Arrancar, detener y vigilar

### 3.1 Docker — un solo comando (el arranque previsto para el bot real)

> **⚠ `docker compose up -d` a secas solo vale DESPUÉS de la mudanza** (§3.2) o en una instalación nueva. Con el bot
> real todavía nativo y apagado, crearía un volumen *vacío* y empezaría una medición nueva, sin historial y con el
> etiquetado real encendido (gasta API). Si el bot nativo está encendido, Docker falla al enlazar el puerto 8000 y no
> pasa nada; el peligro es el bot nativo apagado.

Requisitos: Docker Desktop (Compose v2) **abierto**, y el `.env` junto a `docker-compose.yml`, **como archivo**
(si falta, Docker Desktop crea una *carpeta* llamada `.env` y el bot arrancaría sin credenciales y sin avisar).

```powershell
docker compose up -d                 # la primera vez construye la imagen (~1 min) y arranca el bot real
docker compose ps                    # STATUS pasa a "healthy" en ~1 min
docker compose logs -f minerva       # evita leerlo con la medición ciega (§2 «Qué no mirar»)
docker compose stop                  # parada ordenada: SIGTERM y hasta 30 s para cerrar el lifespan
docker compose restart minerva       # reinicio ordenado (solo en la ventana segura, §4)
```

Qué trae `docker-compose.yml` (probado estáticamente en `tests/unit/test_docker_setup.py` y **de verdad** contra
Docker Desktop en la Fase 7, ver `docs/MUDANZA_DOCKER.md` §2):

- Imagen multietapa (compila el dashboard, instala versiones **exactas** de las dependencias, sin Node ni
  compiladores) y proceso sin privilegios (usuario `minerva`, uid 10001, `cap_drop: ALL`, `no-new-privileges`).
  El código de la imagen es de **root y de solo lectura** para ese usuario: solo puede escribir en `/app/data`,
  `/app/logs` y `/tmp`.
- **El `.env` no entra en la imagen ni en el entorno del contenedor**: se monta de solo lectura en `/app/.env` y
  lo lee la propia aplicación (no aparece en `docker inspect`). Es a propósito: con `env_file:` Compose
  interpolaría los `$` del hash de la contraseña y lo mutilaría (la contraseña correcta daría 401).
- **El hash de la contraseña va entre comillas simples en el `.env`** (`AUTH_PASSWORD_HASH='$2b$12$…'`, como lo
  imprime `scripts\hash_password.py`). Docker Compose lee el `.env` de la raíz en **cada** comando
  (`up`, `ps`, `logs`…); sin comillas simples toma los `$` por variables y escribe **trozos del hash en pantalla**
  («The "XYZ…" variable is not set»). El bot funciona igual, pero esas líneas no deben quedar en capturas ni en
  chats: pon las comillas y limpia la pantalla. `migrate_to_docker.ps1` lo avisa (solo el *nombre* de la variable).
- Puerto publicado **solo en `127.0.0.1`**, por defecto el 8000.
- Volúmenes con nombre `minerva_data` (la base, en `/app/data`) y `minerva_logs` (en `/app/logs`), **no** carpetas
  del host: SQLite en modo WAL no es fiable sobre el sistema de archivos compartido de Docker Desktop. Por eso la
  base **no está en `data\`**: para respaldarla o leerla se usa `scripts\docker_db.py` (§5), nunca copiar archivos.
- `./docs` montado en solo lectura: el registro oficial del veredicto (§9) se lee en cada petición y se
  desbloquea sin reconstruir la imagen.
- **Los mismos interruptores que `start_bot.ps1`** (etiquetado del LLM, Telegram, brazo ejecutado, noticias y push
  encendidos). `MINERVA_REAL_BOT=false` aplica los de un puerto de prueba (todo apagado) para probar sin gastar API
  ni competir por el token de Telegram.
- **`restart: unless-stopped` por defecto**: el contenedor vuelve solo tras un reinicio de Docker; si lo detienes tú
  con `docker compose stop`, se queda detenido. Para que Docker Desktop mismo arranque con Windows:
  `docs/MUDANZA_DOCKER.md` §5. Las instancias de prueba usan `MINERVA_RESTART=no`.
- Healthcheck contra `/health`: no sano si no responde, si el ciclo se detuvo (> 90 min) **sin** pausa deliberada o
  si `frozen_hash_ok` es `false`. Una pausa a propósito no se marca como falla. Docker **no reinicia** un
  contenedor «unhealthy» (el reinicio actúa cuando el proceso termina): lo avisa `MinervaHealthAlert`.

Variables opcionales del compose (en el entorno o en el `.env`): `MINERVA_PORT`, `MINERVA_REAL_BOT`,
`MINERVA_RESTART`, `MINERVA_ENV_FILE`.

**Redes con inspección TLS corporativa**: deja el certificado raíz de esa red como `docker/certs/<nombre>.crt`
antes de `docker compose build` (ver `docker/certs/README.md`). El `.env` y los `.crt` no se suben al repositorio.

### 3.2 Pasar el bot real de nativo a Docker (y volver)

Es una mudanza con un script, no a mano: `scripts\migrate_to_docker.ps1` (ensayo con `-DryRun`, ventana segura, respaldo
verificado, copia de la base al volumen con la API de backup, comprobación de integridad y de las filas de todas las
tablas, y vuelta atrás automática si algo falla) y `scripts\rollback_from_docker.ps1` (el camino de vuelta). Todo,
con sus pasos, códigos de salida y qué hacer si se corta a medias, en **`docs/MUDANZA_DOCKER.md`**.

Mientras el bot esté en Docker, la marca `data\MIGRADO_A_DOCKER.txt` protege la base nativa vieja: `start_bot.ps1`,
`backup_db.py`, `restore_db.py` y `resume_drawdown_stop.py` se niegan a usarla (§0).

### 3.3 Nativo (Windows) — el arranque anterior

Sigue disponible (y es el camino de vuelta). Con el bot en Docker, `start_bot.ps1` se niega a arrancar.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
```

`start_bot.ps1` abre el bot en el puerto 8000 sin consola, sobre `data\minerva_estabilidad.db`, con las
variables fijas de hoy (**con independencia de lo que diga el `.env`**): `AUTO_OPEN_WITHOUT_LLM=false`,
`LOG_LEVEL=INFO`, `LESSONS_MEMORY_ENABLED=false` y, **solo con el puerto 8000**, `LLM_LIVE_LABELING_ENABLED`,
`TELEGRAM_COMMANDS_ENABLED`, `PAPER_EXECUTION_ENABLED`, `NEWS_COLLECTION_ENABLED` y `TELEGRAM_PUSH_ENABLED` en
`true` (con cualquier otro puerto van en `false`). Se **niega a arrancar** si el puerto ya está ocupado y espera
a que `/health` responda (hasta 180 s). Escribe su bitácora en `logs\medicion.log`.

`stop_bot.ps1` termina solo el proceso que escucha el puerto 8000 y sus lanzadores. **Es un corte duro**
(Windows no da una señal de apagado cooperativo a un proceso sin consola): SQLite en modo WAL lo tolera, pero un
etiquetado en vuelo se pierde. Por eso se reinicia en la ventana segura (§4). La parada de Docker, en cambio, es
ordenada.

### 3.4 Nunca los dos a la vez

El puerto 8000 publicado por Docker choca **a propósito** con el del bot nativo: si lo levantas por error con el bot
real encendido, Docker falla al enlazar el puerto *antes* de arrancar el proceso. Y tras la mudanza `start_bot.ps1`
se niega a arrancar por la marca de §3.2. Si cambias `MINERVA_PORT` para evitarlo, tendrás dos bots con el mismo
token y la misma clave: no lo hagas salvo para pruebas (`MINERVA_REAL_BOT=false`).

### 3.5 Vigilancia externa (tareas del Programador de Windows)

Viven **fuera** del bot a propósito: si el bot se cuelga, siguen avisando. Los comandos de registro exactos están en
`docs/MIGRACION_PC.md` y en el docstring de cada script. **Comprueba cuáles existen en tu PC**
(`schtasks /Query /FO CSV /NH` y busca «Minerva»): el 2026-10-18 solo estaba registrada `MinervaHealthAlert`; el
resumen de las 09:15 y el arranque al iniciar sesión **no existían**, así que ese resumen no llegaba.

| Tarea | Qué hace | Cuándo | Con el bot en Docker |
|---|---|---|---|
| `MinervaStartBot` | Arranca el bot nativo al iniciar sesión (60 s de retraso) | Al iniciar sesión | **No aplica**: lo reemplaza `restart: unless-stopped`. `migrate_to_docker.ps1` la deshabilita si existe y `rollback_from_docker.ps1` la reactiva |
| `MinervaHealthAlert` | `scripts/telegram_alert.py`: avisa por Telegram si `/health` está `stale` o sin respuesta | Cada 10 min | **Sin cambios** (consulta `127.0.0.1:8000`) |
| `MinervaDailySummary` | `scripts/telegram_daily.py`: el resumen de las 09:15 (solo conteos) | Diaria, 09:15 hora de Chile | **Sin cambios en la tarea**: el script ve la marca `data\MIGRADO_A_DOCKER.txt` y saca su propia copia de la base del contenedor (incluso con el contenedor detenido, y entonces dice «BOT SIN RESPUESTA») |
| `MinervaBackup` (opcional, §5.2) | Respaldo diario | Diaria | Cambia el comando: `scripts\docker_db.py backup` (§5.2) |

**Que ninguna tarea abra una ventana de consola.** Medido en este PC (2026-10-19) con tareas de prueba y un monitor de
ventanas: una acción `…\.venv\Scripts\pythonw.exe` **abre una consola visible cada vez** (el `pythonw.exe` de un venv
de uv es un trampolín de *consola*, no un programa de ventanas), y `powershell.exe -WindowStyle Hidden` solo la hace
parpadear. `conhost.exe --headless <programa> …` no abre nada y el programa corre igual (los procesos ocultos que deja
un script lanzador, como el bot, siguen vivos). Por eso las órdenes de registro de este manual y de
`docs/MIGRACION_PC.md` usan `conhost.exe --headless`. Si tu `MinervaHealthAlert` se registró con `pythonw.exe`
(abre una consola cada 10 minutos), el cambio de una línea que lo corrige sin recrear la tarea está en
`docs/MIGRACION_PC.md` («Si tu `MinervaHealthAlert` ya existe…»).

## 4. Reinicios

### 4.1 Ventana segura

Reinicia **entre los minutos :10 y :40** de una hora UTC que **no** sea de cierre de vela de 4 h.

- Todas las filas de la medición caen en el minuto :00/:01 (las velas de 1 h cierran cada hora; las de 4 h a las
  00, 04, 08, 12, 16 y 20 UTC; la de 1 d a las 00 UTC). A partir de :10 ya no queda etiquetado en vuelo que el
  apagado pueda cortar, y la siguiente tanda llega al menos 20 min después.
- En las horas de cierre de 4 h la tanda es mayor y su evaluación puede alargarse (en la medición hubo evaluaciones
  hasta 15 min después del cierre): un reinicio ahí puede cortar etiquetados.
- La vela ya cerrada se vuelve a evaluar al arrancar, pero el registro es idempotente (no duplica grupos).

| | Horas válidas (minutos :10–:40) | Horas a evitar |
|---|---|---|
| UTC | 01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19, 21, 22, 23 | 00, 04, 08, 12, 16, 20 |
| Chile con horario de verano (UTC-3, como hoy) | 22, 23, 00, 02, 03, 04, 06, 07, 08, 10, 11, 12, 14, 15, 16, 18, 19, 20 | 21, 01, 05, 09, 13, 17 |
| Chile en invierno (UTC-4) | 21, 22, 23, 01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19 | 20, 00, 04, 08, 12, 16 |

### 4.2 Procedimiento

Docker (el bot real tras la mudanza): `docker compose restart minerva` (cierre ordenado: SIGTERM y hasta 30 s para
que el etiquetado en vuelo y el monitor se cierren; el mismo contenedor y el mismo volumen). Un cambio del `.env`
o del compose se aplica **solo al crear el contenedor**: tras editar uno, `docker compose up -d --force-recreate`
(los volúmenes se conservan; un simple `restart` no garantiza que el contenedor vea un `.env` reemplazado).

Nativo (antes de la mudanza o tras volver con `rollback_from_docker.ps1`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
```

Verifica después (≤ 2 min): `GET /health` con `status: ok`, `llm_labeling.frozen_hash_ok: true`,
`llm_labeling.enabled: true`, `paper_execution.active: true` y, pasada la siguiente hora en punto, que suben las
filas de la medición (`measurement_status.py` sobre una copia nueva: "Grupos omitidos" no debe crecer).

### 4.3 Qué pasa al reiniciar

Las posiciones paper abiertas y las sombras abiertas se **reconcilian** con velas de 1 minuto: si el precio tocó
un SL, TP o la liquidación mientras el bot estaba apagado, se cierran con ese resultado, no a precio de mercado
de hoy. Las pausas pedidas por Telegram o el dashboard **persisten en la base** (si tras un reinicio ves `stale` sin
causa, revisa `control.paused`). Lo que no se recupera es la medición de las velas que cerraron mientras estaba
apagado.

## 5. Respaldos

### 5.1 Hacer un respaldo

**Con Docker (el bot real tras la mudanza)** la base está en un volumen y `data\minerva_estabilidad.db` es una copia
vieja: el respaldo lo hace `scripts\docker_db.py`, que usa la API de backup de SQLite **dentro del contenedor** (es
segura con el bot escribiendo) y trae el resultado al host:

```powershell
.\.venv\Scripts\python.exe scripts\docker_db.py backup            # --keep 14 por defecto
```

Deja una copia en el volumen (`/app/data/backups/docker_AAAAMMDD_HHMMSS.db`) y otra en el host
(`data\backups\docker_AAAAMMDD_HHMMSS.db`, hora UTC), **verifica el SHA-256 al sacarla** y conserva las 14 más recientes
de cada lado (`--keep N`). Funciona también con el contenedor detenido (usa un contenedor de un solo uso sobre el mismo
volumen). Códigos de salida: 0 ok, 2 uso, 5 fallo de Docker o de la copia. Imprime rutas y tamaño, **nunca datos de
operaciones**.

**Nativo** (antes de la mudanza, o tras `rollback_from_docker.ps1`):

```powershell
.\.venv\Scripts\python.exe scripts\backup_db.py --keep 14
```

Copia `data\minerva_estabilidad.db` con la API de backup de SQLite (origen abierto en **solo lectura**, es seguro
con el bot en marcha), verifica la copia con `PRAGMA integrity_check` y solo entonces le da su nombre definitivo:
`data\backups\minerva_AAAAMMDD_HHMMSS.db` (hora UTC). Una copia corrupta no se conserva. `--keep N` borra los
respaldos *más antiguos con ese prefijo* hasta dejar N (por defecto no borra nada). Códigos de salida: 0 ok, 2 uso,
3 el origen no existe, 4 la copia no pasa la verificación, 5 el destino ya existe. **Con la marca de la mudanza
(`data\MIGRADO_A_DOCKER.txt`) se niega a respaldar la base por defecto** (daría por respaldada una copia vieja) y te
manda a `docker_db.py backup`; con `--db` explícito sí funciona.

Los respaldos se guardan en modo clásico (un solo archivo, sin `-wal` ni `-shm`), así que abren desde cualquier parte,
incluso de solo lectura; el bot los vuelve a poner en WAL al conectarse.

### 5.2 Programarlo (recomendado con Docker)

Mismo patrón que las otras tareas de `docs/MIGRACION_PC.md`. **Docker no se puede respaldar copiando archivos del
volumen**, y reiniciar o reinstalar Docker Desktop puede borrarlo (§5.6): el respaldo diario es la red de seguridad.

```powershell
$RepoRoot = "<carpeta del repo>"
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless $RepoRoot\.venv\Scripts\python.exe scripts\docker_db.py backup --keep 14" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 03:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "MinervaBackup" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Respaldo diario consistente de la base de Minerva (Docker)."
```

(Nativo: el mismo bloque con `scripts\backup_db.py --keep 14`.) Si Docker Desktop no está listo a esa hora, el script
espera hasta 60 s al motor y, si no responde, termina con código 5: la tarea aparecerá como fallida en el Programador.
El `conhost.exe --headless` del comando es para que la tarea no abra una ventana de consola (§3.5).

### 5.3 Dónde guardarlos

`data\backups\` está dentro de `data\`, que **no se versiona** (y no debe): contiene la medición completa. Guarda al
menos un respaldo semanal **fuera del PC** (otro disco, nube privada). No los pongas en git ni los compartas: aunque
no llevan credenciales, son el dato crudo de una medición ciega.

### 5.4 Comprobar que un respaldo sirve

Un respaldo que nunca se probó no es un respaldo. Una vez al mes (y siempre antes de una migración), restáuralo en una
ruta temporal **distinta** de la base de trabajo y mide con él:

```powershell
.\.venv\Scripts\python.exe scripts\restore_db.py data\backups\docker_20261101_033000.db --db C:\temp\prueba_restauracion.db
.\.venv\Scripts\python.exe scripts\measurement_status.py --db C:\temp\prueba_restauracion.db
```

Los conteos deben coincidir con los del día del respaldo.

### 5.5 Restaurar

Con el **bot detenido** (nunca con un proceso escribiendo la base).

**Docker** (el contenedor escribe como el usuario `minerva`, así que el volumen queda con el dueño correcto; el
respaldo entra por un montaje de solo lectura):

```powershell
docker compose stop
docker compose run --rm --no-deps -v "${PWD}/data/backups/docker_20261101_033000.db:/in/respaldo.db:ro" minerva python scripts/restore_db.py /in/respaldo.db --force
docker compose up -d
```

`--force` es necesario si ya hay una base en el volumen; no la borra: la aparta como `*.previo_AAAAMMDD_HHMMSS`
dentro del volumen (`docker compose run --rm --no-deps minerva ls /app/data` la muestra). Después de restaurar,
comprueba `frozen_hash_ok` (§4.2).

**Nativo**:

```powershell
.\.venv\Scripts\python.exe scripts\restore_db.py data\backups\minerva_20261101_030000.db
```

El script verifica el respaldo (integridad y que tenga las tablas de Minerva), **se niega a tocar una base que ya
existe** salvo `--force`, y con `--force` no la borra: la mueve a `<nombre>.previo_AAAAMMDD_HHMMSS` (con sus `-wal`
y `-shm`) para poder deshacer. Escribe en un archivo temporal junto al destino y lo pone en su sitio con un
reemplazo atómico. Códigos: 0 ok, 2 uso, 3 el respaldo no existe, 4 no es válido, 5 el destino existe (usa
`--force`), 6 no se pudo escribir (la base anterior queda como estaba). Con la marca de la mudanza puesta, se niega
a restaurar sobre la base por defecto.

### 5.6 Qué borra el volumen de Docker (leer una vez)

El volumen `minerva_data` es la medición completa. Lo borran: `docker compose down -v`, `docker volume rm minerva_data`,
`docker volume prune --all` (sin `--all` solo borra volúmenes anónimos), `docker system prune --volumes -a`, y en
Docker Desktop *Troubleshoot → Clean / Purge data*, *Reset to factory defaults* y desinstalar borrando los datos.
**Nunca uses esas acciones sin un respaldo reciente fuera del PC (§5.3).** `docker compose down` (sin `-v`), `stop`,
`restart`, reconstruir la imagen y actualizar Docker Desktop **no** lo tocan.

## 6. Mudar el bot a otro PC

(Pasar el bot de **nativo a Docker en este mismo PC** es otra cosa, con su propio script: §3.2 y
`docs/MUDANZA_DOCKER.md`.)

La medición depende de dos cosas: **la base** (todo lo medido) y **el mismo código** (para que el hash del prompt
coincida). El orden importa: primero el código, después los datos. Mientras dura la mudanza la medición se
detiene: hazla en la ventana segura (§4.1) y no la alargues.

### 6.1 Antes de empezar

- [ ] En el PC nuevo: `git clone` y `git log -1` muestra **el mismo commit** que el PC viejo.
- [ ] Entorno listo (Docker: Docker Desktop abierto y `docker compose build` termina bien; nativo: `.venv` con
      `pip install -e ".[dev]"`).
- [ ] `.env` copiado **a mano** (nunca por git ni pegado en un chat), con el hash de la contraseña entre comillas
      simples (§3.1). Ojo: `start_bot.ps1` ignora el `DATABASE_PATH` del `.env` y usa `data\minerva_estabilidad.db`;
      en Docker lo fija el compose.
- [ ] `pytest` y `ruff check .` en verde en el PC nuevo (nativo).

### 6.2 Secuencia

1. **PC viejo**: detén el bot (Docker: `docker compose stop`; nativo: `stop_bot.ps1`). Con el bot detenido no hay
   carrera entre respaldo y escritura.
2. **PC viejo**: el respaldo (Docker: `scripts\docker_db.py backup`; nativo: `scripts\backup_db.py`) y lleva el
   archivo al PC nuevo (USB, red).
3. **PC nuevo, Docker**: importa con el comando de §5.5 (sobre un volumen vacío no hace falta `--force`) y
   `docker compose up -d`. **PC nuevo, nativo**: pon el respaldo en `data\minerva_estabilidad.db` con
   `scripts\restore_db.py <respaldo>` y arranca con `start_bot.ps1`.
4. Verifica (§4.2): `frozen_hash_ok: true`, y una copia nueva con `measurement_status.py` cuyos conteos sigan la
   tendencia de antes (ni a cero ni duplicados).
5. Recrea en el PC nuevo las tareas de §3.5 (con las variantes de Docker) y **desactiva las del PC viejo**
   (`Unregister-ScheduledTask`). Si dejas el bot viejo encendido, tendrías dos bots.
6. Docker 24/7: Docker Desktop debe iniciar con Windows y el PC iniciar sesión solo (`docs/MUDANZA_DOCKER.md` §5);
   `restart: unless-stopped` ya es el valor por defecto.

`real_arm_started_at` y `real_arm_drawdown_baseline` (el inicio del criterio de dinero real) viajan con la base: no se
reinician solos.

### 6.3 Volver atrás

Mientras no hayas borrado nada del PC viejo, volver es: detener el nuevo, arrancar el viejo. El respaldo anterior a la
mudanza sigue siendo tu punto de retorno. Lo medido en el PC nuevo entre una cosa y otra no se fusiona: elige un
solo lado.

## 7. Claves y credenciales

Todas viven en el `.env` (copiado de `.env.example`, que tiene todas las variables **sin valores**). `.env` está en
`.gitignore` y `.dockerignore`; el repositorio tiene una prueba que falla si aparece una credencial versionada.

| Variable | Qué es | Cómo se obtiene |
|---|---|---|
| `ANTHROPIC_API_KEY` | La única que cuesta dinero | Consola de Anthropic |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Bot de avisos y comandos | `@BotFather` (`/newbot`); el chat id se lee de `getUpdates` tras escribirle al bot (ver `scripts/telegram_alert.py`) |
| `AUTH_USERNAME`, `AUTH_PASSWORD_HASH` | Acceso al dashboard | `.\.venv\Scripts\python.exe scripts\hash_password.py` (pide la clave oculta e imprime la línea `AUTH_PASSWORD_HASH='…'` **con comillas simples**: pégala tal cual, §3.1) |
| `JWT_SECRET` | Firma de la sesión (24 h, cookie `httpOnly`) | `python -c "import secrets; print(secrets.token_hex(32))"` (mínimo 32 caracteres) |
| `COINGECKO_API_KEY` | Opcional (clave gratuita) | CoinGecko |

### 7.1 Anthropic: gasto y rotación

- **Tope diario del bot**: `LLM_DAILY_BUDGET_USD` (1,0 por defecto), con **corte duro**: al agotarse, las sombras
  siguen pero quedan `SIN_LLM`. El día se mide en la zona `REPORT_TIMEZONE` (`America/Santiago`). Subirlo no es
  "gastar más porque sí": las sombras `SIN_LLM` cuentan contra el tope del 10 % del veredicto, así que un presupuesto
  corto *daña la medición*. Decide con `/health.llm_labeling.cost_today_usd` y el costo acumulado de
  `measurement_status.py`.
- **Tope mensual en la consola de Anthropic**: además del tope diario, fija un límite de gasto de la organización o
  del espacio de trabajo en la consola (el nombre exacto del menú cambia con el tiempo; verifícalo allí). Es el
  freno si el bot tuviera un fallo que el tope diario no ve.
- **Rotar la clave** (rutina o sospecha): crea una clave nueva en la consola → edítala en el `.env` → reinicia en la
  ventana segura (§4) → comprueba que `llm_labeling.failed_today` no sube → **revoca la anterior**. Las
  configuraciones se leen solo al arrancar.
- **Si se filtró** (la pegaste en un chat, un log, una captura): revócala **primero** en la consola y después haz lo
  de arriba. Mientras el bot corra con la clave revocada, el etiquetado fallará y las sombras quedarán `SIN_LLM`:
  reinicia pronto con la nueva.

### 7.2 Telegram

Para rotar el token: en `@BotFather`, `/revoke` (o el comando equivalente de tu versión), pega el nuevo token en el
`.env` y reinicia. Un token admite **un solo consumidor** de `getUpdates`: si el bot de comandos "no responde", mira
primero que no haya otra instancia (Docker + nativo, o una de pruebas) usando el mismo token. El `TELEGRAM_CHAT_ID`
fija el único chat autorizado (debe ser un chat privado: un id de grupo o canal, negativo, se rechaza); cualquier otro
chat es ignorado.

### 7.3 Contraseña del dashboard y `JWT_SECRET`

Para cambiar la contraseña: `scripts\hash_password.py`, pega la línea `AUTH_PASSWORD_HASH='...'` (con sus comillas
simples) en el `.env` y reinicia. Cambiar `JWT_SECRET` cierra todas las sesiones abiertas. La API escucha solo en `127.0.0.1`; los controles
(pausa, reanudar, kill) exigen sesión, mismo origen, JSON y una palabra de confirmación exacta, y quedan en
`control_audit`. `DASHBOARD_CONTROLS_ENABLED=false` hace que la API los rechace con 403.

### 7.4 Qué no cambiar durante la medición

`ANTHROPIC_SONNET_MODEL` (el modelo que etiqueta), `LLM_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS`, los precios
(`LLM_PRICE_*`, que solo afectan al costo mostrado) y el prompt. **El modelo es parte de lo que se mide, pero el
veredicto y `/health` filtran por el hash del prompt, no por el modelo**: un cambio de modelo contaminaría la
medición sin que nada lo señale. La comprobación semanal de §2 (`scripts/measurement_identity.py`) existe para
detectarlo, incluso **antes** de reiniciar (compara el modelo del `.env` con el de las filas medidas). Si hubiera
que cambiarlo, es una decisión que reinicia la medición, no un ajuste.

## 8. Controles y riesgo

### 8.1 Pausa, reanudar, kill

| Acción | Efecto | Se levanta con |
|---|---|---|
| `/pause` (Telegram) o "Pausar medición" (dashboard) | Scheduler pausado: **sin señales, sombras ni etiquetado**; los cierres de posiciones siguen | `/resume` |
| `/kill` o "Kill switch" | Kill switch (no se abren entradas) **y** pausa del scheduler; los cierres siguen | `/resume` |
| `/resume` o "Reanudar" | Quita pausa y kill switch | — |

En Telegram cada una pide confirmación: envías `/pause`, y dentro de 60 s `/pause SI`. En el dashboard hay que escribir
la palabra exacta (`PAUSAR`, `REANUDAR`, `KILL`). Cada acción queda auditada (`control_audit`) y el aviso push la
anuncia (si pausas y reanudas entre dos consultas del notificador, el aviso no verá el cambio, pero la auditoría sí).
Durante una pausa `/health` pasa a `stale` a los 90 min **a propósito** y la alerta externa te avisará.

### 8.2 Frenos automáticos del motor de riesgo

| Freno | Cuándo salta | Cómo se levanta |
|---|---|---|
| Circuit breaker | `CONSECUTIVE_LOSSES_CIRCUIT_BREAKER` (4) pérdidas seguidas; genera un post-mortem | Solo, tras `CIRCUIT_BREAKER_COOLDOWN_HOURS` (8 h) |
| Límite de pérdida diaria | Pérdida del día ≥ `MAX_DAILY_LOSS_PCT` (5 %) | Solo, al cambiar el día (zona `REPORT_TIMEZONE`) |
| **Stop por drawdown** (modo `duro`) | Equity −`MAX_DRAWDOWN_PCT` (20 %) desde su pico | **A mano**: `scripts/resume_drawdown_stop.py` |
| Datos obsoletos | Sin latido del WebSocket, vela o funding vencidos | Solo, cuando vuelven los datos |
| Cupos y tope de SL | 3 posiciones, 2 por dirección, `LIVE_SL_MARGIN_CAP_PCT` | Solo |

Cada entrada rechazada queda en `risk_rejections` con su motivo (y el brazo ejecutado en `paper_execution_log`).

### 8.3 Levantar el stop por drawdown

El stop duro es la señal de que algo anda mal y debe mirarlo una persona: **no** se levanta desde Telegram ni el
dashboard. Primero investiga por qué, y entonces:

```powershell
# Docker (el bot real tras la mudanza): la base viva está en el volumen, así que se corre DENTRO del contenedor
docker compose exec -T minerva python scripts/resume_drawdown_stop.py             # solo muestra el estado
docker compose exec -T minerva python scripts/resume_drawdown_stop.py --confirm   # lo levanta

# Nativo
.\.venv\Scripts\python.exe scripts\resume_drawdown_stop.py             # solo muestra el estado
.\.venv\Scripts\python.exe scripts\resume_drawdown_stop.py --confirm   # lo levanta
```

Con la marca de la mudanza puesta, la versión nativa se niega a actuar sobre `data\minerva_estabilidad.db` (es una
copia vieja: levantar el stop ahí no cambiaría nada en el bot).

Reinicia el pico de equity al equity actual (capital inicial + PnL realizado; `--equity X` fija otro valor) para que
una caída *nueva* pueda volver a activar el stop, **no** reinicia el contador del criterio de dinero real y no
necesita reiniciar el bot. Mientras los resultados estén ocultos **no imprime saldo ni equity**. Sin `--confirm` no
escribe nada; con `--confirm` solo actúa si el stop está activo (si no, sale con código 3).

## 9. Cuando se abra el gate

El **gate** se abre cuando hay 300 conglomerados efectivos por lado (`gate_open` en el dashboard, en
`measurement_status.py` y en el resumen diario: "GATE ABIERTO"). Abrirlo **no desbloquea resultados**: eso requiere,
además, el registro oficial del veredicto. El procedimiento completo y sus razones están en
`docs/VEREDICTO_3_6.md`; este es el camino operativo. La regla se fijó antes de ver datos: **una sola mirada**.

1. **Haz una sola copia consistente** y úsala para todo lo que sigue. Docker (la base viva está en el volumen):
   `.\.venv\Scripts\python.exe scripts\docker_db.py export --out-dir data\backups --prefix veredicto`
   (hace la copia con la API de backup dentro del contenedor, verifica su SHA-256 al sacarla e imprime solo su ruta).
   Nativo: `.\.venv\Scripts\python.exe scripts\backup_db.py --out-dir data\backups --prefix veredicto`.
   En ambos casos queda `data\backups\veredicto_AAAAMMDD_HHMMSS.db`. Anota su nombre.
2. **Placebo oficial** sobre esa copia, 1.000 repeticiones (unos 12–20 min; déjalo en segundo plano). El nombre de
   salida **no debe** cumplir el patrón del registro del veredicto (si lo cumpliera, desbloquearía resultados sin
   veredicto):
   `.\.venv\Scripts\python.exe scripts\placebo_check.py --db data\backups\veredicto_<sello>.db --out docs\PLACEBO_<fecha>.txt`
   Pega el bloque final en `docs/FASE3_6_LLM.md`, sección (f).
3. **Según el código de salida del placebo**:

   | Código | Resultado | Qué haces |
   |---|---|---|
   | 7 | NO INFORMATIVO (< 50 % de las repeticiones llega al punto de análisis) | **No corras el veredicto.** Espera **7 días** más de datos y repite desde el paso 1 con una copia nueva |
   | 0 | CALIBRADO | Sigue con el paso 4 |
   | 6 | NO calibrado | El criterio no se usa para decidir; se revisa el diseño |
   | 3 | Nada que calibrar todavía | Espera |

4. **Solo si salió CALIBRADO**, el informe, **una sola vez**, sobre la **misma copia**. El nombre **debe** cumplir
   `docs\VEREDICTO_3_6_REGISTRO*.txt` (con fecha): ese archivo es el **registro oficial**:
   `.\.venv\Scripts\python.exe scripts\ai_value_report.py --db data\backups\veredicto_<sello>.db --out docs\VEREDICTO_3_6_REGISTRO_<fecha>.txt`
   Solo puede existir **un** registro oficial: `--out` se rechaza (código 5) si ya hay cualquier `VEREDICTO_3_6_REGISTRO*.txt`
   con un veredicto válido. Volver a correr con más datos **no** es un veredicto nuevo.
5. **Qué desbloquea el registro** (sin reiniciar el bot, se lee en cada petición): los resultados de la API, el dashboard,
   Telegram (`/status`, `/balance`, `/positions`, `/metrics`), los avisos push de posiciones cerradas y los informes
   (`shadow_report.py`, `lessons_report.py`, `real_money_report.py`). El resumen diario de las 09:15 **nunca** trae
   resultados, ni siquiera después. Para **volver a ocultarlos** basta mover o renombrar el registro. Se recomienda versionar el registro (`git add docs/VEREDICTO_3_6_REGISTRO_<fecha>.txt` y un
   commit) para que quede fechado.
6. **Qué hacer con el veredicto** (decisiones tuyas, ninguna automática):
   - `APORTA_VALOR`: revisa la sensibilidad y el desglose del informe **antes** de cualquier decisión. Pasar a dinero
     real es una decisión aparte con criterios propios (`docs/PASO_DINERO_REAL.md`, `scripts/real_money_report.py`):
     ≥ 30 días y ≥ 100 operaciones cerradas del brazo ejecutado, factor de beneficio > 1,3 con límite inferior del
     IC > 1, concentración ≤ 40 %, cero fallos de reconciliación, entre otros. Y esta v1 no tiene ejecución real: habría que
     construirla y verificar contra la documentación oficial de Bitunix (`docs/PASO_DINERO_REAL.md` lista lo no verificado).
   - `NO_APORTA_VALOR`: la IA no compensa su costo ni la dependencia de la API.
   - `INCONCLUSO`: lee el motivo (SIN_LLM > 10 %, o IC que cruza entre 0 y δ = 3 %).

**Con Docker** no hay nada distinto salvo el paso 1: los informes (`placebo_check.py`, `ai_value_report.py`) corren en
el host, con el `.venv`, sobre esa **copia**, y escriben su `--out` en `docs\` del host. El contenedor ve `./docs`
(montado en solo lectura) y reconoce el registro oficial al instante, sin reiniciar. Los informes **nunca** abren la
base viva: por eso en Docker se les da la copia de `docker_db.py export` (o, para un informe suelto,
`scripts\docker_db.py run <informe>.py`, que saca una copia fresca, la pasa con `--db` y la borra al terminar).

## 10. Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `/health` en `stale` o alerta de caída | Pausa deliberada, PC dormido, scheduler atascado (Bitunix caído) o proceso muerto | `control.paused` en `/health` → `/resume`. Si no, `logs\minerva.log` (solo errores) y, si hace falta, reinicio en la ventana segura |
| `frozen_hash_ok: false` | El código del prompt difiere del congelado (otro commit, archivo editado) | **No sigas operando**: el etiquetado ya se detuvo solo. `git status` y `git log -1`; vuelve al commit original. Nunca edites `app/llm/prompts.py` |
| `start_bot.ps1` dice RECHAZADO | Hay algo escuchando en el puerto 8000, **o** existe `data\MIGRADO_A_DOCKER.txt` (el bot corre en Docker) | Es el bot real o un Docker: no arranques otro. Mira `logs\medicion.log`. Para volver al arranque nativo: `rollback_from_docker.ps1` |
| `backup_db.py` / `restore_db.py` / `resume_drawdown_stop.py` dicen «el bot corre en Docker» | La marca de la mudanza: `data\minerva_estabilidad.db` es una copia vieja | Usa `docker_db.py backup` o `docker compose exec …` (§5, §8.3). Con `--db` explícito sí operan |
| Docker: "port is already allocated" | El bot nativo (u otro contenedor) usa el 8000 | Es intencional. Detén uno de los dos; no cambies de puerto para correr dos bots reales |
| `docker compose ...` escribe «The "XYZ…" variable is not set» | El hash de la contraseña del `.env` lleva `$` sin comillas simples: Compose lo interpreta y muestra trozos | Pon `AUTH_PASSWORD_HASH='…'` entre comillas simples (§3.1) y limpia la pantalla; el bot no se ve afectado |
| Contenedor `unhealthy` | `/health` no responde, ciclo detenido o `frozen_hash_ok` falso | `docker inspect --format '{{json .State.Health}}' <contenedor>` da el motivo; `docker compose logs minerva` |
| `docker compose` / `docker_db.py`: «cannot connect to the docker API» o código 5 | Docker Desktop no está abierto o aún arranca | Ábrelo y espera a que `docker info` responda; el bot (con `restart: unless-stopped`) vuelve solo al iniciar el motor |
| El dashboard no pide ni acepta la contraseña tras mudar a Docker | El `.env` no llegó al contenedor (era una carpeta, o falta) | `docker compose exec -T minerva python -c "import os; print(os.path.isfile('/app/.env'))"` debe decir `True`; el `.env` debe existir **como archivo** antes de `up` |
| Telegram no responde a comandos | Token/chat mal, otra instancia con el mismo token, `TELEGRAM_COMMANDS_ENABLED` en `false` (puerto distinto del 8000 o `MINERVA_REAL_BOT=false`) | Revisa `/health.telegram_push` y que solo corra un bot real |
| Sombras `SIN_LLM` suben | Presupuesto diario agotado, clave inválida o revocada, API de Anthropic con fallos | `/health.llm_labeling` (`budget_exceeded_today`, `failed_today`); revisa la clave (§7.1) |
| El dashboard abre sin interfaz (solo la API) | Falta `frontend/dist` | `npm --prefix frontend ci` y `npm --prefix frontend run build` (en Docker se compila al construir la imagen) |
| "Datos obsoletos" / no se abren entradas | WebSocket o velas atrasadas, reloj del PC desfasado | Sincroniza la hora del PC; `data_source_health`; espera a que se recuperen |
| Stop por drawdown activo | Caída del 20 % del equity | §8.3, **después** de investigar |
| `restore_db.py` dice que el destino existe | Hay una base en el destino | Es una protección. `--force` la mueve a `*.previo_*` (no la borra) |
| Red con inspección TLS: errores de certificado | La red re-firma el tráfico | Nativo: el cliente usa el almacén del sistema (`truststore`). Docker: `docker/certs/*.crt` y reconstruye |
| `OPENSSL_Uplink ... no OPENSSL_Applink` | Build de Python 3.14 en Windows | Usa Python 3.11 o 3.12 |

## 11. Referencia

### 11.1 Carpetas y archivos

| Ruta | Contenido | Se versiona |
|---|---|---|
| `.env` | Credenciales y configuración local (el hash de la contraseña, entre comillas simples) | **No, nunca** |
| `data/minerva_estabilidad.db` | Base del bot real **nativo**. Tras la mudanza es una copia vieja (§0) | **No** |
| `data/MIGRADO_A_DOCKER.txt` | Marca de la mudanza: la escribe `migrate_to_docker.ps1` y la quita `rollback_from_docker.ps1` | **No** |
| `data/backups/` | Respaldos y copias de trabajo: `minerva_*` (nativo), `docker_*` (`docker_db.py backup`), `pre_docker_*` y `migracion_*` (mudanza), `pre_rollback_*` y `rollback_*` (vuelta atrás), `volumen_reemplazado_*`, `veredicto_*` | **No** |
| `logs/minerva.log` | Log principal (rota: 5 × 10 MB). Con Docker vive en el volumen `minerva_logs`: `docker compose logs minerva` | No |
| `logs/medicion.log` | Bitácora de `start_bot.ps1` (cada arranque, rechazo o error) | No |
| `logs/medicion_out.log` | Salida de consola del arranque nativo (no rota) | No |
| `logs/mudanza_docker.log` | Registro de `migrate_to_docker.ps1` y `rollback_from_docker.ps1` (cada paso, cada comprobación, cada deshacer) | No |
| `docs/VEREDICTO_3_6_REGISTRO*.txt` | Registro oficial del veredicto | Sí (recomendado) |
| Volúmenes `minerva_data`, `minerva_logs` | **Base viva** y logs del contenedor (§5.6: qué los borra) | No |

### 11.2 Claves de `system_state` (estado persistente del sistema)

`scheduler_paused`, `kill_switch_active`, `drawdown_stop_active`, `drawdown_breach_in_progress`,
`drawdown_stop_would_have_triggered_count` (contador del criterio de dinero real: no se reinicia solo),
`equity_peak_usdt`, `consecutive_losses`, `circuit_breaker_until`, `daily_loss_day`, `daily_loss_baseline_usdt`,
`daily_loss_limit_locked`, `drawdown_stop_last_resumed_at`, `monitor_last_seen_ms`, `signals_stale_data_count`,
`real_arm_started_at`, `real_arm_drawdown_baseline`, `universe_last_refresh_error`, `push_state` y
`telegram_last_control_action`. El esquema completo de la base está en `docs/ESQUEMA_BD.md` (generado del código).

### 11.3 Scripts

"Seguro" significa que con el gate cerrado solo muestra conteos o se niega a mostrar resultados.

| Script | Para qué | Con el gate cerrado |
|---|---|---|
| `migrate_to_docker.ps1` | **Mudanza del bot real de nativo a Docker** (`-DryRun` para ensayar; ventana segura; vuelta atrás automática si falla) | — |
| `rollback_from_docker.ps1` | **Vuelta atrás**: la base del volumen de vuelta a `data\` y el arranque nativo | — |
| `docker_db.py` | Con el bot en Docker: `backup` (respaldo), `export` (una copia) y `run <informe>.py` (copia fresca → informe → se borra) | Seguro (copias; el informe decide qué muestra) |
| `db_fingerprint.py` | Integridad y filas por tabla de una base (la usa la mudanza para comprobar el volumen) | Seguro (solo conteos) |
| `docker_common.ps1`, `docker_support.py` | Piezas compartidas, no se ejecutan solas: `docker_common.ps1` (ventana segura, huellas de la base, tarea programada, avisos) lo cargan los dos scripts de la mudanza; `docker_support.py` (hablar con Docker, sacar una copia con SHA-256, la marca) lo usan `docker_db.py`, `telegram_daily.py` y los scripts que respetan la marca | — |
| `start_bot.ps1`, `stop_bot.ps1` | Arrancar y detener el bot nativo (`start_bot.ps1` se niega si existe la marca de la mudanza) | — |
| `check_health.py` | Consulta `/health` (0 OK, 1 STALE, 2 SIN_RESPUESTA) | Seguro |
| `telegram_alert.py`, `telegram_daily.py` | Alerta de caída y resumen diario (con la marca, `telegram_daily.py` saca su copia del contenedor) | Seguro |
| `backup_db.py`, `restore_db.py` | Respaldar y restaurar la base **nativa** (con la marca, se niegan a operar sobre la base por defecto) | Seguro |
| `resume_drawdown_stop.py` | Levantar el stop por drawdown (en Docker: `docker compose exec -T minerva python scripts/resume_drawdown_stop.py`) | Seguro (no imprime el equity) |
| `measurement_status.py` | Avance de la medición (sobre una copia) | Seguro (solo conteos) |
| `measurement_identity.py` | ¿Un solo modelo, el prompt congelado y una sola versión de features en la medición? (sobre una copia) | Seguro (solo conteos y fechas) |
| `paper_execution_status.py` | Aperturas y omisiones del brazo por motivo | Seguro (sin PnL) |
| `news_status.py` | Salud de la recolección de noticias | Seguro |
| `shadow_report.py`, `lessons_report.py`, `real_money_report.py` | Informes de resultados | Se niegan a mostrar resultados |
| `placebo_check.py`, `ai_value_report.py` | Procedimiento del veredicto (§9) | El placebo puede correr antes; el informe se niega |
| `hash_password.py` | Hash bcrypt para `AUTH_PASSWORD_HASH` | — |
| `run_dev_server.py --source-db <respaldo>` | Servidor de pruebas en el 8001 sobre una copia, con LLM, Telegram, brazo, noticias, memoria y avisos apagados por construcción | — |
| `llm_smoke.py`, `llm_pilot.py` | Prueba mínima y piloto del LLM (**gastan API** con `--real`) | No son de la rutina |
| `download_history.py`, `run_backtest.py`, `analyze_risk.py`, `compare_fase2_runs.py`, `repair_ohlcv_gaps.py` | Herramientas de la Fase 2 (backtest histórico) | No son de la rutina |
| `export_schema_doc.py`, `export_docker_requirements.py` | Regeneran `docs/ESQUEMA_BD.md` y `docker/requirements.lock` (`--check` verifica que estén al día) | — |

### 11.4 Límites y cosas que el bot nunca hace

- No envía órdenes reales: `BitunixBackend` existe solo como interfaz documentada y está desactivado.
- No lee ni escribe `.env` desde sus scripts; las herramientas de informes nunca abren la base de trabajo, solo copias.
- No muestra resultados de la cuenta paper (PnL, saldo, equity, precio y motivo de cierre, factor de beneficio) por
  ningún canal mientras el reveal esté cerrado.

### 11.5 Cómo se prueba todo esto

`pytest` (unitarias, de integración y de punta a punta), `ruff check .`, `npm --prefix frontend run test` y
`npm --prefix frontend run build`. Las pruebas de punta a punta usan el sistema real sobre una base temporal y simulan
solo el exterior (Anthropic, Bitunix, WebSocket y Telegram), sin red ni gasto.

Las pruebas **no abren ventanas de consola** (todo proceso hijo de `tests/`, `scripts/` y `app/` se lanza con
`creationflags=CREATE_NO_WINDOW` y todo `Start-Process` de PowerShell con `-WindowStyle Hidden`; lo vigila
`tests/unit/test_no_console_windows.py`, también de forma empírica con la API de ventanas de Windows) y **no pueden
tocar el Programador de tareas real**: durante toda la sesión `tests/conftest.py` apunta `MINERVA_SCHTASKS_EXE` (la única
puerta de los scripts al Programador) a una trampa que no toca nada, las pruebas de la mudanza usan su `schtasks` falso
(tarea `MinervaStartBotTest`, nunca un nombre real) y `tests/unit/test_no_real_task_scheduler.py` vigila que ningún
otro código nombre a `schtasks` ni a los cmdlets `*-ScheduledTask`.

La mudanza a Docker se probó de dos maneras (detalle en `docs/MUDANZA_DOCKER.md` §2 y §13): las pruebas del repositorio
ejecutan **de verdad** los scripts de PowerShell contra un `docker`, un `schtasks` y un bot nativo *falsos*
(`tests/support/`), y el ensayo general se hizo contra **Docker Desktop real** con una instancia nativa de prueba en el
puerto 8001, Docker en el 8002 y una copia de la base real (sin tocar el 8000 ni `data\`). **No** se probó: la mudanza
con el `.env`, Telegram y la API de Anthropic reales, el reinicio del PC con Docker Desktop iniciando con Windows ni
semanas de operación continua. Antes de la real, corre `migrate_to_docker.ps1 -DryRun`.

### 11.6 Limitaciones conocidas

- Los logs de nivel INFO dejan pasar el motivo de cierre de las sombras y el PnL neto de las posiciones paper
  (`Monitor cerro ... motivo=`, `Posicion cerrada ... pnl_neto=`). No se tocaron para no alterar el comportamiento del
  bot en medición; cuando el reveal se abra deja de importar.
- El aviso push compara estados entre consultas: una pausa seguida de una reanudación dentro del mismo intervalo
  (`TELEGRAM_PUSH_POLL_SECONDS`, 10 s) no se anuncia, aunque la auditoría sí la registra.
- `stop_bot.ps1` es un corte duro (ver §3.3); la parada de Docker es ordenada (SIGTERM, 30 s).
- Docker Desktop **no inicia con Windows** en este PC mientras no lo actives (`docs/MUDANZA_DOCKER.md` §5): con
  `restart: unless-stopped` el contenedor vuelve cuando el motor arranca, pero el motor no arranca solo.
- Un contenedor `unhealthy` (proceso vivo pero colgado) **no** se reinicia solo; lo avisa `MinervaHealthAlert`.
- `docker compose down -v`, `docker volume prune --all` y *Reset to factory defaults* de Docker Desktop borran la base
  viva (§5.6): el respaldo diario fuera del PC es obligatorio.
- Un Ctrl+C a mitad de `migrate_to_docker.ps1` deshace lo hecho (bloque `finally`), pero esa ruta no tiene prueba
  automática; un corte de luz sí puede dejarlo a medias (`docs/MUDANZA_DOCKER.md` §10).
