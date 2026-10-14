# Continuidad operativa: tarea de arranque y migración a otro PC

Pieza de continuidad operativa del plan pre-Fase 5 (aprobado 2026-10-14): el
PC actual es de pruebas y se apaga de tanto en tanto; esto deja arrancar el
bot real con un solo comando tras un reinicio, y documenta cómo mudarlo a un
PC 24/7 sin perder la medición en vivo en curso.

## Arrancar y detener el bot real

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_bot.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1
```

`start_bot.ps1` arranca con las variables fijas de hoy (`AUTO_OPEN_WITHOUT_LLM=
false`, `LOG_LEVEL=INFO`, `LLM_LIVE_LABELING_ENABLED=true`), **rehúsa** arrancar
si ya hay algo escuchando en el puerto 8000 (evita dos procesos escribiendo la
misma base), y espera a que `/health` responda antes de devolver el control.
`stop_bot.ps1` detiene solo el proceso que escucha ese puerto y su lanzador,
nunca otros procesos python del sistema. El detalle completo de cómo y por qué
está en el docstring de cada script (`Get-Help .\scripts\start_bot.ps1 -Full`).

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

5. **Recrear las dos tareas programadas** en el PC nuevo: la alerta de
   Telegram (`schtasks /Create`, ver `scripts/telegram_alert.py`) y el
   arranque automático (`Register-ScheduledTask`, sección de arriba).

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
