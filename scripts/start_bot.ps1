<#
.SYNOPSIS
    Arranca el bot real de Minerva (medicion en vivo) de un solo comando,
    sin consola visible, rehusando una segunda instancia sobre la misma base.

.DESCRIPTION
    Continuidad operativa (plan pre-Fase 5 aprobado 2026-10-14, seccion de
    arranque/parada): el PC actual se apaga de tanto en tanto -- este
    script deja arrancar el bot real tras un reinicio con un solo comando,
    de forma segura.

    Variables FIJAS ("las que uso hoy"), siempre las mismas sin importar lo
    que diga `.env` en ese momento -- un lugar unico y visible para ver
    exactamente con que arranca el bot real, en vez de depender de un
    `.env` que puede haber quedado desactualizado:
        AUTO_OPEN_WITHOUT_LLM=false
        LOG_LEVEL=INFO
        LLM_LIVE_LABELING_ENABLED=true  (SOLO con -Port 8000; false con cualquier
            otro puerto: una instancia de prueba sobre una copia de la base no debe
            llamar a la API de Anthropic -- gasto real -- al abrir una sombra nueva.)
        TELEGRAM_COMMANDS_ENABLED=true  (SOLO con -Port 8000; false con cualquier
            otro puerto: un token de Telegram admite UN solo consumidor de
            getUpdates, asi que una instancia de prueba nunca debe consumirlo.
            Token y chat_id siguen viniendo de `.env`.)
        PAPER_EXECUTION_ENABLED=true  (SOLO con -Port 8000; false con cualquier
            otro puerto. Brazo ejecutado de la cuenta PAPER, subfase 3.9: abre solo
            las senales APROBADA que llegaron a tiempo, pasando por el motor de
            riesgo. Nunca ordenes reales: solo PaperBackend.)

    -Port/-DatabasePath/-LogDir son parametros SOLO para poder probar este
    script contra el puerto 8001 y una base/carpeta de log temporales
    (nunca el bot real ni su base) -- el uso real, sin argumentos, apunta
    exactamente a lo pedido: puerto 8000, data\minerva_estabilidad.db,
    logs\.

    REHUSA arrancar si ya hay algo escuchando en -Port: dos procesos
    escribiendo la MISMA base SQLite a la vez es exactamente lo que este
    script existe para evitar.

    "Sin consola visible": el proceso real (`pythonw.exe`, que de por si
    nunca tiene consola) se envuelve en `cmd.exe /c "... >> archivo 2>&1"`
    -ocultada con -WindowStyle Hidden- porque PowerShell 5.1 no permite que
    `Start-Process` redirija stdout Y stderr al MISMO archivo directamente
    (error verificado: "RedirectStandardOutput y RedirectStandardError son
    iguales") -- el `2>&1` de `cmd.exe` si lo permite. Esto es tambien la
    razon de por que hay un proceso "lanzador" (`cmd.exe`) distinto del que
    de verdad escucha el puerto (`pythonw.exe`, su hijo) -- ver
    `stop_bot.ps1`, que detiene los dos.

    Dos logs, con proposito distinto:
    - `logs\medicion_out.log`: la salida CRUDA (stdout+stderr fusionados)
      del proceso -- captura, por ejemplo, un error de import ANTES de que
      el logging interno del bot (`setup_logging()`, que escribe en
      `logs\minerva.log`, el log detallado de siempre, sin cambios) llegue
      a inicializarse.
    - `logs\medicion.log`: el diario OPERATIVO de este script (no del bot):
      cuando se intento arrancar, con que PID, y que respondio `/health`.

.EXAMPLE
    .\scripts\start_bot.ps1
    Arranca el bot real en el puerto 8000 contra data\minerva_estabilidad.db.

.EXAMPLE
    .\scripts\start_bot.ps1 -Port 8001 -DatabasePath "C:\temp\copia.db" -LogDir "C:\temp\logs"
    Para probar el script en si (nunca el bot real).
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$DatabasePath = "data/minerva_estabilidad.db",
    [string]$LogDir = "logs"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Resolve-MaybeRelative([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $RepoRoot $Path)
}

$DatabaseFullPath = Resolve-MaybeRelative $DatabasePath
$LogDirFullPath = Resolve-MaybeRelative $LogDir
New-Item -ItemType Directory -Force -Path $LogDirFullPath | Out-Null
$OutLog = Join-Path $LogDirFullPath "medicion_out.log"
$OpLog = Join-Path $LogDirFullPath "medicion.log"

function Write-OpLog([string]$Message) {
    $line = "{0} | {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Add-Content -Path $OpLog -Value $line -Encoding utf8
    Write-Output $line
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $existingPids = ($existing | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    Write-OpLog ("RECHAZADO: ya hay un proceso escuchando en el puerto $Port (PID $existingPids). " +
        "No se arranca una segunda instancia -- dos procesos escribiendo la misma base a la vez " +
        "la corromperian. Corre stop_bot.ps1 primero si de verdad queres reiniciar.")
    exit 1
}

$pythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-OpLog "ERROR: no existe $pythonw -- el entorno virtual (.venv) no esta creado en este PC."
    exit 1
}

# Solo el bot REAL (puerto 8000) etiqueta con el LLM y consume getUpdates de Telegram;
# cualquier otro puerto es una instancia de prueba (ver DESCRIPTION).
$isRealBot = ($Port -eq 8000)
$telegramCommands = if ($isRealBot) { "true" } else { "false" }
$llmLabeling = if ($isRealBot) { "true" } else { "false" }
$paperExecution = if ($isRealBot) { "true" } else { "false" }
Write-OpLog ("Puerto $Port libre. Arrancando con DATABASE_PATH=$DatabaseFullPath " +
    "AUTO_OPEN_WITHOUT_LLM=false LOG_LEVEL=INFO LLM_LIVE_LABELING_ENABLED=$llmLabeling " +
    "TELEGRAM_COMMANDS_ENABLED=$telegramCommands PAPER_EXECUTION_ENABLED=$paperExecution")

$env:DATABASE_PATH = $DatabaseFullPath
$env:AUTO_OPEN_WITHOUT_LLM = "false"
$env:LOG_LEVEL = "INFO"
$env:LLM_LIVE_LABELING_ENABLED = $llmLabeling
$env:TELEGRAM_COMMANDS_ENABLED = $telegramCommands
$env:PAPER_EXECUTION_ENABLED = $paperExecution
$env:LOG_DIR = $LogDirFullPath

# Ver DESCRIPTION: cmd.exe fusiona stdout+stderr a $OutLog con "2>&1" (algo
# que Start-Process no permite hacer directo sobre el mismo archivo) -- por
# eso el proceso lanzado por Start-Process es cmd.exe, no pythonw.exe. La
# capa EXTRA de comillas alrededor de todo $innerCommand es necesaria por un
# quirk conocido de "cmd /c": si el argumento de /c ya empieza con una ruta
# citada, cmd.exe lo parsea mal (sale con ExitCode 1 y nunca llega a crear
# el archivo de salida) salvo que TODO el comando quede envuelto en un par
# de comillas mas -- verificado en esta maquina antes de confiar en esto.
$innerCommand = "`"$pythonw`" -m uvicorn app.main:app --host 127.0.0.1 --port $Port >> `"$OutLog`" 2>&1"
$wrappedCommand = "`"$innerCommand`""
$process = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $wrappedCommand) `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru

Write-OpLog "Proceso lanzador (cmd.exe) PID $($process.Id). Esperando a que /health responda..."

$healthUrl = "http://127.0.0.1:$Port/health"
# 180s, no 60: verificado que el arranque real (descubrimiento de universo +
# backfill de velas) puede tardar mas de un minuto, sobre todo con una base
# nueva/copia fresca que todavia no tiene cache tibia.
$deadline = (Get-Date).AddSeconds(180)
$health = $null
while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
        Write-OpLog ("ERROR: el proceso lanzador (PID $($process.Id)) termino solo durante el " +
            "arranque (ExitCode $($process.ExitCode)). Revisa $OutLog.")
        exit 1
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5 -ErrorAction Stop
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if ($null -eq $health) {
    Write-OpLog ("ERROR: /health no respondio dentro de 180s (PID lanzador $($process.Id) sigue " +
        "vivo -- revisa $OutLog antes de reintentar).")
    exit 1
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$listenerPid = if ($listener) { ($listener | Select-Object -First 1 -ExpandProperty OwningProcess) } else { "?" }
Write-OpLog ("OK: PID $listenerPid escuchando en el puerto $Port (lanzado por PID $($process.Id)). " +
    "/health -> status=$($health.status) llm_labeling.enabled=$($health.llm_labeling.enabled) " +
    "frozen_hash_ok=$($health.llm_labeling.frozen_hash_ok)")
$health | ConvertTo-Json -Depth 5
