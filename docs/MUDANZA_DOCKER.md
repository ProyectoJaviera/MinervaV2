# Pasar un bot nativo a Docker (y volver atrás)

Guía para quien **ya tiene el bot corriendo de forma nativa en Windows** (con `scripts/start_bot.ps1`, con su base
en `data\` y una medición en curso) y quiere pasarlo a Docker **sin perder esa base ni la medición**, con un solo
script y con la posibilidad de volver atrás. Minerva sigue siendo 100 % paper trading.

> **¿Instalas desde cero?** Entonces no necesitas esta guía: sigue la
> [guía de instalación](GUIA_INSTALACION.md) y arranca con `docker compose up -d`.

- **Solo Windows.** Los scripts de la mudanza son de PowerShell (`scripts/migrate_to_docker.ps1`,
  `scripts/rollback_from_docker.ps1`, `scripts/docker_common.ps1`).
- **Por qué Docker:** el bot arranca solo (`restart: unless-stopped`), la base vive en un volumen interno (SQLite en
  modo WAL no es fiable sobre las carpetas de Windows compartidas con Docker), las dependencias van fijadas con sus
  versiones exactas y el proceso corre sin privilegios.
- **Si algo sale mal:** la mudanza se deshace sola y `scripts\rollback_from_docker.ps1` es el camino de vuelta.
- **Estado de la verificación:** los scripts están probados con un `docker`, un `schtasks` y un bot nativo *falsos*
  (pruebas automáticas) y con un ensayo general contra Docker Desktop real sobre una copia de la base. Lo que no se
  pudo verificar está en [Lo que no se pudo verificar](#lo-que-no-se-pudo-verificar).

## Cómo correr la mudanza y la vuelta atrás

Desde la raíz del repositorio, en PowerShell:

```powershell
# 1) Ensayo: comprueba todo y NO cambia nada. Puedes correrlo a cualquier hora.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1 -DryRun

# 2) La mudanza de verdad. Solo corre dentro de la ventana segura. Te pide escribir MIGRAR.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1

# 3) La vuelta atrás (misma ventana; en una emergencia con Docker roto: -IgnoreWindow). Te pide escribir VOLVER.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_from_docker.ps1
```

Sin una terminal interactiva (por ejemplo, desde una tarea programada) añade `-Yes` para saltar la confirmación
escrita. Después de la mudanza el arranque es **un comando**: `docker compose up -d`. Todo lo demás (respaldos,
reinicios, informes) está en la [guía de uso](USO.md).

> **⚠ No uses `docker compose up -d` para hacer la mudanza.** Con el bot nativo apagado crearía un volumen *vacío* y
> empezaría una medición nueva (sin historial y con el etiquetado real encendido, que gasta API). Solo el script
> copia la base, la verifica y puede deshacerse. Si ya lo hiciste por error: `docker compose down` (**sin** `-v`,
> que borra los volúmenes) y haz la mudanza con el script; este se niega a seguir mientras existan volúmenes y te lo
> dice (`-ReplaceVolume` guarda antes una copia del volumen que reemplaza, así que nada se pierde sin querer).

## La ventana segura

Igual que para los reinicios (ver [Reinicios seguros](USO.md#reinicios-seguros)): **minutos :10 a :40 de una hora
UTC que no sea de cierre de vela de 4 h** (00, 04, 08, 12, 16 y 20 UTC). Todas las filas de la medición caen en el
minuto :00/:01 de cada hora; a partir de :10 ya no hay un etiquetado en vuelo que cortar y el siguiente cierre queda
a 20 minutos o más. Los dos scripts **se niegan a correr fuera de la ventana** y te dicen cuándo abre la siguiente
(en UTC y en tu hora local).

Horas UTC válidas (minutos :10 a :40): 01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19, 21, 22 y 23.
Convierte esas horas a tu zona horaria; si tu zona tiene un desfase de media hora, los minutos válidos cambian.

La mudanza dura ≈ 1,5 minutos con la imagen ya construida y hasta ≈ 3 la primera vez, porque la construye. La imagen
se construye **antes** de detener el bot, así que la medición solo está en pausa entre la parada del bot nativo y el
primer `/health` sano (≈ 1 minuto en el ensayo; con tus credenciales e interruptores reales el arranque puede
tardar algo más).

## Antes de la mudanza: lista de comprobación

1. Docker Desktop **abierto y listo** (`docker info` responde).
2. **Docker Desktop iniciando con Windows** (siguiente sección), si quieres que el bot vuelva solo tras reiniciar el PC.
3. Tu `.env` de siempre en la raíz del repositorio, **como archivo**, y en él el hash de la contraseña **entre
   comillas simples**: `AUTH_PASSWORD_HASH='$2b$12$…'` (edita esa línea a mano o genera una nueva con
   `scripts\hash_password.py`). Sin las comillas, cada `docker compose …` escribe trozos del hash en pantalla.
4. El árbol de trabajo en el commit que quieres correr (la imagen se construye con lo que haya en disco). Los archivos
   del prompt congelado (`app/llm/prompts.py`) **no deben** estar modificados: el ensayo lo comprueba.
5. `-DryRun` en verde. Los avisos que deje (Docker Desktop sin arranque automático, `.env` sin comillas, cambios sin
   confirmar) no impiden la mudanza, pero léelos.
6. Espera a la ventana segura. Si tienes la alerta externa de caída (ver [Tareas programadas](USO.md#tareas-programadas)),
   recibirás un aviso de caída y otro de recuperación (si la caída dura menos que su intervalo de 10 minutos puede
   que no avise).

## Dejar Docker Desktop iniciando con Windows

Con `restart: unless-stopped`, Docker vuelve a levantar el contenedor cuando arranca su motor, **pero Docker Desktop
no se abre solo salvo que se lo pidas** (en Windows no corre como servicio: arranca con tu sesión).

1. Docker Desktop → **Settings (engranaje) → General → "Start Docker Desktop when you sign in to your computer"** →
   marcado → *Apply*. (Los nombres exactos del menú pueden cambiar entre versiones: compruébalo en tu Docker Desktop.)
2. Verifica que Windows no lo tenga deshabilitado: **Administrador de tareas → Inicio → Docker Desktop → Habilitado**.
   Por línea de comandos:
   `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" /v "Docker Desktop"`. Si el
   primer byte del valor es `03` (impar) está **deshabilitado**; `02` o `06`, habilitado.
3. Para que el bot vuelva solo **sin que tú inicies sesión** tras un corte de luz o una actualización de Windows, el
   PC debe iniciar sesión solo (Windows → `netplwiz` → desmarcar "Los usuarios deben escribir su nombre y
   contraseña"; tiene implicancias de seguridad física) y tener desactivada la suspensión (**Configuración → Sistema →
   Inicio/apagado y suspensión → Nunca**): tras una suspensión el reloj de la máquina virtual de Docker puede
   atrasarse y el scheduler se guía por la hora.
4. Después de reiniciar el PC espera 1 o 2 minutos y comprueba: `docker compose ps` (debe decir `healthy`) y
   `http://127.0.0.1:8000/health`.

Docker **no reinicia un contenedor "unhealthy"** por sí mismo (`restart` actúa cuando el proceso termina, no cuando
se cuelga): lo avisa la alerta externa y lo arreglas con `docker compose restart minerva` en la ventana segura.

## La mudanza, paso a paso

`scripts\migrate_to_docker.ps1` hace lo siguiente, **y se detiene si algo falla**:

| Paso | Qué hace |
|---|---|
| Comprobaciones | Ventana segura · Docker responde · el `.env` existe como archivo · entorno de Python (`.venv`) · base nativa · aún no migrado · hash del prompt congelado intacto · el bot nativo responde y no está pausado · puerto de Docker libre (si es otro que el del bot nativo) · `docker-compose.yml` válido · sin contenedores ni volúmenes previos · espacio en disco · estado de la tarea `MinervaStartBot` · avisos (arranque de Docker Desktop, hash del `.env` sin comillas simples, cambios sin confirmar) |
| 1 | Respaldo de seguridad de la base nativa con el bot **corriendo** (API de backup, solo lectura): `data\backups\pre_docker_*.db` |
| 2 | Construye la imagen (el bot nativo sigue midiendo) |
| 3 | Deshabilita la tarea `MinervaStartBot`, si existe y está habilitada |
| 4 | Detiene el bot nativo con `stop_bot.ps1` — **aquí empieza la pausa de la medición** |
| 5 | Respaldo definitivo con el bot detenido (`migracion_*.db`) y su huella (integridad + filas por tabla) |
| 6 | Copia el respaldo al volumen `minerva_data` con la API de backup (`restore_db.py` dentro de un contenedor) |
| 7 | **Verifica el volumen**: `PRAGMA integrity_check` y las **mismas filas en todas las tablas** antes y después |
| 8 | Escribe la marca `data\MIGRADO_A_DOCKER.txt` (ver [La marca y las protecciones](#la-marca-y-las-protecciones)) |
| 9 | `docker compose up -d` en `127.0.0.1:8000` con los **mismos interruptores que `start_bot.ps1`** y `restart: unless-stopped` |
| 10 | Espera `/health`: `status: ok`, `frozen_hash_ok: true`, `paused: false` |

**Si algo falla a partir del paso 3 (o pulsas Ctrl+C), se deshace todo en orden inverso:** baja el contenedor, quita
la marca, borra el volumen si el contenedor nunca arrancó (si llegó a arrancar se conserva), vuelve a arrancar el bot
nativo con la base de antes (la mudanza solo la **lee**, nunca la modifica) y reactiva la tarea. Códigos de salida:

| Código | Significa |
|---|---|
| 0 | Mudanza completa |
| 1 | No se cambió nada (una comprobación falló, no escribiste MIGRAR, o el fallo fue antes del paso 3) |
| 2 | Falló y la vuelta atrás **se completó**: el bot nativo volvió a como estaba |
| 3 | Falló y la vuelta atrás **no** terminó: mira las líneas `FALLO al deshacer` del registro y [Si algo queda a medias](#si-algo-queda-a-medias) |

El registro completo queda en `logs\mudanza_docker.log`. Para reintentar tras un fallo que dejó el volumen (el
contenedor llegó a arrancar): `-ReplaceVolume` guarda antes una copia de seguridad del volumen en el host y lo
reemplaza.

**Qué esperar:** el bot dejará de contestar `/health` entre el paso 4 y el 10. Con los interruptores reales, el
notificador push del contenedor te enviará por Telegram su **mensaje de arranque** unos segundos después de levantarse
(es normal; el bot nativo hace lo mismo cada vez que arranca). Si la ventana de espera de `/health`
(`-HealthTimeoutSeconds`, 300 por defecto) se queda corta, el script deshace todo: el contenedor con las credenciales
y los interruptores reales puede tardar más en arrancar que en el ensayo.

## Después de la mudanza

1. Abre `http://127.0.0.1:8000/` y entra con tu usuario de siempre.
2. `docker compose ps` → `healthy` (Docker lo marca al primer chequeo, ≤ 1 minuto).
3. `docker compose logs --tail 50 minerva`. (Los logs de nivel INFO muestran motivos de cierre de operaciones:
   evítalos mientras la medición siga ciega.)
4. **Tareas programadas** (detalle en [Tareas programadas](USO.md#tareas-programadas)): la alerta de caída sigue
   valiendo **sin cambios** (consulta `127.0.0.1:8000`); `MinervaStartBot`, si existe, el script la deshabilita (el
   contenedor la reemplaza); el resumen diario se adapta solo (al ver la marca saca su copia del contenedor), y
   conviene programar el respaldo diario con `scripts\docker_db.py backup`.
5. Respaldos y copias para los informes: `python scripts\docker_db.py …` (ver [Respaldos](USO.md#respaldos)).

## La marca y las protecciones

Tras la mudanza, `data\minerva_estabilidad.db` es una **copia vieja** (la del momento de la mudanza). Para que nadie
la use por error:

- `scripts\migrate_to_docker.ps1` escribe `data\MIGRADO_A_DOCKER.txt` (con la fecha, el proyecto y el estado previo de
  la tarea).
- `start_bot.ps1` **se niega a arrancar** el bot nativo mientras exista (evitaría que la medición se bifurque).
- `backup_db.py`, `restore_db.py` y `resume_drawdown_stop.py` **se niegan a operar sobre la base por defecto** (darían
  por respaldado o por arreglado algo que no es la base viva) y te dicen el comando de Docker. Con `--db` explícito
  sí funcionan.
- `telegram_daily.py` saca su copia del contenedor.
- `rollback_from_docker.ps1` borra la marca al volver.

## La vuelta atrás

`scripts\rollback_from_docker.ps1` (misma ventana; en una emergencia, `-IgnoreWindow`):

| Paso | Qué hace |
|---|---|
| 1 | Copia de seguridad de la base del volumen en el host (`pre_rollback_*.db`) con el contenedor corriendo |
| 2 | Detiene el contenedor de forma ordenada (la medición se pausa) |
| 3 | Copia final de la base del volumen (API de backup dentro de un contenedor, SHA-256 al traerla) y comprueba que tiene las mismas filas en todas las tablas |
| 4 | La instala en `data\minerva_estabilidad.db` con `restore_db.py`; **la base nativa vieja no se borra**: queda como `minerva_estabilidad.db.previo_AAAAMMDD_HHMMSS` |
| 5 | Quita la marca |
| 6 | Reactiva `MinervaStartBot` si estaba habilitada antes de la mudanza (la marca lo recuerda) |
| 7 | Baja y quita el contenedor; **los volúmenes de Docker se conservan** |
| 8 | Arranca el bot nativo con `start_bot.ps1` y espera `/health` |

Si algo falla se deshace y el bot vuelve a quedar en Docker. Cuando estés seguro de que no necesitas el volumen:
`docker volume rm minerva_data minerva_logs` (esto **borra la medición** que viva ahí: mira antes
[Qué borra el volumen de Docker](USO.md#qué-borra-el-volumen-de-docker)).

## Si algo queda a medias

Casi nunca debería ocurrir (cada paso se deshace solo), pero si el script termina con el código 3 o se corta de golpe
(corte de luz):

1. **No arranques nada a mano todavía.** Mira `logs\mudanza_docker.log`: dice el último paso hecho.
2. ¿Existe `data\MIGRADO_A_DOCKER.txt`?
   - **Sí, y el contenedor corre y está sano:** la mudanza terminó; solo falta que hagas la verificación de
     [Después de la mudanza](#después-de-la-mudanza).
   - **Sí, y el contenedor no corre:** `docker compose up -d` si el volumen tiene la base
     (`docker compose run --rm --no-deps minerva python scripts/db_fingerprint.py --db /app/data/minerva_estabilidad.db`
     imprime las filas), o `scripts\rollback_from_docker.ps1` para volver.
   - **No:** la base nativa no se modificó nunca; arranca `start_bot.ps1`. Si el bot nativo estaba detenido, la
     medición se retoma desde donde estaba.
3. Los respaldos `pre_docker_*.db` y `migracion_*.db` de `data\backups\` son copias completas y verificadas de la base
   nativa en cada momento: `restore_db.py` las restaura.

## Si Docker no arranca

El bot sigue siendo recuperable: la base vive en el volumen, y el último respaldo diario (`docker_db.py backup`) está
en `data\backups\` del host. Sin Docker no hay forma de leer el volumen, así que:

1. Intenta reiniciar Docker Desktop; si no abre, reinicia el PC.
2. Si definitivamente no arranca:
   `.\.venv\Scripts\python.exe scripts\restore_db.py data\backups\docker_<último>.db --db data\minerva_estabilidad.db --force`
   (con la marca presente hay que borrarla a mano) y `start_bot.ps1`. Pierdes lo medido desde ese respaldo, por eso
   importa programar el respaldo diario.

## Lo que no se pudo verificar

- **La mudanza con tus credenciales reales** (el `.env` real, Telegram y la API de Anthropic): el ensayo usó
  credenciales de mentira y los interruptores de una instancia de prueba. En concreto, con credenciales y etiquetado
  reales el contenedor puede tardar más que el del ensayo en llegar a `/health` sano (si la espera de 300 s se queda
  corta, el script deshace todo).
- **Que tu hash de contraseña funcione** con las comillas simples y que el login entre: nadie abrió tu `.env`; lo ves
  tú al entrar al dashboard.
- **Que Docker Desktop inicie con Windows** y que el bot vuelva solo tras reiniciar el PC: depende de un ajuste tuyo y
  no se probó reiniciando el equipo.
- **Una pausa de Windows (suspensión o hibernación)** y el reloj de la máquina virtual de Docker.
- **Ctrl+C a mitad de la mudanza**: el script lo cubre con un bloque `finally`, pero no hay una prueba automática de eso.
- **El comportamiento en semanas de operación continua** (crecimiento del volumen, rotación de logs en Docker).
- **`schtasks` real:** solo se probó `/Change /DISABLE` y `/ENABLE` sobre una tarea ficticia creada por el ensayo; la
  deshabilitación de una tarea `MinervaStartBot` real no se ejerció.

El relato completo del ensayo (qué se ejecutó, qué falló y qué se corrigió) está en el
[historial](historial/MUDANZA_DOCKER_ENSAYO.md).
