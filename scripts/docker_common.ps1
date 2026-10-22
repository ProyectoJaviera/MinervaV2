# docker_common.ps1 -- funciones compartidas por migrate_to_docker.ps1 y rollback_from_docker.ps1
# (Fase 7, docs/MUDANZA_DOCKER.md). NO se ejecuta solo: se carga con
#     . (Join-Path $PSScriptRoot "docker_common.ps1")
#
# Solo ASCII: Windows PowerShell 5.1 lee los .ps1 sin BOM como ANSI. Compatible con 5.1 (sin ??, sin
# operador ternario, sin &&).
#
# Variables de entorno que SOLO existen para las pruebas (las pruebas ponen un `docker`, un `schtasks`
# y unos start/stop del bot falsos; en el uso real no se definen):
#     MINERVA_DOCKER_EXE, MINERVA_SCHTASKS_EXE, MINERVA_STOP_BOT_SCRIPT, MINERVA_START_BOT_SCRIPT,
#     MINERVA_ASSUME_NOW_UTC (hora UTC fingida, formato ISO, p. ej. 2026-10-18T11:20:00Z).

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:LogFile = $null
$script:UndoStack = New-Object System.Collections.ArrayList
$script:DockerExe = if ($env:MINERVA_DOCKER_EXE) { $env:MINERVA_DOCKER_EXE } else { "docker" }
$script:SchtasksExe = if ($env:MINERVA_SCHTASKS_EXE) { $env:MINERVA_SCHTASKS_EXE } else { "schtasks" }
$script:ComposeFile = Join-Path $script:RepoRoot "docker-compose.yml"
$script:ComposeVars = Join-Path $script:RepoRoot "docker\compose.vars"
# OJO: NO llamar a esta variable ProjectName: en el ambito del script seria LA MISMA que el parametro
# -ProjectName de migrate/rollback (este archivo se carga con punto) y lo pisaria.
$script:ComposeProject = "minerva"
$script:MarkerName = "MIGRADO_A_DOCKER.txt"
$script:FourHourCloseHours = @(0, 4, 8, 12, 16, 20)


# --- registro ------------------------------------------------------------------------------------

function Initialize-Log([string]$Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $script:LogFile = $Path
}

function Write-Log([string]$Message) {
    $line = "{0} | {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    if ($script:LogFile) { Add-Content -Path $script:LogFile -Value $line -Encoding utf8 }
    Write-Host $line
}

$script:Failures = New-Object System.Collections.ArrayList

# Una comprobacion previa: se registra y, si falla, se acumula (nada se cambia hasta que todas pasen).
function Add-Check([bool]$Ok, [string]$Name, [string]$Detail = "") {
    $tag = if ($Ok) { "[ OK  ]" } else { "[FALLA]" }
    $suffix = if ($Detail) { " -- $Detail" } else { "" }
    Write-Log ("{0} {1}{2}" -f $tag, $Name, $suffix)
    if (-not $Ok) { [void]$script:Failures.Add("$Name$suffix") }
}

function Add-Warning([string]$Text) { Write-Log "[AVISO] $Text" }

function Resolve-RepoPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $script:RepoRoot $Path)
}

function Get-Tail([string]$Text, [int]$Lines = 3) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return "" }
    $all = @($Text.Trim() -split "`r?`n" | Where-Object { $_.Trim() -ne "" })
    return (($all | Select-Object -Last $Lines) -join " | ")
}


# --- ejecutar programas nativos --------------------------------------------------------------------

# Cada argumento entre comillas si hace falta (reglas de CommandLineToArgvW). Windows PowerShell 5.1
# une -ArgumentList con espacios SIN comillar, asi que se hace aqui.
function ConvertTo-ArgString([string[]]$Items) {
    $parts = foreach ($item in $Items) {
        if ($item -eq "") { '""' }
        elseif ($item -match '[\s"]') {
            $escaped = $item -replace '(\\*)"', '$1$1\"'
            $escaped = $escaped -replace '(\\+)$', '$1$1'
            '"' + $escaped + '"'
        } else { $item }
    }
    return ($parts -join " ")
}

# Ejecuta `$Exe $ArgList` SIN usar el flujo de errores de PowerShell (un `docker` que escribe en
# stderr no debe tumbar el script), con tiempo maximo. Devuelve ExitCode, Stdout y Stderr.
# Siempre con `-WindowStyle Hidden`: ningun programa que lance esta mudanza (python, docker, git,
# schtasks, powershell) debe abrir una ventana de consola, ni siquiera un instante.
function Invoke-Native {
    param([string]$Exe, [string[]]$ArgList = @(), [int]$TimeoutSec = 600)
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $startArgs = @{
            FilePath = $Exe; WindowStyle = "Hidden"; PassThru = $true
            RedirectStandardOutput = $outFile; RedirectStandardError = $errFile
        }
        if ($ArgList.Count -gt 0) { $startArgs.ArgumentList = (ConvertTo-ArgString $ArgList) }
        $process = Start-Process @startArgs
        $null = $process.Handle   # sin esto ExitCode queda vacio en Windows PowerShell 5.1
        if (-not $process.WaitForExit($TimeoutSec * 1000)) {
            Start-Process -FilePath "taskkill.exe" -ArgumentList @("/PID", "$($process.Id)", "/T", "/F") `
                -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue
            return [pscustomobject]@{
                ExitCode = 124; Stdout = ""; Stderr = "TIEMPO AGOTADO tras $TimeoutSec s: $Exe"
                TimedOut = $true
            }
        }
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = [System.IO.File]::ReadAllText($outFile)
            Stderr = [System.IO.File]::ReadAllText($errFile)
            TimedOut = $false
        }
    } finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

# `docker compose` del proyecto: archivo del repo, nombre de proyecto y variables SIN secretos
# (--env-file docker\compose.vars: Compose no lee ni interpola el .env real).
function Invoke-Compose {
    param([string[]]$ComposeArgs, [int]$TimeoutSec = 900, [switch]$AllowFail)
    $base = @("compose", "--progress", "quiet", "-f", $script:ComposeFile, "-p", $script:ComposeProject)
    if (Test-Path -LiteralPath $script:ComposeVars -PathType Leaf) {
        $base += @("--env-file", $script:ComposeVars)
    }
    $result = Invoke-Native $script:DockerExe ($base + $ComposeArgs) $TimeoutSec
    if ($result.ExitCode -ne 0 -and -not $AllowFail) {
        throw ("docker compose $($ComposeArgs[0]) fallo (codigo $($result.ExitCode)): " +
            (Get-Tail ($result.Stderr + " " + $result.Stdout)))
    }
    return $result
}

function Invoke-Python {
    param([string[]]$ArgList, [int]$TimeoutSec = 600)
    $python = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
    return (Invoke-Native $python $ArgList $TimeoutSec)
}

function ConvertTo-DockerPath([string]$Path) {
    # Docker Desktop acepta rutas de Windows con barras normales en -v (C:/ruta/archivo)
    return ([System.IO.Path]::GetFullPath($Path) -replace "\\", "/")
}


# --- ventana segura ----------------------------------------------------------------------------------

function Get-UtcNow {
    if ($env:MINERVA_ASSUME_NOW_UTC) {
        return [datetime]::Parse(
            $env:MINERVA_ASSUME_NOW_UTC, [System.Globalization.CultureInfo]::InvariantCulture,
            ([System.Globalization.DateTimeStyles]::AdjustToUniversal -bor
                [System.Globalization.DateTimeStyles]::AssumeUniversal))
    }
    return [datetime]::UtcNow
}

function Format-LocalTime([datetime]$Utc) {
    $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($Utc, [System.TimeZoneInfo]::Local)
    return $local.ToString("HH:mm")
}

# La ventana segura de la medicion (docs/MANUAL_OPERACION.md, seccion 4.1): minutos :10 a :40 de una
# hora UTC que NO sea de cierre de vela de 4 h (00, 04, 08, 12, 16, 20). Todas las filas de la
# medicion caen en :00/:01; a partir de :10 ya no queda un etiquetado en vuelo que cortar.
function Test-SafeWindow([datetime]$NowUtc) {
    if ($script:FourHourCloseHours -contains $NowUtc.Hour) {
        return [pscustomobject]@{
            Ok = $false
            Reason = ("las {0:00}:{1:00} UTC son de una hora de cierre de vela de 4 h " +
                "(00, 04, 08, 12, 16, 20 UTC)") -f $NowUtc.Hour, $NowUtc.Minute
        }
    }
    if ($NowUtc.Minute -lt 10 -or $NowUtc.Minute -gt 40) {
        return [pscustomobject]@{
            Ok = $false
            Reason = ("el minuto :{0:00} esta fuera de :10-:40 (las filas de la medicion caen en " +
                ":00/:01 de cada hora)") -f $NowUtc.Minute
        }
    }
    return [pscustomobject]@{ Ok = $true; Reason = "dentro de la ventana segura" }
}

# La proxima vez que se abre la ventana (minuto :10 de una hora que no sea de cierre de 4 h).
function Get-NextSafeWindow([datetime]$NowUtc) {
    $candidate = $NowUtc.Date.AddHours($NowUtc.Hour).AddMinutes(10)
    for ($i = 0; $i -lt 30; $i++) {
        if ($candidate -gt $NowUtc -and ($script:FourHourCloseHours -notcontains $candidate.Hour)) {
            return $candidate
        }
        $candidate = $candidate.AddHours(1)
    }
    return $candidate
}


# --- puertos y /health -----------------------------------------------------------------------------

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-PortFree([int]$Port, [int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortListening $Port)) { return $true }
        Start-Sleep -Seconds 1
    }
    return (-not (Test-PortListening $Port))
}

# `$Attempts` > 1 reintenta (con 2 s de pausa) antes de rendirse: una sola sonda lenta -- el PC cargado,
# el primer Invoke-RestMethod de un proceso nuevo -- no debe bastar para declarar que el bot no responde.
function Get-HealthOrNull([int]$Port, [int]$TimeoutSec = 5, [int]$Attempts = 1) {
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec $TimeoutSec `
                -ErrorAction Stop)
        } catch {
            if ($attempt -lt $Attempts) { Start-Sleep -Seconds 2 }
        }
    }
    return $null
}

# Problemas de un /health frente a lo que exige la mudanza: status ok, hash congelado ok y sin pausa.
function Get-HealthProblems($Health) {
    $problems = @()
    if ($null -eq $Health) { return @("no responde") }
    if ($Health.status -ne "ok") { $problems += "status=$($Health.status)" }
    if ($Health.llm_labeling.frozen_hash_ok -ne $true) { $problems += "frozen_hash_ok no es true" }
    if ($Health.control.paused -ne $false) { $problems += "la medicion esta PAUSADA" }
    return $problems
}

function Format-Health($Health) {
    if ($null -eq $Health) { return "sin respuesta" }
    return ("status={0} frozen_hash_ok={1} paused={2} kill_switch={3} etiquetado={4} brazo={5} push={6}" -f
        $Health.status, $Health.llm_labeling.frozen_hash_ok, $Health.control.paused,
        $Health.control.kill_switch_active, $Health.llm_labeling.enabled,
        $Health.paper_execution.active, $Health.telegram_push.active)
}

# Espera a que /health quede bien. Falla de inmediato ante algo que no se arregla solo (hash
# congelado distinto, pausa); `stale` se tolera hasta el primer ciclo del scheduler.
function Wait-HealthGood([int]$Port, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $health = Get-HealthOrNull $Port 5
        if ($null -ne $health) {
            $last = $health
            if ($health.llm_labeling.frozen_hash_ok -eq $false) {
                return [pscustomobject]@{ Ok = $false; Health = $health
                    Reason = "frozen_hash_ok=false: el prompt de este codigo no es el congelado" }
            }
            if ($health.control.paused -eq $true) {
                return [pscustomobject]@{ Ok = $false; Health = $health
                    Reason = "la medicion esta PAUSADA (control.paused=true)" }
            }
            if ($health.status -eq "ok") {
                return [pscustomobject]@{ Ok = $true; Health = $health; Reason = "ok" }
            }
        }
        Start-Sleep -Seconds 3
    }
    $reason = if ($null -eq $last) { "no respondio /health en $TimeoutSec s" }
              else { "no llego a status=ok en $TimeoutSec s ($(Format-Health $last))" }
    return [pscustomobject]@{ Ok = $false; Health = $last; Reason = $reason }
}


# --- tarea programada (locale-independiente: XML y /ENABLE /DISABLE) -------------------------------------

# Enabled | Disabled | Missing
function Get-TaskState([string]$Name) {
    $result = Invoke-Native $script:SchtasksExe @("/Query", "/TN", $Name, "/XML") 30
    if ($result.ExitCode -ne 0) { return "Missing" }
    $xml = $result.Stdout -replace "`0", ""
    if ($xml -match "<Enabled>\s*false\s*</Enabled>") { return "Disabled" }
    return "Enabled"
}

function Set-TaskEnabled([string]$Name, [bool]$Enabled) {
    $flag = if ($Enabled) { "/ENABLE" } else { "/DISABLE" }
    $result = Invoke-Native $script:SchtasksExe @("/Change", "/TN", $Name, $flag) 30
    if ($result.ExitCode -ne 0) {
        throw ("no se pudo cambiar la tarea '$Name' ($flag): " +
            (Get-Tail ($result.Stderr + " " + $result.Stdout)) +
            " -- abre PowerShell como administrador si es un tema de permisos")
    }
}


# --- el bot nativo --------------------------------------------------------------------------------------

function Get-StopBotScript {
    if ($env:MINERVA_STOP_BOT_SCRIPT) { return $env:MINERVA_STOP_BOT_SCRIPT }
    return (Join-Path $script:RepoRoot "scripts\stop_bot.ps1")
}

function Get-StartBotScript {
    if ($env:MINERVA_START_BOT_SCRIPT) { return $env:MINERVA_START_BOT_SCRIPT }
    return (Join-Path $script:RepoRoot "scripts\start_bot.ps1")
}

function Stop-NativeBot([int]$Port) {
    $result = Invoke-Native "powershell.exe" @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Get-StopBotScript), "-Port", "$Port"
    ) 120
    foreach ($line in ($result.Stdout -split "`r?`n")) { if ($line.Trim()) { Write-Log "    stop_bot: $line" } }
    if ($result.ExitCode -ne 0) {
        throw "stop_bot.ps1 termino con codigo $($result.ExitCode): $(Get-Tail $result.Stderr)"
    }
    if (-not (Wait-PortFree $Port 30)) { throw "el puerto $Port sigue ocupado despues de detener el bot" }
}

function Start-NativeBot([int]$Port, [string]$DatabaseFull, [string]$LogDirFull) {
    $result = Invoke-Native "powershell.exe" @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Get-StartBotScript),
        "-Port", "$Port", "-DatabasePath", $DatabaseFull, "-LogDir", $LogDirFull
    ) 300
    foreach ($line in ($result.Stdout -split "`r?`n")) {
        if ($line -match "^\d{4}-\d{2}-\d{2}T.* \| ") { Write-Log "    start_bot: $line" }
    }
    if ($result.ExitCode -ne 0) {
        throw "start_bot.ps1 termino con codigo $($result.ExitCode): $(Get-Tail $result.Stdout)"
    }
}


# --- huellas de la base (integridad + filas por tabla) ----------------------------------------------------------

function Get-HostFingerprint([string]$DbPath) {
    $result = Invoke-Python @((Join-Path $script:RepoRoot "scripts\db_fingerprint.py"), "--db", $DbPath) 300
    if ($result.ExitCode -ne 0) {
        throw "db_fingerprint.py fallo sobre $DbPath (codigo $($result.ExitCode)): $(Get-Tail ($result.Stderr + ' ' + $result.Stdout))"
    }
    return ($result.Stdout | ConvertFrom-Json)
}

function Get-VolumeFingerprint {
    $result = Invoke-Compose @(
        "run", "--rm", "--no-deps", "-T", "minerva", "python", "scripts/db_fingerprint.py",
        "--db", "/app/data/minerva_estabilidad.db"
    ) 300
    return ($result.Stdout | ConvertFrom-Json)
}

# Diferencias entre dos huellas (vacio = equivalentes). El modo del diario no cuenta: un respaldo se
# guarda en modo clasico y el bot lo pasa a WAL al conectarse.
function Compare-Fingerprint($Left, $Right) {
    $diffs = @()
    if ($Left.integrity -ne "ok") { $diffs += "la integridad del origen no es ok ($($Left.integrity))" }
    if ($Right.integrity -ne "ok") { $diffs += "la integridad del destino no es ok ($($Right.integrity))" }
    if ($Left.schema_sha256 -ne $Right.schema_sha256) { $diffs += "el esquema es distinto" }
    $names = @($Left.tables.PSObject.Properties.Name) + @($Right.tables.PSObject.Properties.Name) |
        Sort-Object -Unique
    foreach ($name in $names) {
        $a = $Left.tables.$name
        $b = $Right.tables.$name
        if ($a -ne $b) {
            # OJO: PowerShell no distingue mayusculas; "$left" pisaria al parametro $Left en la vuelta siguiente.
            $originText = if ($null -eq $a) { "no existe" } else { "$a filas" }
            $targetText = if ($null -eq $b) { "no existe" } else { "$b filas" }
            $diffs += "${name}: origen $originText, destino $targetText"
        }
    }
    return $diffs
}

function Format-KeyCounts($Fingerprint) {
    $parts = foreach ($name in @("shadow_trades", "llm_logs", "paper_execution_log")) {
        "{0}={1}" -f $name, $Fingerprint.tables.$name
    }
    return ($parts -join " ")
}


# --- la marca de la mudanza -------------------------------------------------------------------------------------

function Get-MarkerPath([string]$DatabaseFull) {
    return (Join-Path (Split-Path -Parent $DatabaseFull) $script:MarkerName)
}

function Read-Marker([string]$Path) {
    $values = @{}
    foreach ($line in (Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)) {
        if ($line -match "^\s*([a-z_]+)\s*=\s*(.*?)\s*$") { $values[$Matches[1]] = $Matches[2] }
    }
    return $values
}

function Write-Marker([string]$Path, [hashtable]$Fields) {
    $lines = @(
        "Minerva: la medicion corre en Docker (marca escrita por scripts\migrate_to_docker.ps1).",
        "La base VIVA esta en el volumen de Docker, NO en este directorio: la de aqui es una copia",
        "VIEJA, conservada solo como respaldo. start_bot.ps1 se niega a arrancar el bot nativo sobre",
        "ella y los scripts de respaldo/restauracion rechazan la base por defecto.",
        "Para volver al arranque nativo: scripts\rollback_from_docker.ps1 (docs\MUDANZA_DOCKER.md).",
        ""
    )
    foreach ($key in ($Fields.Keys | Sort-Object)) { $lines += ("{0}={1}" -f $key, $Fields[$key]) }
    Set-Content -LiteralPath $Path -Value $lines -Encoding ASCII
}


# --- deshacer lo hecho si algo falla -----------------------------------------------------------------------------

# Cada paso que cambia algo registra aqui como volver atras ANTES de hacerlo. Si el script falla (o
# alguien pulsa Ctrl+C) se ejecutan en orden inverso y el sistema vuelve a como estaba.
function Push-Undo([string]$Name, [scriptblock]$Action) {
    $script:UndoStack.Insert(0, [pscustomobject]@{ Name = $Name; Action = $Action })
}

function Invoke-Undos {
    $allOk = $true
    foreach ($entry in @($script:UndoStack)) {
        Write-Log "DESHACIENDO: $($entry.Name)"
        try {
            & $entry.Action
            Write-Log "    hecho"
        } catch {
            $allOk = $false
            Write-Log "    FALLO al deshacer '$($entry.Name)': $($_.Exception.Message)"
        }
    }
    $script:UndoStack.Clear()
    return $allOk
}


# --- comprobaciones del entorno (Docker Desktop y archivo de credenciales) -----------------------------------------

# Avisos (no fallos) sobre valores del archivo de credenciales que tienen un '$' y NO estan entre comillas
# simples. Docker Compose lee el `.env` de la raiz en CADA comando (up, ps, logs...), toma ese '$' por una
# variable y escribe trozos del valor -- tipicamente el hash de la contrasena del dashboard -- en pantalla
# (el bot funciona igual: el archivo se monta tal cual dentro del contenedor). Solo se informa el NOMBRE de
# la variable, nunca su valor. Solo lectura.
function Get-EnvQuotingWarnings([string]$EnvPath) {
    if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { return @() }
    $names = @()
    foreach ($line in (Get-Content -LiteralPath $EnvPath)) {
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Contains('$') -and -not $value.StartsWith("'")) { $names += $name }
        }
    }
    if ($names.Count -eq 0) { return @() }
    return ('en el archivo de credenciales, ' + ($names -join ', ') + ' lleva signos $ sin comillas simples: ' +
        'cada "docker compose ..." escribira trozos de su valor en pantalla (el bot funciona igual). ' +
        'Rodea el valor con comillas simples en ese archivo (scripts\hash_password.py ya imprime asi el hash) ' +
        'y despues limpia la pantalla')
}

# Avisos (no fallos) sobre si Docker Desktop vuelve solo tras reiniciar el PC. Solo lectura.
function Get-DockerAutostartWarnings {
    $warnings = @()
    $settings = Join-Path $env:APPDATA "Docker\settings-store.json"
    if (Test-Path -LiteralPath $settings -PathType Leaf) {
        try {
            $parsed = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json
            if ($parsed.AutoStart -eq $false) {
                $warnings += ("Docker Desktop tiene desactivado 'Start Docker Desktop when you sign in' " +
                    "(Settings > General): tras reiniciar el PC el bot NO volvera solo")
            }
        } catch { }
    }
    $approved = Get-ItemProperty -Path `
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" `
        -Name "Docker Desktop" -ErrorAction SilentlyContinue
    if ($approved -and $approved."Docker Desktop" -and ($approved."Docker Desktop"[0] -band 1) -eq 1) {
        $warnings += ("Windows tiene DESHABILITADO 'Docker Desktop' en Inicio (Administrador de tareas > " +
            "Inicio): tras reiniciar el PC el bot NO volvera solo")
    }
    return $warnings
}
