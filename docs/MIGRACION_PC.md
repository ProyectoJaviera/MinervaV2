# Continuidad operativa: tarea de arranque y migración a otro PC

Pieza de continuidad operativa del plan pre-Fase 5 (aprobado 2026-10-14): el
PC actual es de pruebas y se apaga de tanto en tanto; esto deja arrancar el
bot real con un solo comando tras un reinicio, y documenta cómo mudarlo a un
PC 24/7 sin perder la medición en vivo en curso.

> **Fase 6:** la rutina diaria, los reinicios, los respaldos (`scripts/backup_db.py` y
> `scripts/restore_db.py`, que verifican la copia), la mudanza —nativa o con Docker— y las claves están
> reunidos en [`docs/MANUAL_OPERACION.md`](MANUAL_OPERACION.md). Este documento conserva el detalle de
> las tareas del Programador de Windows y de la mudanza nativa.
>
> **Fase 7 (Docker):** el bot real puede pasar a Docker, con su base en un volumen interno y arranque con
> `docker compose up -d`, con `scripts\migrate_to_docker.ps1` (y volver con `scripts\rollback_from_docker.ps1`):
> [`docs/MUDANZA_DOCKER.md`](MUDANZA_DOCKER.md). Lo de este documento sigue valiendo para el arranque nativo
> (que es también el camino de vuelta); lo que cambia con Docker está en la sección «Con Docker» del final.
> **Estado de las tareas en este PC (2026-10-18):** solo existía `MinervaHealthAlert`; `MinervaStartBot` y
> `MinervaDailySummary` **no estaban registradas** (compruébalo con `schtasks /Query /FO CSV /NH`). Sin la tarea
> del resumen, el resumen de las 09:15 no llega: regístrala con el bloque de más abajo.

## Arrancar y detener el bot real

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
```

`start_bot.ps1` arranca con las variables fijas de hoy (`AUTO_OPEN_WITHOUT_LLM=
false`, `LOG_LEVEL=INFO`, `LLM_LIVE_LABELING_ENABLED=true` -- esta y la de Telegram
**solo con el puerto 8000**; con otro puerto, una instancia de prueba, van en `false`
para no gastar API ni competir por el token), **rehúsa** arrancar
si ya hay algo escuchando en el puerto 8000 (evita dos procesos escribiendo la
misma base), y espera a que `/health` responda antes de devolver el control.
`stop_bot.ps1` detiene solo el proceso que escucha ese puerto y su cadena de
lanzadores (`cmd.exe` de `start_bot.ps1` → `.venv\Scripts\pythonw.exe` → proceso real),
verificando antes de tocar cada padre que sea exactamente el de este repo, y confirma al
final que no queda vivo ninguno; nunca otros procesos python del sistema. El detalle completo de cómo y por qué
está en el docstring de cada script (`Get-Help .\scripts\start_bot.ps1 -Full`).

`start_bot.ps1` también fija `TELEGRAM_COMMANDS_ENABLED=true` (el bot de comandos de
Telegram, subfase 3.7; ver `docs/FASE3_7_TELEGRAM.md`) **solo con el puerto 8000**; con
cualquier otro puerto lo fija en `false`, porque un token de Telegram admite un solo
consumidor de `getUpdates`. El token y el chat_id siguen viniendo del `.env`. Una pausa
pedida por Telegram (`/pause`, `/kill`) **persiste en la base**: si tras mudarte o
reiniciar el bot ves el estado `stale` sin causa, revisa `GET /health` → `control.paused`
y reanuda con `/resume`.

Igual con `PAPER_EXECUTION_ENABLED=true` (el brazo ejecutado de la cuenta paper, subfase 3.9;
ver `docs/FASE3_9_BRAZO_EJECUTADO.md`): `start_bot.ps1` lo activa **solo con el puerto 8000** y
`GET /health` lo informa en `paper_execution`. En un PC nuevo, el primer arranque crea la tabla
`paper_execution_log` y fija una sola vez `real_arm_started_at`; si migras la base, esas marcas
viajan con ella (no se reinician solas).

**Límite conocido**: Windows no da una señal de apagado cooperativo real para
un proceso sin consola -- `stop_bot.ps1` termina el proceso directo
(`TerminateProcess`), no espera a que `ShadowLiveLabeler.shutdown()` corra de
forma prolija dentro de él. SQLite en modo WAL (el que usa este proyecto)
tolera esto por diseño: cada escritura ya hace *commit* de inmediato, así que
no hay una transacción grande pendiente que perder.

## Tarea del Programador de Windows: arranque automático al iniciar sesión

Comandos verificados en esta máquina (la sintaxis es correcta; el registro en
sí puede exigir una terminal con privilegios suficientes -- si da "Acceso
denegado" en tu sesión normal, abrí PowerShell como Administrador):

**Registrar** (ajustá `$RepoRoot` si tu checkout está en otra ruta):

```powershell
$RepoRoot = "D:\Estudio\IA\Minerva"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\scripts\start_bot.ps1`"" `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT60S"   # 60s de retraso: tiempo para que haya red al iniciar sesion
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "MinervaStartBot" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Arranca el bot real de Minerva al iniciar sesion (60s de retraso)."
```

**Eliminar**:

```powershell
Unregister-ScheduledTask -TaskName "MinervaStartBot" -Confirm:$false
```

**Verificar que quedó bien registrada**:

```powershell
Get-ScheduledTask -TaskName "MinervaStartBot" | Select-Object TaskName, State
```

La tarea de alerta por Telegram (`scripts/telegram_alert.py`, cada 10 min, sin
depender del login) se registra por separado con `schtasks /Create` -- ver el
docstring de ese script para el comando exacto.

## Tarea del Programador de Windows: resumen diario por Telegram (09:15)

`scripts/telegram_daily.py` manda **un mensaje al día** con: estado de `/health`
(ok/stale, `frozen_hash_ok`, etiquetado activo), etiquetadas y fallidas de las últimas
24 h, costo del LLM en 24 h y acumulado de la medición, N efectivo por lado (x/300) y si
el gate está abierto. **Nunca PnL ni resultados de sombras.** Si el bot no responde, el
resumen lo dice y sigue informando lo que hay en la base.

Los números salen de una **copia de solo lectura** de `data\minerva_estabilidad.db`
(API de backup de sqlite, en un directorio temporal del sistema, nunca dentro de `data\`,
borrada al terminar); `/health` se consulta en vivo. Usa **esa** base por defecto -- la
misma que fija `start_bot.ps1` -- y no la de `DATABASE_PATH` del `.env`. Usa
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` del `.env` (los mismos de la alerta) sin
imprimirlos.

Probarlo a mano, sin enviar nada:

```powershell
.\.venv\Scripts\python.exe scripts\telegram_daily.py --dry-run
```

**Registrar** `MinervaDailySummary` (todos los días a las 09:15, hora local del PC; el PC
debe estar en la zona horaria de Chile -- Windows: "Pacific SA Standard Time" --, y
`-StartWhenAvailable` lo corre en cuanto se pueda si el PC estaba apagado a esa hora):

```powershell
$RepoRoot = "D:\Estudio\IA\Minerva"
$action = New-ScheduledTaskAction -Execute "$RepoRoot\.venv\Scripts\pythonw.exe" `
    -Argument "`"$RepoRoot\scripts\telegram_daily.py`"" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 09:15
# El cmdlet guarda la hora en UTC ("...12:15:00Z"), y en Chile cambia el horario de
# verano: se fija la hora LOCAL sin "Z" para que siga siendo 09:15 todo el año.
$trigger.StartBoundary = (Get-Date -Hour 9 -Minute 15 -Second 0).ToString("yyyy-MM-ddTHH:mm:ss")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "MinervaDailySummary" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Resumen diario de Minerva por Telegram (09:15 hora de Chile)."
```

**Eliminar**:

```powershell
Unregister-ScheduledTask -TaskName "MinervaDailySummary" -Confirm:$false
```

**Verificar / probar la tarea ya registrada**:

```powershell
Get-ScheduledTask -TaskName "MinervaDailySummary" | Select-Object TaskName, State
Start-ScheduledTask -TaskName "MinervaDailySummary"
```

La sintaxis (acción, disparador con la hora local fija y opciones) está verificada en
esta máquina construyendo los objetos sin registrar; el registro en sí puede exigir una
terminal con privilegios suficientes (si da "Acceso denegado", abre PowerShell como
Administrador). `pythonw.exe` no muestra ventana ni salida: si la tarea falla, el
historial del Programador marca la corrida (código de salida 1 = Telegram no confirmó el
envío); para ver los errores, prueba primero con `python.exe` y `--dry-run`.

## Mudar el bot a otro PC sin perder la medición en vivo

Checklist, en orden. La medición depende de que el hash del prompt congelado
(`PROMPT_SHA256`) siga coincidiendo -- por eso el ORDEN de los pasos 2 y 3
importa (el código primero, los datos después).

1. **Respaldo consistente de la base, con la API de sqlite (nunca copiar el
   archivo directo)**: en modo WAL, un `.db` puede tener escrituras a medio
   confirmar en el `.db-wal` -- copiar solo el `.db` con el Explorador de
   archivos puede dejar una copia inconsistente. Desde el PC viejo:

   ```powershell
   .\.venv\Scripts\python.exe -c "import sqlite3; src = sqlite3.connect('file:data/minerva_estabilidad.db?mode=ro', uri=True); dst = sqlite3.connect('respaldo_migracion.db'); src.backup(dst); dst.close(); src.close()"
   ```

   Llevá `respaldo_migracion.db` al PC nuevo (USB, red, lo que sea).

   Desde la Fase 6 hay una herramienta equivalente que además **verifica** la copia con
   `PRAGMA integrity_check` y no deja a medias un respaldo corrupto:
   `.\.venv\Scripts\python.exe scripts\backup_db.py` (deja `data\backups\minerva_AAAAMMDD_HHMMSS.db`) y, en el PC
   nuevo, `scripts\restore_db.py <respaldo>` en lugar de renombrar el archivo a mano (paso 4).

2. **Mismo commit del repo en el PC nuevo**: `git clone` (o `git pull`) y
   `git log -1` para confirmar que es el MISMO commit que tenía el PC viejo
   al momento del respaldo. Esto es necesario para que `PROMPT_SHA256`
   (calculado desde `app/llm/prompts.py`, nunca editado desde que se congeló
   -- ver `docs/FASE3_6_LLM.md` sección k) coincida exactamente; si el código
   difiere, `frozen_hash_ok` en `/health` saldría `false` y el etiquetado en
   vivo se detendría solo (por diseño, sin romper nada, pero sin medir).

3. **Recrear el entorno**: `uv venv .venv` (o `python -m venv .venv` si no
   usás `uv`) e instalar dependencias dentro de ese venv
   (`.\.venv\Scripts\python.exe -m pip install -e .` o el equivalente con
   `uv pip install -e .`). `start_bot.ps1` usa siempre
   `.\.venv\Scripts\python.exe`, así que el venv debe llamarse `.venv` y
   estar en la raíz del repo.

4. **Copiar `.env` a mano** (nunca por git, nunca pegado en un chat): el
   mismo `.env` del PC viejo. **Ojo con la base**: `start_bot.ps1` ignora el
   `DATABASE_PATH` del `.env` -- fija el suyo propio
   (`data\minerva_estabilidad.db`, relativo a la raíz del repo) en el entorno
   del proceso, y ese valor tiene prioridad sobre el archivo. Por eso el
   respaldo debe quedar en el PC nuevo exactamente como
   `data\minerva_estabilidad.db` (renombrá `respaldo_migracion.db` a ese
   nombre y ponelo en `data\`); cambiar `DATABASE_PATH` en el `.env` no
   cambia qué base abre el bot arrancado con ese script.

5. **Recrear las tres tareas programadas** en el PC nuevo: la alerta de
   Telegram (`schtasks /Create`, ver `scripts/telegram_alert.py`), el
   arranque automático y el resumen diario (`Register-ScheduledTask`, secciones
   de arriba; el resumen diario asume el PC en la zona horaria de Chile).

6. **Arrancar y verificar**:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
   ```

   Confirmá en la salida que `frozen_hash_ok` es `true` -- si es `false`, algo
   quedó desalineado entre el commit del código y el hash que tenía la base
   (volvé al paso 2).

7. **Verificar el avance de la medición con una copia** (nunca contra la base
   de trabajo real, ni en el PC viejo ni en el nuevo -- `measurement_status.py`
   exige explícitamente que `--db` sea una ruta distinta de
   `settings.database_path`):

   ```powershell
   .\.venv\Scripts\python.exe -c "import sqlite3; src = sqlite3.connect('file:data/minerva_estabilidad.db?mode=ro', uri=True); dst = sqlite3.connect('verif.db'); src.backup(dst); dst.close(); src.close()"
   .\.venv\Scripts\python.exe scripts/measurement_status.py --db verif.db
   ```

   Los conteos deberían seguir la misma tendencia que tenías en el PC viejo
   justo antes de la migración (no deberían saltar a cero ni duplicarse).

## Con Docker (Fase 7): qué cambia respecto a todo lo anterior

El bot real arranca con `docker compose up -d` (Docker Desktop abierto, `.env` como **archivo** junto a
`docker-compose.yml` y el hash de la contraseña entre comillas simples) y se reinicia solo
(`restart: unless-stopped`). La base vive en el volumen `minerva_data`, no en `data\`. Tareas del Programador:

| Tarea | Con Docker |
|---|---|
| `MinervaStartBot` | **No aplica** (la reemplaza `restart: unless-stopped`). `migrate_to_docker.ps1` la deshabilita si existe; `rollback_from_docker.ps1` la reactiva. Docker Desktop sí debe iniciar con Windows: `docs/MUDANZA_DOCKER.md` §5 |
| `MinervaHealthAlert` | Sin cambios (consulta `127.0.0.1:8000`) |
| `MinervaDailySummary` | Sin cambios en la tarea: `telegram_daily.py` ve la marca `data\MIGRADO_A_DOCKER.txt` y saca una copia de la base del contenedor (con el contenedor detenido informa igual y dice que el bot no responde). Probarlo sin enviar: `.\.venv\Scripts\python.exe scripts\telegram_daily.py --dry-run` |
| `MinervaBackup` | `scripts\docker_db.py backup --keep 14` en lugar de `backup_db.py` (manual §5.2) |

Para **mudar a otro PC con Docker**: en el PC viejo `docker compose stop` y `scripts\docker_db.py backup`; en el nuevo,
`git clone` del mismo commit, `.env` a mano, `docker compose build`, importar el respaldo con el comando de manual §5.5
y `docker compose up -d`. La mudanza *de nativo a Docker en el mismo PC* tiene su propio script
(`docs/MUDANZA_DOCKER.md`).
