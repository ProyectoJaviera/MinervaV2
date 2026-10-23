# Mudanza del bot real a Docker (Fase 7)

Guía para pasar el bot real del arranque nativo (`scripts/start_bot.ps1`) a Docker con un solo comando, volver
atrás si hace falta, y operarlo después. **Minerva sigue siendo 100 % paper trading.** (Esta tarea llega después de
las seis fases de `docs/SPEC.md`; en los documentos y comentarios se la llama «Fase 7» solo para ubicarla.)

- **Estado:** los scripts están listos y probados (§2 y §13), pero **la mudanza real la ejecutas tú**: nada de esto
  se corrió contra el bot del puerto 8000 ni contra `data/minerva_estabilidad.db` (§13 declara el único efecto
  colateral que el ensayo dejó en `data/backups/`).
- **Por qué Docker:** el bot arranca solo (`restart: unless-stopped`), la base vive en un volumen interno
  (SQLite en modo WAL no es fiable sobre carpetas de Windows compartidas con Docker), las dependencias van fijadas y
  el proceso corre sin privilegios.
- **Si algo sale mal:** la mudanza se deshace sola y `scripts\rollback_from_docker.ps1` es el camino de vuelta.

## 1. Cómo correr la mudanza y la vuelta atrás

Desde la raíz del repositorio, en PowerShell:

```powershell
# 1) Ensayo: comprueba todo y NO cambia nada. Puedes correrlo a cualquier hora.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1 -DryRun

# 2) La mudanza de verdad. Solo corre dentro de la ventana segura (§3). Te pide escribir MIGRAR.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1

# 3) La vuelta atrás (misma ventana; en una emergencia con Docker roto: -IgnoreWindow). Te pide escribir VOLVER.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_from_docker.ps1
```

Después de la mudanza, el arranque es **un comando**: `docker compose up -d` (también tras reiniciar el PC si
Docker Desktop inicia con Windows: §5). Todo lo demás, en [`docs/MANUAL_OPERACION.md`](MANUAL_OPERACION.md).

> **⚠ No uses `docker compose up -d` para hacer la mudanza.** Con el bot nativo apagado crearía un volumen *vacío* y
> empezaría una medición nueva (sin historial, con el etiquetado real encendido). Solo el script copia la base, la
> verifica y puede deshacerse. Si ya lo hiciste por error: `docker compose down` (**sin** `-v`, que borra volúmenes) y
> haz la mudanza con el script; este se niega a seguir mientras existan volúmenes y te lo dice (`-ReplaceVolume` guarda
> antes una copia del volumen que reemplaza, así que nada se pierde sin querer).

## 2. Qué se probó de verdad antes de entregarte esto

Docker Desktop estaba encendido, así que la Etapa 1 fue una prueba **real**: se construyó la imagen, se levantó un
contenedor en el puerto 8001 con una **copia** de la base real (API de backup de SQLite, origen en solo lectura) en un
volumen de prueba, con el etiquetado, Telegram (comandos y push), el brazo ejecutado y las noticias **apagados** y
con credenciales de mentira (cero gasto, cero conflicto con el `getUpdates` del bot real); después se bajó y se borró
el volumen. El bot real del puerto 8000 y `data/` no se tocaron.

| Qué se verificó | Resultado |
|---|---|
| `docker compose build` (3 etapas, `npm ci`, `vite build`, 38 dependencias con `--only-binary`) | Bien a la primera, ≈ 1 min en frío; imagen de 503 MB |
| Llega a `healthy`; `/health` responde | `/health` en ≈ 60 s con `status: ok` (primer ciclo del scheduler); Docker lo marca `healthy` al primer chequeo |
| `frozen_hash_ok` | `true` dentro del contenedor (el prompt congelado coincide) |
| El dashboard carga | `GET /` y su bundle JS: 200; login, `/api/auth/me` y el avance de la medición: 200 |
| El volumen persiste tras `docker compose restart` | Una pausa puesta por la API de control sobrevivió al reinicio (el apagado ordenado tardó ≈ 4 s); la auditoría guardó `pause` y `resume` |
| Permisos del usuario sin privilegios | El proceso corre como uid 10001 con `cap_drop: ALL` y `no-new-privileges`; solo puede escribir en `/app/data`, `/app/logs` y `/tmp` |
| Conectividad desde el contenedor | WebSocket y REST públicos de Bitunix funcionan (llegaron ticks) |
| Respaldo dentro del contenedor y `docker compose cp` | Con el servicio corriendo **y** detenido; el SHA-256 coincide en ambos lados |
| Resumen diario dentro del contenedor | Con el bot vivo (`Bot: OK`) y detenido (`BOT SIN RESPUESTA`, informando igual desde la base) |

### Lo que falló y lo que se corrigió

La prueba real encontró **cinco** problemas que ni las pruebas automáticas ni la validación estática habían visto:

1. **`restore_db.py` rechazaba el respaldo dentro del contenedor** (`attempt to write a readonly database`). El respaldo
   heredaba el modo WAL en su cabecera y, montado de solo lectura (`:ro`), SQLite no puede abrir un archivo WAL
   (necesita crear el `-shm`). *Corregido:* `backup_db.py` ahora guarda los respaldos en modo clásico (un archivo
   suelto que abre desde cualquier parte; el bot lo vuelve a poner en WAL al conectarse) y `restore_db.py` puede leer
   también respaldos antiguos con WAL en la cabecera (abre `immutable` solo si no hay un `-wal` con contenido ni otro
   tipo de error).
2. **Compose mutilaba el hash de la contraseña del dashboard.** Con `env_file:`, Compose interpola los `$` de los
   valores y un hash bcrypt (`$2b$12$…`, el formato de `AUTH_PASSWORD_HASH`) llegaba al contenedor con **56
   caracteres en vez de 60**: el login fallaba con la contraseña correcta (el bot habría seguido midiendo, pero sin
   acceso al panel) y Compose avisaba `The "IWD" variable is not set`. *Corregido:* el `.env` se monta de solo lectura
   en `/app/.env` y lo lee la propia aplicación con el mismo analizador que en modo nativo. Con eso el login
   funciona y los secretos ya no figuran en `docker inspect`.
3. **El código de la imagen era escribible por el proceso.** Todo `/app` pertenecía al usuario sin privilegios.
   *Corregido:* el código es de root y de solo lectura; solo `/app/data` y `/app/logs` son del usuario.
4. **Si falta el `.env`, Docker Desktop crea una carpeta con ese nombre** (comprobado: `create_host_path: false` no lo
   impide) y el contenedor arrancaría **sin credenciales y sin avisar**. *Corregido:* los scripts de la mudanza
   comprueban que sea un archivo antes de llamar a Compose.
5. **Docker Desktop no inicia con Windows en este PC.** `AutoStart` está en `false` y Windows tiene la entrada
   deshabilitada en Inicio, aunque la clave `Run` exista. Con `restart: unless-stopped` el bot no volvería tras
   reiniciar el PC. *Documentado en §5, y el script de la mudanza lo avisa.*

Además, `restart: unless-stopped` es ahora el valor por defecto del compose (las instancias de prueba usan
`MINERVA_RESTART=no`).

### Lo que falló o faltaba en la Etapa 2 (preparación y ensayo general)

1. **Un `docker compose …` a secas escribe trozos del hash de tu contraseña en la pantalla.** Compose lee el `.env`
   de la raíz en *cada* comando (aunque el contenedor ya no use `env_file`); si el hash bcrypt (`$2b$12$…`) va sin
   comillas, toma lo que sigue a un `$` por una variable y escribe `The "IWD…" variable is not set`, **con una parte
   larga del hash**, cuatro veces por comando (reproducido con un `.env` de mentira). El bot funciona igual, pero esas
   líneas acaban en capturas y chats. *Corregido:* `scripts\hash_password.py` imprime ahora el valor **entre comillas
   simples** (la aplicación lo lee igual con o sin ellas), `.env.example` lo explica, los scripts de la mudanza usan un
   archivo de variables vacío (`docker/compose.vars`) para que Compose no lea nunca el `.env`, y `migrate_to_docker.ps1`
   **avisa** —solo con el *nombre* de la variable— si el `.env` tiene un valor con `$` sin comillas simples. **Si
   generaste el hash con la versión anterior de `hash_password.py`, tu `.env` lo tiene sin comillas** (yo no abro tu
   `.env`, así que no lo sé con certeza: el aviso del `-DryRun` te lo confirma). Ponle las comillas (§4, paso 3).
2. **Ninguna prueba cubría la configuración real:** el bot nativo y el contenedor en el **mismo puerto 8000**. Todas las
   pruebas usaban dos puertos distintos, así que las esperas «el contenedor toma el puerto que dejó el bot nativo» y
   «al deshacer, el contenedor suelta el puerto antes de que el bot nativo lo pida» no se ejercían. Añadidas (pruebas
   automáticas y una repetición del ensayo real con el mismo puerto, §13).
3. **Un defecto en el informe de diferencias** (`Compare-Fingerprint`): una variable local pisaba al parámetro
   (PowerShell no distingue mayúsculas) y, si diferían dos tablas, la segunda se informaba con cifras equivocadas. No
   cambiaba el veredicto (la mudanza igualmente falla), pero el mensaje engañaba. Corregido, con una prueba que altera
   dos tablas.
4. **En este PC no existen `MinervaStartBot` ni `MinervaDailySummary`** (solo `MinervaHealthAlert`; se comprobó con
   `schtasks /Query`). Consecuencias: la mudanza real no tendrá tarea que deshabilitar, y **el resumen de las 09:15 no
   se está enviando** hoy. No se creó ninguna tarea real (es decisión tuya; el comando está en
   `docs/MIGRACION_PC.md`); el ensayo solo creó y borró una tarea ficticia.

## 3. La ventana segura

Igual que para los reinicios (manual §4.1): **minutos :10 a :40 de una hora UTC que no sea de cierre de vela de 4 h**
(00, 04, 08, 12, 16, 20 UTC). Todas las filas de la medición caen en :00/:01 de cada hora; a partir de :10 ya no hay
un etiquetado en vuelo que cortar y el siguiente cierre queda a 20 minutos o más. La mudanza dura ≈ 1,5 minutos con la
imagen ya construida (en el ensayo, 69 y 75 s) y hasta ≈ 3 la primera vez, que la construye; la imagen se construye
**antes** de detener el bot, así que la medición solo está en pausa entre la parada del bot nativo y el primer
`/health` sano:
≈ 1 minuto en el ensayo (con las credenciales y los interruptores reales el arranque puede tardar algo más).
Los dos scripts **se niegan a correr fuera de la ventana** y te dicen cuándo abre la siguiente.

| UTC | Chile con horario de verano (UTC-3) |
|---|---|
| 01, 02, 03, 05, 06, 07, 09, 10, 11, 13, 14, 15, 17, 18, 19, 21, 22, 23 | 22, 23, 00, 02, 03, 04, 06, 07, 08, 10, 11, 12, 14, 15, 16, 18, 19, 20 |

## 4. Antes de la mudanza: lista de comprobación

1. Docker Desktop **abierto y listo** (`docker info` responde).
2. **Docker Desktop iniciando con Windows** (§5), si quieres que el bot vuelva solo tras reiniciar el PC.
3. El `.env` de siempre en la raíz del repositorio (**como archivo**) y, en él, el hash de la contraseña **entre comillas
   simples**: `AUTH_PASSWORD_HASH='$2b$12$…'` (edita esa línea a mano o genera una nueva con
   `scripts\hash_password.py`). Sin ellas, cada `docker compose …` escribe trozos del hash en pantalla (§2).
4. El árbol de trabajo en el commit que quieres correr (la imagen se construye con lo que haya en disco). El
   `app/llm/prompts.py` **no debe** estar modificado: el ensayo lo comprueba (el hash debe ser el congelado).
5. `-DryRun` en verde (los avisos que deje —Docker Desktop sin autoarranque, `.env` sin comillas, cambios sin commitear—
   no impiden la mudanza, pero léelos).
6. Esperar a la ventana segura. No hace falta avisar a nadie: recibirás de `MinervaHealthAlert` un aviso de caída y
   otro de recuperación (si la caída dura menos que su intervalo de 10 minutos puede que no avise).

## 5. Dejar Docker Desktop iniciando con Windows

Con `restart: unless-stopped`, Docker vuelve a levantar el contenedor cuando arranca su motor, **pero Docker Desktop
no se abre solo salvo que se lo pidas** (en Windows no corre como servicio: arranca con tu sesión).

1. Docker Desktop → **Settings (engranaje) → General → "Start Docker Desktop when you sign in to your computer"** →
   marcado → *Apply*. (Opcional: desmarcar "Open Docker Dashboard when Docker Desktop starts".)
2. Verifica que Windows no lo tenga deshabilitado: **Administrador de tareas → Inicio → Docker Desktop → Habilitado**.
   Por línea de comandos:
   `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" /v "Docker Desktop"`. Si el
   primer byte del valor es `03` (impar) está **deshabilitado**; `02` o `06`, habilitado.
3. **En este PC hoy ambas cosas están mal** (`AutoStart: false` y valor `03…`): hay que hacer el paso 1.
4. Para que el bot vuelva solo **sin que tú inicies sesión** tras un corte de luz o una actualización de Windows, el
   PC debe iniciar sesión solo (Windows → `netplwiz` → desmarcar "Los usuarios deben escribir su nombre y contraseña";
   tiene implicancias de seguridad física) y tener desactivada la suspensión
   (**Configuración → Sistema → Inicio/apagado y suspensión → Nunca**): tras una suspensión el reloj de la máquina
   virtual de Docker puede atrasarse y el scheduler se guía por la hora.
5. Después de reiniciar el PC espera 1 o 2 minutos y comprueba: `docker compose ps` (debe decir `healthy`) y
   `http://127.0.0.1:8000/health`.

Docker **no reinicia un contenedor "unhealthy"** por sí mismo (`restart` actúa cuando el proceso termina, no cuando
se cuelga): lo avisa `MinervaHealthAlert` y lo arreglas con `docker compose restart minerva` en la ventana segura.

## 6. La mudanza, paso a paso

`scripts\migrate_to_docker.ps1` hace, **y se detiene si algo falla**:

| Paso | Qué hace |
|---|---|
| Comprobaciones | Ventana segura · Docker responde · el `.env` existe como archivo · base nativa · aún no migrado · hash del prompt congelado intacto · el bot nativo responde y no está pausado · puerto de Docker libre (si es otro que el del bot nativo) · `docker-compose.yml` válido · sin contenedores ni volúmenes previos · espacio en disco · estado de la tarea `MinervaStartBot` · avisos (autoarranque de Docker, hash del `.env` sin comillas simples, cambios sin commitear) |
| 1 | Respaldo de seguridad de la base nativa con el bot **corriendo** (API de backup, solo lectura): `data\backups\pre_docker_*.db` |
| 2 | Construye la imagen (el bot nativo sigue midiendo) |
| 3 | Deshabilita la tarea `MinervaStartBot`, si existe y está habilitada |
| 4 | Detiene el bot nativo con `stop_bot.ps1` — **aquí empieza la pausa de la medición** |
| 5 | Respaldo definitivo con el bot detenido (`migracion_*.db`) y su huella (integridad + filas por tabla) |
| 6 | Copia el respaldo al volumen `minerva_data` con la API de backup (`restore_db.py` dentro de un contenedor) |
| 7 | **Verifica el volumen**: `PRAGMA integrity_check` y las **mismas filas en todas las tablas** (`shadow_trades`, `llm_logs`, `paper_execution_log` y el resto) antes y después |
| 8 | Escribe la marca `data\MIGRADO_A_DOCKER.txt` (§8) |
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
| 3 | Falló y la vuelta atrás **no** terminó: mira las líneas `FALLO al deshacer` del registro y §10 |

El registro completo queda en `logs\mudanza_docker.log`. Para reintentar tras un fallo que dejó el volumen (el
contenedor llegó a arrancar): `-ReplaceVolume` guarda antes una copia de seguridad del volumen en el host y lo
reemplaza.

**Qué esperar:** ≈ 1,5 minutos (hasta ≈ 3 la primera vez, por construir la imagen); el bot dejará de contestar
`/health` entre el paso 4 y el 10, y `MinervaHealthAlert` puede mandar un aviso de caída y otro de recuperación (solo
avisa en cambios de estado entre sus consultas de cada 10 minutos). Con los interruptores reales, el notificador push
del contenedor te enviará por Telegram su **mensaje de arranque** unos segundos después de levantarse (es normal; el
bot nativo hace lo mismo cada vez que arranca). Si la ventana de espera de `/health` se queda corta
(`-HealthTimeoutSeconds`, 300 por defecto), el script deshace todo: el contenedor con las credenciales y los
interruptores reales puede tardar más en arrancar que el del ensayo.

## 7. Después de la mudanza

1. Abre `http://127.0.0.1:8000/` y entra con tu usuario de siempre.
2. `docker compose ps` → `healthy` (Docker lo marca al primer chequeo, ≤ 1 minuto).
3. `docker compose logs --tail 50 minerva`. (Recuerda: los logs INFO muestran motivos de cierre; evita leerlos
   mientras la medición siga ciega.)
4. **Tareas programadas** (el detalle, en el manual §3.5): en este PC solo existe `MinervaHealthAlert` (verificado el
   2026-10-18; `MinervaStartBot` y `MinervaDailySummary` no están registradas). `MinervaHealthAlert` sigue valiendo
   **sin cambios** (consulta `127.0.0.1:8000`); `MinervaStartBot`, si la registras alguna vez, el script la
   deshabilita (el contenedor la reemplaza); el resumen diario (`telegram_daily.py`), si lo registras
   (`docs/MIGRACION_PC.md`), se adapta solo: al ver la marca saca su copia del contenedor; y conviene programar el
   respaldo diario con `scripts\docker_db.py backup` (manual §5.2).
5. Respaldos y copias para los informes: `python scripts\docker_db.py …` (manual §5 y §9).

## 8. La marca y las protecciones

Tras la mudanza, `data\minerva_estabilidad.db` es una **copia vieja** (la del momento de la mudanza). Para que nadie
la use por error:

- `scripts\migrate_to_docker.ps1` escribe `data\MIGRADO_A_DOCKER.txt` (con la fecha, el proyecto y el estado previo de
  la tarea).
- `start_bot.ps1` **se niega a arrancar** el bot nativo mientras exista (evitaría que la medición se bifurque).
- `backup_db.py`, `restore_db.py` y `resume_drawdown_stop.py` **se niegan a operar sobre la base por defecto**
  (darían por respaldado o por arreglado algo que no es la base viva) y te dicen el comando de Docker. Con `--db`
  explícito sí funcionan.
- `telegram_daily.py` saca su copia del contenedor.
- `rollback_from_docker.ps1` borra la marca al volver.

## 9. La vuelta atrás

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
`docker volume rm minerva_data minerva_logs`.

## 10. Si algo queda a medias

Casi nunca debería ocurrir (cada paso se deshace solo), pero si el script termina con el código 3 o se corta de golpe
(corte de luz):

1. **No arranques nada a mano todavía.** Mira `logs\mudanza_docker.log`: dice el último paso hecho.
2. ¿Existe `data\MIGRADO_A_DOCKER.txt`?
   - **Sí, y el contenedor corre y está sano:** la mudanza terminó; solo falta que hagas la verificación de §7.
   - **Sí, y el contenedor no corre:** `docker compose up -d` si el volumen tiene la base (`docker compose run --rm
     --no-deps minerva python scripts/db_fingerprint.py --db /app/data/minerva_estabilidad.db` imprime las filas), o
     `scripts\rollback_from_docker.ps1` para volver.
   - **No:** la base nativa no se modificó nunca; arranca `start_bot.ps1`. Si el bot nativo estaba detenido, la
     medición se retoma desde donde estaba.
3. Los respaldos `pre_docker_*.db` y `migracion_*.db` de `data\backups\` son copias completas y verificadas de la base
   nativa en cada momento: `restore_db.py` las restaura.

## 11. Si Docker no arranca

El bot sigue siendo recuperable: la base vive en el volumen, y el último respaldo diario (`docker_db.py backup`) está
en `data\backups\` del host. Sin Docker no hay forma de leer el volumen, así que:

1. Intenta reiniciar Docker Desktop; si no abre, reinicia el PC.
2. Si definitivamente no arranca: `restore_db.py data\backups\docker_<último>.db --db data\minerva_estabilidad.db
   --force` (con la marca presente hay que borrarla a mano) y `start_bot.ps1`. Pierdes lo medido desde ese respaldo,
   por eso importa programar el respaldo diario.

## 12. Lo que no se pudo verificar

- **La mudanza real** (puerto 8000, el `.env` real, Telegram y la API de Anthropic con credenciales de verdad): se
  ensayó entera con Docker real pero aislada (§13); el paso con las credenciales reales y los interruptores reales es
  el primero que verás tú. En concreto: con credenciales y etiquetado reales el contenedor puede tardar más que el del
  ensayo en llegar a `/health` sano (si la espera de 300 s se queda corta, el script deshace todo).
- **La mudanza con el `.env` real:** nadie lo abrió; que su hash lleve comillas simples o no, y que el login funcione
  con tu contraseña real, lo verás tú al entrar al dashboard.
- **Que Docker Desktop inicie con Windows** y que el bot vuelva solo tras reiniciar el PC: depende de un ajuste tuyo
  (§5) y no se probó reiniciando el PC.
- **Una pausa de Windows (suspensión/hibernación)** y el reloj de la máquina virtual de Docker.
- **Ctrl+C a mitad de la mudanza**: el script lo cubre con `finally`, pero no hay una prueba automática de eso.
- **El comportamiento en semanas de operación continua** (crecimiento del volumen, rotación de logs en Docker).
- **`schtasks` real:** solo se probó `/Change /DISABLE` y `/ENABLE` sobre una tarea ficticia creada por el ensayo. La
  tarea real `MinervaStartBot` no existe en este PC, así que su deshabilitación nunca se ejerció.

## 13. El ensayo general: qué se hizo, qué se vio y qué efectos dejó en tu PC

**Montaje (todo aislado del bot real).** Una copia fresca de la base real hecha con la API de backup (origen abierto
en solo lectura) en una carpeta del scratchpad de la sesión; una instancia nativa de prueba con el `start_bot.ps1`
**real** en el puerto 8001 (por no ser el 8000, con el etiquetado, Telegram, el brazo, las noticias y el push
apagados); Docker en el 8002 y después en el 8001, con el proyecto `minerva-rehearsal` (volúmenes
`minerva-rehearsal_data` y `_logs`), credenciales de mentira y `MINERVA_REAL_BOT=false`; una tarea programada
ficticia (`MinervaRehearsalDummy`) para probar `schtasks /Change`; la hora fingida (`MINERVA_ASSUME_NOW_UTC`) dentro de
la ventana segura porque el ensayo corrió fuera de ella; y dos envolturas de `stop_bot.ps1` y `start_bot.ps1` que solo
aceptaban el puerto 8001, para que ningún defecto pudiera tocar el 8000.

| # | Qué se ejecutó | Resultado |
|---|---|---|
| 1 | `migrate_to_docker.ps1 -DryRun` | Código 0; todas las comprobaciones en `[ OK  ]`; los avisos esperados (autoarranque de Docker ×2, cambios sin commitear); no cambió nada |
| 2 | Mudanza completa, nativo 8001 → Docker 8002 | Código 0 en 69 s; la medición estuvo en pausa ≈ 58 s; **mismas filas en las 28 tablas** y `PRAGMA integrity_check` ok; tarea ficticia deshabilitada; marca escrita; `/health` `status ok`, `frozen_hash_ok true`, `paused false` |
| 3 | Con la marca puesta: `start_bot.ps1` | Rechazado (`RECHAZADO: el bot corre en Docker…`) |
| 4 | `docker_db.py backup`, `export --prefix veredicto`, `run measurement_status.py`, `run measurement_identity.py`, `run shadow_report.py` | Funcionan con el contenedor vivo y verifican el SHA-256; **`shadow_report.py` se negó a mostrar resultados (código 3)**: la puerta de resultados sigue cerrada por el camino de Docker |
| 5 | Restauración del manual (§5.5): contenedor detenido, respaldo del host montado de solo lectura, `--force` | Funcionó; la base anterior quedó como `*.previo_*` dentro del volumen, con dueño `minerva`; `docker compose up -d` la levantó |
| 6 | `rollback_from_docker.ps1 -DryRun` y real (Docker 8002 → nativo 8001) | Código 0 en 44 s; mismas filas; marca quitada; tarea reactivada; la base nativa vieja quedó como `*.previo_*` (con su `-wal` y `-shm`); el bot nativo volvió sano |
| 7 | **Fallo forzado** (mudanza con `-ReplaceVolume` y 3 s de espera de `/health`) | Código 2: bajó el contenedor, quitó la marca, **conservó el volumen** (el contenedor había llegado a arrancar), reinició el bot nativo y reactivó la tarea; `-ReplaceVolume` guardó antes una copia del volumen viejo |
| 8 | Reintento tras ese fallo (`-ReplaceVolume`) | Código 0 en 75 s; aquí apareció por primera vez el aviso del hash sin comillas simples del `.env` de prueba (solo el *nombre* de la variable, nunca su valor) |
| 9 | **Mismo puerto** (nativo 8001 y Docker 8001, como será en el 8000): mudanza completa, vuelta atrás con el contenedor *detenido* (rama «no estaba corriendo») | Código 0 en ambas; el contenedor tomó el puerto que el bot nativo acababa de dejar y viceversa |
| 10 | Desde un **clon aislado** del repositorio con la marca en su `data/`: `backup_db.py`, `restore_db.py`, `resume_drawdown_stop.py` y `start_bot.ps1` | Los cuatro rechazaron operar sobre la base vieja (código 2 / 1) y nombraron el comando de Docker |
| 11 | `telegram_daily.py --dry-run` desde ese clon, con el contenedor corriendo y detenido | Con la marca, sacó su copia **del contenedor** (el clon no tenía base nativa): `Bot (puerto 8001): OK` y, detenido, `BOT SIN RESPUESTA` informando igual desde el volumen; sin resultados (PnL) |
| — | Limpieza | Bot nativo de prueba detenido con la envoltura (puerto 8001 libre), proyecto `minerva-rehearsal` bajado con sus volúmenes (`down -v`), tarea ficticia borrada; el 8000 siguió escuchando todo el tiempo con el mismo PID |

### Efectos que el ensayo dejó en tu PC

1. **Dos respaldos de más en `data/backups/` (error de mi arnés, declarado).** Dos veces lancé `scripts/backup_db.py` sin
   `--db` creyendo que apuntaba a la carpeta del ensayo (y luego a un clon): como `backup_db.py` resuelve sus rutas por
   defecto contra el directorio de trabajo y ese era la raíz del repositorio real, creó
   `data/backups/minerva_20261019_002121.db` y `data/backups/minerva_20261019_021843.db` (≈ 160 MB cada uno): **copias
   consistentes de la base viva** hechas con la API de backup (origen en solo lectura, integridad verificada). No se
   modificó, movió ni borró ningún archivo existente de `data/`, y el bot real no se enteró. El `rm` está denegado por
   permisos y la regla es no tocar `data/`, así que **ahí quedan: puedes borrarlos o conservarlos** (son respaldos
   válidos). Además `resume_drawdown_stop.py` (sin `--confirm`, solo lectura) y `telegram_daily.py --dry-run` leyeron la
   base real por esa misma causa (sin escribir nada; el resumen usa una copia temporal). El arnés quedó corregido: el
   directorio de trabajo del ensayo es ahora una carpeta aislada del scratchpad y las protecciones se probaron en un
   clon (filas 3 y 10–11 de la tabla).
2. La tarea programada ficticia `MinervaRehearsalDummy` se creó y se borró. Las tareas reales (solo existe
   `MinervaHealthAlert`) no se tocaron.
3. Docker: la imagen `minerva:local` (503 MB) y su caché de compilación se quedan (acortan la mudanza real; `docker image
   rm minerva:local` y `docker builder prune` las quitan). No quedó ningún contenedor ni volumen de Minerva.
4. Copias de la base real (≈ 160 MB cada una) y del clon en la carpeta temporal de la sesión de Claude (scratchpad):
   `rehearsal/` (con sus respaldos), `rehearsal_repo/`, `etapa1/` y las de tareas anteriores. Puedes borrar la carpeta
   entera cuando quieras.
