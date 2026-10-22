<#
.SYNOPSIS
    Muda el bot REAL de Minerva del arranque nativo (start_bot.ps1) a Docker, con verificacion y
    vuelta atras automatica si algo falla. Fase 7 (docs\MUDANZA_DOCKER.md).

.DESCRIPTION
    Antes de tocar nada hace comprobaciones SIN efectos; con -DryRun se queda ahi (ensayo). Luego,
    tras tu confirmacion escrita, en este orden:

        1. respaldo de seguridad de la base nativa (con el bot corriendo; API de backup, solo lectura)
        2. construye la imagen de Docker (el bot sigue midiendo mientras tanto)
        3. deshabilita la tarea programada que arranca el bot nativo (si existe)
        4. detiene el bot nativo con stop_bot.ps1            <- aqui empieza la pausa de la medicion
        5. respaldo definitivo + huella de la base (integridad y filas por tabla)
        6. copia ese respaldo al volumen de Docker con la API de backup (restore_db.py)
        7. verifica el volumen: PRAGMA integrity_check y las MISMAS filas en TODAS las tablas
           (shadow_trades, llm_logs, paper_execution_log y el resto) antes y despues
        8. escribe la marca data\MIGRADO_A_DOCKER.txt (start_bot.ps1 se niega a arrancar el nativo)
        9. levanta docker compose en 127.0.0.1:<DockerPort> con los mismos interruptores que
           start_bot.ps1 y restart: unless-stopped
       10. espera /health y comprueba status ok, frozen_hash_ok true y paused false

    SI ALGO FALLA a partir del paso 3 (o pulsas Ctrl+C) se DESHACE todo en orden inverso: baja el
    contenedor, quita la marca, borra el volumen si el contenedor nunca arranco, vuelve a arrancar el
    bot nativo con la base de antes (que esta mudanza solo LEE, nunca modifica) y reactiva la tarea.

    Ventana segura: se niega a correr fuera de los minutos :10-:40 de una hora UTC que no sea de
    cierre de vela de 4 h (00, 04, 08, 12, 16, 20 UTC): ahi las filas de la medicion ya no estan en
    vuelo. Durante la mudanza la medicion se detiene unos 2 a 4 minutos, y recibiras el aviso de caida
    y el de recuperacion de MinervaHealthAlert.

    Codigos de salida: 0 mudanza completa; 1 no se cambio nada (comprobaciones, confirmacion o fallo
    antes del paso 3); 2 fallo y la vuelta atras al bot nativo se completo; 3 fallo y la vuelta atras
    NO se completo (el registro dice que falta; docs\MUDANZA_DOCKER.md, "Si algo queda a medias").

    Registro: logs\mudanza_docker.log.

    Parametros SOLO para ensayar sin tocar el bot real: -NativePort, -DockerPort, -DatabasePath,
    -LogDir, -BackupDir, -ProjectName, -EnvFile y -TaskName. Con un -DockerPort distinto de 8000 el
    contenedor arranca como instancia de PRUEBA (sin etiquetado del LLM, Telegram, brazo, noticias ni
    push, y sin reinicio automatico), igual que start_bot.ps1 con un puerto distinto de 8000.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1 -DryRun
    Ensayo: solo comprueba y cuenta que haria. No cambia nada.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_to_docker.ps1
    La mudanza real (pide que escribas MIGRAR).
#>
[CmdletBinding()]
param(
    [int]$NativePort = 8000,
    [int]$DockerPort = 8000,
    [string]$DatabasePath = "data/minerva_estabilidad.db",
    [string]$LogDir = "logs",
    [string]$BackupDir = "",
    [string]$ProjectName = "minerva",
    [string]$EnvFile = ".env",
    [string]$TaskName = "MinervaStartBot",
    [int]$HealthTimeoutSeconds = 300,
    [switch]$DryRun,
    [switch]$ReplaceVolume,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "docker_common.ps1")
Set-Location $script:RepoRoot
$script:ComposeProject = $ProjectName

$DatabaseFull = Resolve-RepoPath $DatabasePath
$LogDirFull = Resolve-RepoPath $LogDir
$EnvFileFull = Resolve-RepoPath $EnvFile
$BackupDirFull = if ($BackupDir) { Resolve-RepoPath $BackupDir } else {
    Join-Path (Split-Path -Parent $DatabaseFull) "backups"
}
$MarkerPath = Get-MarkerPath $DatabaseFull
$IsRealBot = ($DockerPort -eq 8000)

Initialize-Log (Join-Path $LogDirFull "mudanza_docker.log")

$script:State = @{
    Mutated = $false; ContainerStarted = $false; NativeWasRunning = $false; TaskBefore = "Missing"
    VolumesExist = $false
}
$script:Completed = $false
$script:RollbackOk = $true

# Lo que ven los comandos de Docker (el compose lee estas variables).
$env:MINERVA_ENV_FILE = $EnvFileFull
$env:MINERVA_PORT = "$DockerPort"
$env:MINERVA_REAL_BOT = if ($IsRealBot) { "true" } else { "false" }
$env:MINERVA_RESTART = if ($IsRealBot) { "unless-stopped" } else { "no" }
$env:COMPOSE_PROJECT_NAME = $ProjectName


# ===== comprobaciones previas: NO cambian nada ===========================================================

function Invoke-Preflight {
    $now = Get-UtcNow
    $window = Test-SafeWindow $now
    if ($window.Ok) {
        Add-Check $true "Ventana segura" ("{0:HH:mm} UTC ({1} hora local)" -f $now, (Format-LocalTime $now))
    } else {
        $next = Get-NextSafeWindow $now
        Add-Check $false "Ventana segura" ("{0}; la proxima abre a las {1:HH:mm} UTC ({2} hora local)" -f
            $window.Reason, $next, (Format-LocalTime $next))
    }

    $dockerInfo = Invoke-Native $script:DockerExe @("info", "--format", "{{.ServerVersion}}") 60
    $dockerUp = ($dockerInfo.ExitCode -eq 0)
    Add-Check $dockerUp "Docker responde" $(if ($dockerUp) { "motor $($dockerInfo.Stdout.Trim())" } else {
        "abre Docker Desktop y espera a que este listo ($(Get-Tail $dockerInfo.Stderr 1))" })

    $envFileOk = Test-Path -LiteralPath $EnvFileFull -PathType Leaf
    Add-Check $envFileOk "Archivo de credenciales" $(if ($envFileOk) { "existe: $EnvFileFull" } else {
        "$EnvFileFull debe existir COMO ARCHIVO (si falta, Docker Desktop crea una carpeta y el bot arrancaria sin credenciales)" })
    $python = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
    Add-Check (Test-Path -LiteralPath $python -PathType Leaf) "Entorno de Python (.venv)" $python
    Add-Check (Test-Path -LiteralPath $DatabaseFull -PathType Leaf) "Base nativa" $DatabaseFull
    $notMigrated = -not (Test-Path -LiteralPath $MarkerPath)
    Add-Check $notMigrated "Aun no migrado" $(if ($notMigrated) { "no hay marca de mudanza" } else {
        "existe ${MarkerPath}: el bot ya corre en Docker" })

    if (Test-Path -LiteralPath $python -PathType Leaf) {
        $hash = Invoke-Python @(
            "-c", ("import sys; from app.llm.prompts import PROMPT_SHA256; " +
                "from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256; " +
                "sys.exit(0 if PROMPT_SHA256 == FROZEN_PROMPT_SHA256 else 1)")
        ) 120
        Add-Check ($hash.ExitCode -eq 0) "Prompt congelado intacto en este codigo" $(if ($hash.ExitCode -eq 0) {
            "el hash del arbol de trabajo es el congelado" } else {
            "el hash del prompt del arbol de trabajo NO es el congelado (el contenedor no mediria): git status" })
    }

    $listening = Test-PortListening $NativePort
    $script:State.NativeWasRunning = $listening
    if ($listening) {
        $health = Get-HealthOrNull $NativePort 8 2
        if ($null -eq $health) {
            Add-Check $false "Bot nativo en el puerto $NativePort" `
                "algo escucha pero no responde /health como Minerva: no lo toco"
        } elseif ($health.control.paused -eq $true) {
            Add-Check $false "Bot nativo en el puerto $NativePort" `
                "la medicion esta PAUSADA (/health: control.paused): reanudala (/resume) antes de mudar"
        } else {
            Add-Check $true "Bot nativo en el puerto $NativePort" (Format-Health $health)
            if ($health.llm_labeling.frozen_hash_ok -eq $false) {
                Add-Warning "el bot nativo ya NO etiqueta (hash congelado distinto): la mudanza no lo arregla"
            }
        }
    } else {
        Add-Warning ("no hay bot nativo corriendo en el puerto ${NativePort}: no hay nada que detener; " +
            "se muda la base tal como esta en $DatabaseFull")
    }
    if ($NativePort -ne $DockerPort) {
        Add-Check (-not (Test-PortListening $DockerPort)) "Puerto $DockerPort libre para Docker" ""
    }

    if ($dockerUp) {
        $config = Invoke-Compose @("config", "--quiet") 60 -AllowFail
        Add-Check ($config.ExitCode -eq 0) "docker-compose.yml valido" (Get-Tail $config.Stderr 2)
        $existing = Invoke-Compose @("ps", "-a", "-q") 60 -AllowFail
        $noContainers = [string]::IsNullOrWhiteSpace($existing.Stdout)
        Add-Check $noContainers "Sin contenedores del proyecto '$ProjectName'" $(if ($noContainers) { "ninguno" } else {
            "ya existe uno: bajalo con docker compose down" })
        $volumes = Invoke-Native $script:DockerExe @("volume", "ls", "--format", "{{.Name}}") 60
        $names = @($volumes.Stdout -split "`r?`n" | ForEach-Object { $_.Trim() })
        $script:State.VolumesExist = [bool](($names -contains "${ProjectName}_data") -or
            ($names -contains "${ProjectName}_logs"))
        if ($script:State.VolumesExist -and -not $ReplaceVolume) {
            Add-Check $false "Volumenes de Docker libres" ("ya existen ${ProjectName}_data/${ProjectName}_logs: " +
                "esta mudanza no pisa datos; si son de un intento anterior, repite con -ReplaceVolume " +
                "(guarda antes una copia de seguridad en el host)")
        } else {
            Add-Check $true "Volumenes de Docker libres" $(if ($script:State.VolumesExist) {
                "existen y se reemplazaran (-ReplaceVolume), con copia de seguridad previa" } else { "no existen" })
        }
    }

    if (Test-Path -LiteralPath $DatabaseFull -PathType Leaf) {
        $needed = [int64]((Get-Item -LiteralPath $DatabaseFull).Length * 3 + 500MB)
        $drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($BackupDirFull).Substring(0, 1)) `
            -ErrorAction SilentlyContinue
        if ($drive) {
            Add-Check ($drive.Free -ge $needed) "Espacio en disco para los respaldos" `
                ("{0:N1} GB libres, hacen falta ~{1:N1} GB" -f ($drive.Free / 1GB), ($needed / 1GB))
        }
    }

    $taskState = Get-TaskState $TaskName
    $script:State.TaskBefore = $taskState
    switch ($taskState) {
        "Enabled" { Write-Log "[INFO ] Tarea '$TaskName': habilitada; se deshabilitara (el bot nativo ya no debe arrancar solo)" }
        "Disabled" { Write-Log "[INFO ] Tarea '$TaskName': ya estaba deshabilitada" }
        default { Write-Log "[INFO ] Tarea '$TaskName': no existe en este PC; no hay nada que deshabilitar" }
    }

    foreach ($warning in (Get-DockerAutostartWarnings)) { Add-Warning $warning }
    foreach ($warning in (Get-EnvQuotingWarnings $EnvFileFull)) { Add-Warning $warning }
    $git = Invoke-Native "git" @("-C", $script:RepoRoot, "status", "--porcelain", "--", "app", "scripts",
        "docker", "Dockerfile", "docker-compose.yml", ".dockerignore") 60
    if ($git.ExitCode -eq 0 -and $git.Stdout.Trim()) {
        Add-Warning ("hay cambios sin commitear en el codigo: la imagen se construye con el arbol de " +
            "trabajo tal cual (git status)")
    }
}


# ===== la mudanza ============================================================================================

function Invoke-Migration {
    if ($ReplaceVolume -and $script:State.VolumesExist) {
        Write-Log "PASO 0: -ReplaceVolume: copia de seguridad del volumen existente y borrado"
        $saved = Invoke-Python @((Join-Path $script:RepoRoot "scripts\docker_db.py"), "export",
            "--out-dir", $BackupDirFull, "--prefix", "volumen_reemplazado") 900
        if ($saved.ExitCode -ne 0) {
            throw "no se pudo guardar una copia del volumen existente (no se borra nada): $(Get-Tail $saved.Stderr)"
        }
        Write-Log "    copia de seguridad del volumen: $($saved.Stdout.Trim())"
        [void](Invoke-Compose @("down", "-v") 300)
    }

    Write-Log "PASO 1/10: respaldo de seguridad de la base nativa (con el bot corriendo, solo lectura)"
    $pre = Invoke-Python @((Join-Path $script:RepoRoot "scripts\backup_db.py"), "--db", $DatabaseFull,
        "--out-dir", $BackupDirFull, "--prefix", "pre_docker") 900
    if ($pre.ExitCode -ne 0) { throw "el respaldo previo fallo: $(Get-Tail ($pre.Stderr + ' ' + $pre.Stdout))" }
    Write-Log "    $(Get-Tail $pre.Stdout 2)"

    Write-Log "PASO 2/10: construye la imagen de Docker (el bot nativo sigue midiendo)"
    [void](Invoke-Compose @("build") 1800)

    $window = Test-SafeWindow (Get-UtcNow)
    if (-not $window.Ok) { throw "la ventana segura se cerro mientras se preparaba la mudanza: $($window.Reason)" }

    Write-Log "PASO 3/10: deshabilita la tarea '$TaskName' (si existe y esta habilitada)"
    if ($script:State.TaskBefore -eq "Enabled") {
        Set-TaskEnabled $TaskName $false   # si falla (p. ej. Acceso denegado) aun no se cambio nada
        $script:State.Mutated = $true
        Push-Undo "reactivar la tarea '$TaskName'" { Set-TaskEnabled $TaskName $true }
        if ((Get-TaskState $TaskName) -ne "Disabled") { throw "la tarea '$TaskName' sigue habilitada" }
        Write-Log "    tarea deshabilitada"
    } else {
        Write-Log "    nada que deshabilitar ($($script:State.TaskBefore))"
    }

    Write-Log "PASO 4/10: detiene el bot nativo (desde aqui la medicion esta en pausa)"
    $script:State.Mutated = $true
    if ($script:State.NativeWasRunning) {
        Push-Undo "volver a arrancar el bot nativo (start_bot.ps1)" {
            if (-not (Test-PortListening $NativePort)) {
                if (-not (Wait-PortFree $NativePort 45)) { throw "el puerto $NativePort sigue ocupado" }
                Start-NativeBot $NativePort $DatabaseFull $LogDirFull
            }
        }
        Stop-NativeBot $NativePort
        Write-Log "    bot nativo detenido; puerto $NativePort libre"
    } else {
        Write-Log "    el bot nativo no estaba corriendo"
    }

    Write-Log "PASO 5/10: respaldo definitivo con el bot detenido y su huella"
    $final = Invoke-Python @((Join-Path $script:RepoRoot "scripts\backup_db.py"), "--db", $DatabaseFull,
        "--out-dir", $BackupDirFull, "--prefix", "migracion") 900
    if ($final.ExitCode -ne 0) { throw "el respaldo definitivo fallo: $(Get-Tail ($final.Stderr + ' ' + $final.Stdout))" }
    $match = [regex]::Match($final.Stdout, "(?m)^Respaldo: (?<path>.+?) \(")
    if (-not $match.Success) { throw "backup_db.py no informo la ruta del respaldo" }
    $staged = $match.Groups["path"].Value
    Write-Log "    respaldo definitivo: $staged"
    $sourcePrint = Get-HostFingerprint $DatabaseFull
    $stagedPrint = Get-HostFingerprint $staged
    $diffs = @(Compare-Fingerprint $sourcePrint $stagedPrint)
    if ($diffs.Count -gt 0) { throw "el respaldo no es igual a la base de origen: $($diffs -join '; ')" }
    Write-Log "    integridad ok; $(Format-KeyCounts $sourcePrint)"

    Write-Log "PASO 6/10: copia el respaldo al volumen de Docker con la API de backup (restore_db.py)"
    Push-Undo "borrar el volumen creado por esta mudanza (solo si el contenedor nunca arranco)" {
        if (-not $script:State.ContainerStarted) { [void](Invoke-Compose @("down", "-v") 300 -AllowFail) }
        else { Write-Log "    el contenedor llego a arrancar: el volumen se CONSERVA (tiene datos del bot)" }
    }
    $stagedDocker = ConvertTo-DockerPath $staged
    [void](Invoke-Compose @("run", "--rm", "--no-deps", "-T", "-v", "${stagedDocker}:/in/respaldo.db:ro",
        "minerva", "python", "scripts/restore_db.py", "/in/respaldo.db") 900)
    Write-Log "    base copiada al volumen"

    Write-Log "PASO 7/10: verifica el volumen (integridad y filas de TODAS las tablas, antes y despues)"
    $volumePrint = Get-VolumeFingerprint
    $diffs = @(Compare-Fingerprint $sourcePrint $volumePrint)
    if ($diffs.Count -gt 0) { throw "el volumen NO coincide con la base de origen: $($diffs -join '; ')" }
    Write-Log "    origen : $(Format-KeyCounts $sourcePrint)"
    Write-Log "    volumen: $(Format-KeyCounts $volumePrint)  -> IGUALES ($($volumePrint.tables.PSObject.Properties.Name.Count) tablas, integridad ok)"

    Write-Log "PASO 8/10: escribe la marca de la mudanza ($MarkerPath)"
    Push-Undo "quitar la marca de la mudanza" { Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue }
    Write-Marker $MarkerPath @{
        migrated_at_utc = (Get-UtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ")
        project = $ProjectName; docker_port = "$DockerPort"; native_port = "$NativePort"
        task_name = $TaskName; task_state_before = $script:State.TaskBefore
        backup = $staged
    }

    Write-Log "PASO 9/10: levanta docker compose en 127.0.0.1:$DockerPort (instancia real: $IsRealBot)"
    Push-Undo "bajar y quitar el contenedor" {
        [void](Invoke-Compose @("down") 300 -AllowFail)
        if (-not (Wait-PortFree $DockerPort 45)) { Write-Log "    aviso: el puerto $DockerPort sigue ocupado" }
    }
    [void](Invoke-Compose @("up", "-d") 900)
    # Solo desde aqui el contenedor pudo escribir en el volumen: si `up` falla, el volumen recien
    # creado (una copia de lo que el bot nativo aun tiene) se puede borrar sin perder nada.
    $script:State.ContainerStarted = $true

    Write-Log "PASO 10/10: espera /health (maximo $HealthTimeoutSeconds s): status ok, frozen_hash_ok true, paused false"
    $result = Wait-HealthGood $DockerPort $HealthTimeoutSeconds
    if (-not $result.Ok) { throw "el contenedor no quedo sano: $($result.Reason)" }
    Write-Log "    $(Format-Health $result.Health)"
    $script:FinalHealth = $result.Health
}

function Show-Summary {
    Write-Log "============================================================"
    Write-Log "MUDANZA COMPLETA: el bot corre en Docker en http://127.0.0.1:$DockerPort/"
    Write-Log "  /health        : $(Format-Health $script:FinalHealth)"
    Write-Log "  base del bot   : volumen '${ProjectName}_data' (la de $DatabaseFull queda como copia vieja)"
    Write-Log "  respaldos      : $BackupDirFull (pre_docker_* y migracion_*)"
    Write-Log "  reinicio       : $($env:MINERVA_RESTART) (Docker Desktop debe iniciar con Windows)"
    Write-Log "Siguientes pasos (docs\MUDANZA_DOCKER.md): abre el dashboard, mira"
    Write-Log "  docker compose ps ; docker compose logs -f minerva"
    Write-Log "y vuelve atras cuando quieras con scripts\rollback_from_docker.ps1."
    Write-Log "============================================================"
}


# ===== principal =============================================================================================

Write-Log "=== migrate_to_docker.ps1 ($(if ($DryRun) { 'ENSAYO' } else { 'REAL' })) | proyecto=$ProjectName nativo=:$NativePort docker=:$DockerPort ==="
Invoke-Preflight

if ($script:Failures.Count -gt 0) {
    Write-Log "NO SE CAMBIO NADA: $($script:Failures.Count) comprobacion(es) fallaron:"
    foreach ($failure in $script:Failures) { Write-Log "  - $failure" }
    exit 1
}
if ($DryRun) {
    Write-Log "ENSAYO TERMINADO: todas las comprobaciones pasan. No se cambio nada."
    Write-Log "Para mudar de verdad, dentro de la ventana segura: scripts\migrate_to_docker.ps1"
    exit 0
}
if (-not $Yes) {
    Write-Host ""
    Write-Host "Se va a DETENER el bot nativo (:$NativePort) y a pasar la medicion a Docker. Dura 2 a 4 minutos."
    $answer = Read-Host "Escribe MIGRAR para continuar (cualquier otra cosa cancela)"
    if ($null -eq $answer -or $answer.Trim() -ne "MIGRAR") {
        Write-Log "Cancelado (no se escribio MIGRAR; sin una terminal interactiva usa -Yes). No se cambio nada."
        exit 1
    }
}

try {
    Invoke-Migration
    $script:Completed = $true
} catch {
    Write-Log "FALLO: $($_.Exception.Message)"
} finally {
    if (-not $script:Completed -and $script:UndoStack.Count -gt 0) {
        Write-Log "Se deshace lo hecho para dejar el sistema como estaba..."
        $script:RollbackOk = Invoke-Undos
    }
}

if ($script:Completed) {
    Show-Summary
    exit 0
}
if (-not $script:State.Mutated) {
    Write-Log "No se cambio nada del sistema (el fallo fue antes de detener o deshabilitar algo)."
    exit 1
}
if ($script:RollbackOk) {
    Write-Log "VUELTA ATRAS COMPLETA: el bot nativo volvio a como estaba. Revisa el registro y reintenta."
    exit 2
}
Write-Log "ATENCION: la vuelta atras NO termino. Mira las lineas 'FALLO al deshacer' de arriba y"
Write-Log "docs\MUDANZA_DOCKER.md, seccion 'Si algo queda a medias'."
exit 3
