<#
.SYNOPSIS
    Vuelve del bot en Docker al arranque nativo (start_bot.ps1), copiando la base del volumen a data\
    con la API de backup. Fase 7 (docs\MUDANZA_DOCKER.md). Es el camino de vuelta de
    scripts\migrate_to_docker.ps1.

.DESCRIPTION
    Antes de tocar nada hace comprobaciones SIN efectos; con -DryRun se queda ahi. Luego, tras tu
    confirmacion escrita, en este orden:

        1. copia de seguridad de la base del volumen en el host (con el contenedor corriendo)
        2. detiene el contenedor de forma ordenada (la medicion se pausa aqui)
        3. copia final de la base del volumen (API de backup DENTRO de un contenedor, SHA-256 al
           traerla al host) y comprueba que tiene las MISMAS filas en todas las tablas
        4. la instala en data\minerva_estabilidad.db con restore_db.py; la base nativa vieja NO se
           borra: queda como minerva_estabilidad.db.previo_AAAAMMDD_HHMMSS
        5. quita la marca data\MIGRADO_A_DOCKER.txt (start_bot.ps1 vuelve a poder arrancar)
        6. reactiva la tarea programada si estaba habilitada antes de la mudanza (la marca lo recuerda)
        7. baja y quita el contenedor (los volumenes de Docker se CONSERVAN)
        8. arranca el bot nativo con start_bot.ps1 y espera /health: status ok, frozen_hash_ok true,
           paused false

    SI ALGO FALLA se deshace en orden inverso y el bot vuelve a quedar en Docker, como estaba.

    Ventana segura: igual que la mudanza (minutos :10-:40 de una hora UTC que no sea de cierre de vela
    de 4 h). Si el contenedor YA no mide (Docker roto) y necesitas el bot nativo cuanto antes, -IgnoreWindow
    la salta: no hay etiquetado en vuelo que cortar. Si Docker no arranca del todo, mira
    docs\MUDANZA_DOCKER.md ("Si Docker no arranca").

    Codigos de salida: 0 vuelta completa; 1 no se cambio nada; 2 fallo y el bot Docker quedo como
    estaba; 3 fallo y la restauracion NO se completo (mira el registro).

    Registro: logs\mudanza_docker.log.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_from_docker.ps1 -DryRun

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rollback_from_docker.ps1
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
    [int]$HealthTimeoutSeconds = 300,
    [switch]$DryRun,
    [switch]$IgnoreWindow,
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
    Mutated = $false; ServiceBefore = "absent"; MarkerFields = @{}; MarkerText = ""
    TaskName = "MinervaStartBot"; TaskBefore = "Missing"; TaskNow = "Missing"
}
$script:Completed = $false
$script:RollbackOk = $true

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
    } elseif ($IgnoreWindow) {
        Add-Warning "fuera de la ventana segura ($($window.Reason)); sigue porque pediste -IgnoreWindow"
    } else {
        $next = Get-NextSafeWindow $now
        $detail = ("{0}; la proxima abre a las {1:HH:mm} UTC ({2} hora local). " -f
            $window.Reason, $next, (Format-LocalTime $next))
        Add-Check $false "Ventana segura" ($detail + "Si el contenedor ya no mide, usa -IgnoreWindow")
    }

    $dockerInfo = Invoke-Native $script:DockerExe @("info", "--format", "{{.ServerVersion}}") 60
    $dockerUp = ($dockerInfo.ExitCode -eq 0)
    Add-Check $dockerUp "Docker responde" $(if ($dockerUp) { "motor $($dockerInfo.Stdout.Trim())" } else {
        "abre Docker Desktop y espera a que este listo ($(Get-Tail $dockerInfo.Stderr 1))" })

    $hasMarker = Test-Path -LiteralPath $MarkerPath -PathType Leaf
    Add-Check $hasMarker "El bot esta migrado a Docker" $(if ($hasMarker) { "marca: $MarkerPath" } else {
        "debe existir $MarkerPath (si no existe, no hay mudanza que deshacer)" })
    if ($hasMarker) {
        $script:State.MarkerText = Get-Content -Raw -LiteralPath $MarkerPath
        $script:State.MarkerFields = Read-Marker $MarkerPath
        if ($script:State.MarkerFields.ContainsKey("task_name")) {
            $script:State.TaskName = $script:State.MarkerFields["task_name"]
        }
        if ($script:State.MarkerFields.ContainsKey("task_state_before")) {
            $script:State.TaskBefore = $script:State.MarkerFields["task_state_before"]
        }
    }
    Add-Check (Test-Path -LiteralPath $EnvFileFull -PathType Leaf) "Archivo de credenciales" $EnvFileFull
    $python = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
    Add-Check (Test-Path -LiteralPath $python -PathType Leaf) "Entorno de Python (.venv)" $python

    if ($dockerUp) {
        $volumes = Invoke-Native $script:DockerExe @("volume", "ls", "--format", "{{.Name}}") 60
        $names = @($volumes.Stdout -split "`r?`n" | ForEach-Object { $_.Trim() })
        Add-Check ($names -contains "${ProjectName}_data") "Volumen ${ProjectName}_data existe" `
            "es donde esta la base viva"
        $ps = Invoke-Compose @("ps", "-a", "--format", "json", "minerva") 60 -AllowFail
        $state = "absent"
        if ($ps.Stdout -match '"State"\s*:\s*"(?<state>[a-z]+)"') { $state = $Matches["state"] }
        $script:State.ServiceBefore = $state
        Write-Log "[INFO ] Contenedor del servicio: $state"
        if ($state -eq "running" -and $NativePort -ne $DockerPort) {
            Add-Check (-not (Test-PortListening $NativePort)) "Puerto $NativePort libre para el bot nativo" ""
        }
        if ($state -eq "running") {
            $health = Get-HealthOrNull $DockerPort 8
            Write-Log "[INFO ] /health del contenedor: $(Format-Health $health)"
        }
    }

    if (Test-Path -LiteralPath $DatabaseFull -PathType Leaf) {
        Write-Log "[INFO ] La base nativa vieja ($DatabaseFull) se conservara como *.previo_<fecha>"
    }
    $script:State.TaskNow = Get-TaskState $script:State.TaskName
    Write-Log ("[INFO ] Tarea '{0}': antes de la mudanza {1}, ahora {2}" -f $script:State.TaskName,
        $script:State.TaskBefore, $script:State.TaskNow)
}


# ===== la vuelta atras ====================================================================================

function Invoke-Rollback {
    $dockerDb = Join-Path $script:RepoRoot "scripts\docker_db.py"

    Write-Log "PASO 1/8: copia de seguridad de la base del volumen en el host"
    $pre = Invoke-Python @($dockerDb, "export", "--out-dir", $BackupDirFull, "--prefix", "pre_rollback") 900
    if ($pre.ExitCode -ne 0) { throw "la copia de seguridad del volumen fallo: $(Get-Tail $pre.Stderr)" }
    Write-Log "    $($pre.Stdout.Trim())"

    Write-Log "PASO 2/8: detiene el contenedor de forma ordenada (desde aqui la medicion esta en pausa)"
    $script:State.Mutated = $true
    if ($script:State.ServiceBefore -eq "running") {
        Push-Undo "volver a arrancar el contenedor" {
            [void](Invoke-Compose @("up", "-d") 900)
            $health = Wait-HealthGood $DockerPort $HealthTimeoutSeconds
            if (-not $health.Ok) { throw "el contenedor no quedo sano al volver: $($health.Reason)" }
        }
        [void](Invoke-Compose @("stop") 180)
        Write-Log "    contenedor detenido"
    } else {
        Write-Log "    el contenedor no estaba corriendo ($($script:State.ServiceBefore))"
    }

    Write-Log "PASO 3/8: copia final de la base del volumen (contenedor detenido) y su huella"
    $final = Invoke-Python @($dockerDb, "export", "--out-dir", $BackupDirFull, "--prefix", "rollback") 900
    if ($final.ExitCode -ne 0) { throw "la copia final del volumen fallo: $(Get-Tail $final.Stderr)" }
    $copy = $final.Stdout.Trim().Split("`n")[-1].Trim()
    Write-Log "    copia final: $copy"
    $copyPrint = Get-HostFingerprint $copy
    $volumePrint = Get-VolumeFingerprint
    $diffs = @(Compare-Fingerprint $volumePrint $copyPrint)
    if ($diffs.Count -gt 0) { throw "la copia no es igual a la base del volumen: $($diffs -join '; ')" }
    Write-Log "    integridad ok; $(Format-KeyCounts $volumePrint)"

    Write-Log "PASO 4/8: instala la copia en $DatabaseFull (la base vieja se conserva como *.previo_*)"
    $restore = Invoke-Python @((Join-Path $script:RepoRoot "scripts\restore_db.py"), $copy,
        "--db", $DatabaseFull, "--force") 900
    foreach ($line in ($restore.Stdout -split "`r?`n")) { if ($line.Trim()) { Write-Log "    restore_db: $line" } }
    if ($restore.ExitCode -ne 0) { throw "restore_db.py fallo (codigo $($restore.ExitCode)): $(Get-Tail $restore.Stderr)" }
    $installed = Get-HostFingerprint $DatabaseFull
    $diffs = @(Compare-Fingerprint $volumePrint $installed)
    if ($diffs.Count -gt 0) { throw "la base instalada no es igual a la del volumen: $($diffs -join '; ')" }
    Write-Log "    base nativa instalada: $(Format-KeyCounts $installed) -> IGUAL al volumen"

    Write-Log "PASO 5/8: quita la marca de la mudanza"
    Push-Undo "volver a escribir la marca de la mudanza" {
        Set-Content -LiteralPath $MarkerPath -Value $script:State.MarkerText -Encoding ASCII
    }
    Remove-Item -LiteralPath $MarkerPath -Force

    Write-Log "PASO 6/8: reactiva la tarea '$($script:State.TaskName)' si estaba habilitada antes"
    if ($script:State.TaskBefore -eq "Enabled" -and $script:State.TaskNow -eq "Disabled") {
        Push-Undo "volver a deshabilitar la tarea '$($script:State.TaskName)'" {
            Set-TaskEnabled $script:State.TaskName $false
        }
        Set-TaskEnabled $script:State.TaskName $true
        Write-Log "    tarea reactivada"
    } else {
        Write-Log "    nada que reactivar (antes: $($script:State.TaskBefore), ahora: $($script:State.TaskNow))"
    }

    Write-Log "PASO 7/8: baja y quita el contenedor (los volumenes de Docker se conservan)"
    if ($script:State.ServiceBefore -ne "absent") {
        [void](Invoke-Compose @("down") 300)
    }
    if (-not (Wait-PortFree $NativePort 45)) { throw "el puerto $NativePort sigue ocupado despues de bajar Docker" }

    Write-Log "PASO 8/8: arranca el bot nativo (start_bot.ps1) y espera /health"
    Push-Undo "detener el bot nativo" { Stop-NativeBot $NativePort }
    Start-NativeBot $NativePort $DatabaseFull $LogDirFull
    $result = Wait-HealthGood $NativePort $HealthTimeoutSeconds
    if (-not $result.Ok) { throw "el bot nativo no quedo sano: $($result.Reason)" }
    Write-Log "    $(Format-Health $result.Health)"
    $script:FinalHealth = $result.Health
}


# ===== principal =============================================================================================

Write-Log "=== rollback_from_docker.ps1 ($(if ($DryRun) { 'ENSAYO' } else { 'REAL' })) | proyecto=$ProjectName nativo=:$NativePort docker=:$DockerPort ==="
Invoke-Preflight

if ($script:Failures.Count -gt 0) {
    Write-Log "NO SE CAMBIO NADA: $($script:Failures.Count) comprobacion(es) fallaron:"
    foreach ($failure in $script:Failures) { Write-Log "  - $failure" }
    exit 1
}
if ($DryRun) {
    Write-Log "ENSAYO TERMINADO: todas las comprobaciones pasan. No se cambio nada."
    exit 0
}
if (-not $Yes) {
    Write-Host ""
    Write-Host "Se va a DETENER el contenedor y a volver al bot nativo (:$NativePort). Dura 2 a 4 minutos."
    $answer = Read-Host "Escribe VOLVER para continuar (cualquier otra cosa cancela)"
    if ($null -eq $answer -or $answer.Trim() -ne "VOLVER") {
        Write-Log "Cancelado (no se escribio VOLVER; sin una terminal interactiva usa -Yes). No se cambio nada."
        exit 1
    }
}

try {
    Invoke-Rollback
    $script:Completed = $true
} catch {
    Write-Log "FALLO: $($_.Exception.Message)"
} finally {
    if (-not $script:Completed -and $script:UndoStack.Count -gt 0) {
        Write-Log "Se deshace lo hecho para dejar el bot en Docker como estaba..."
        $script:RollbackOk = Invoke-Undos
    }
}

if ($script:Completed) {
    Write-Log "============================================================"
    Write-Log "VUELTA COMPLETA: el bot nativo corre en http://127.0.0.1:$NativePort/"
    Write-Log "  /health      : $(Format-Health $script:FinalHealth)"
    Write-Log "  base nativa  : $DatabaseFull (la vieja quedo como *.previo_*)"
    Write-Log "  volumenes de Docker conservados; cuando estes seguro, para borrarlos:"
    Write-Log "    docker volume rm ${ProjectName}_data ${ProjectName}_logs"
    Write-Log "============================================================"
    exit 0
}
if (-not $script:State.Mutated) {
    Write-Log "No se cambio nada del sistema."
    exit 1
}
if ($script:RollbackOk) {
    Write-Log "El bot quedo en Docker, como estaba. Revisa el registro y reintenta."
    exit 2
}
Write-Log "ATENCION: lo que se deshizo NO termino. Mira las lineas 'FALLO al deshacer' de arriba y"
Write-Log "docs\MUDANZA_DOCKER.md, seccion 'Si algo queda a medias'."
exit 3
