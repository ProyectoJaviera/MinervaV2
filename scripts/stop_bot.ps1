<#
.SYNOPSIS
    Detiene SOLO el proceso que escucha en -Port y su cadena de lanzadores
    (cmd.exe de start_bot.ps1 -> pythonw.exe del .venv de este repo -> proceso
    real) -- nunca otros procesos python/pythonw del sistema.

.DESCRIPTION
    Windows no da una señal de apagado cooperativo real para un proceso
    sin consola (`pythonw.exe`) lanzado asi -- esto detiene el proceso
    (`TerminateProcess` via `Stop-Process -Force`; no hay un "WM_CLOSE" que
    funcione sobre un proceso sin ventana). No espera a que
    `ShadowLiveLabeler.shutdown()`/`monitor.shutdown()` terminen de forma
    cooperativa dentro del proceso. SQLite en modo WAL (el que usa este
    proyecto) tolera esto por diseño -- a lo sumo se pierde la ULTIMA
    escritura si estaba a mitad de camino, nunca deja el archivo corrupto,
    y cada `Database.execute()` ya hace commit de inmediato (sin
    transacciones largas pendientes que perder). Documentado aqui para no
    prometer un apagado "gracil" que Windows no deja hacer facil sin
    controles adicionales que este proyecto no tiene todavia (ningun
    endpoint de apagado por HTTP).

    LA CADENA DE PROCESOS (verificada en esta maquina, puerto 8001):
        cmd.exe                                  <- start_bot.ps1 (`cmd /c "... >> log 2>&1"`)
          .venv\Scripts\pythonw.exe              <- el LAUNCHER del venv (trampolin)
            <intérprete base>\python.exe         <- el proceso REAL, el que escucha el puerto
    El launcher del venv lanza al interprete base como HIJO, asi que el PID que
    escucha el puerto tiene como padre al pythonw.exe del .venv, no al cmd.exe.

    Antes de tocar CUALQUIER padre se verifica, con `Get-CimInstance
    Win32_Process`:
      - que el padre exista y haya empezado ANTES que su hijo (si no, el PID fue
        reciclado por otro proceso y no es el padre de verdad);
      - launcher del venv: que su ejecutable sea EXACTAMENTE
        `<este repo>\.venv\Scripts\pythonw.exe` (comparacion de ruta completa);
      - cmd.exe: que su linea de comandos lance ese mismo pythonw.exe con
        `--port <-Port>` (es el cmd de start_bot.ps1 de ESTE puerto).
    Un padre que no pasa la verificacion se deja intacto, con un aviso. Tambien
    se reconoce la cadena corta cmd.exe -> proceso real.

    Se detiene en orden proceso real, launcher, cmd.exe, y al terminar se
    CONFIRMA que no queda vivo ninguno de los procesos de la cadena (mismo PID Y
    misma hora de creacion, para no confundir con un PID reciclado) y que el
    puerto quedo libre; si sobrevive alguno, sale con codigo 1.

.EXAMPLE
    .\scripts\stop_bot.ps1
    Detiene el bot real (puerto 8000).

.EXAMPLE
    .\scripts\stop_bot.ps1 -Port 8001
    Para probar el script en si (nunca el bot real).
#>
[CmdletBinding()]
param(
    [int]$Port = 8000
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"

function Get-ProcInfo([int]$ProcId) {
    return (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue)
}

function Test-SamePath([string]$A, [string]$B) {
    if ([string]::IsNullOrWhiteSpace($A) -or [string]::IsNullOrWhiteSpace($B)) { return $false }
    try {
        return [string]::Equals(
            [System.IO.Path]::GetFullPath($A), [System.IO.Path]::GetFullPath($B),
            [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

# El padre que Windows reporta solo es confiable si existe Y empezo antes que su
# hijo; un PID reciclado por otro proceso nuevo empezaria DESPUES.
function Get-RealParent($Child) {
    if (-not $Child.ParentProcessId) { return $null }
    $parent = Get-ProcInfo $Child.ParentProcessId
    if (-not $parent) { return $null }
    if ($parent.CreationDate -ge $Child.CreationDate) { return $null }
    return $parent
}

# El launcher del venv de ESTE repo: ejecutable exactamente .venv\Scripts\pythonw.exe.
function Test-VenvLauncher($Info) {
    return (Test-SamePath $Info.ExecutablePath $VenvPythonw)
}

# El cmd.exe que crea start_bot.ps1 para ESTE puerto.
function Test-StartBotCmd($Info) {
    if ($Info.Name -ine "cmd.exe") { return $false }
    $cmdline = [string]$Info.CommandLine
    if ($cmdline.IndexOf($VenvPythonw, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    return ($cmdline -match "--port $Port(\s|$)")
}

function Test-StillAlive($Info) {
    $now = Get-ProcInfo $Info.ProcessId
    return ($null -ne $now -and $now.CreationDate -eq $Info.CreationDate)
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Write-Output "Nada escuchando en el puerto $Port -- no hay nada que detener."
    exit 0
}

$targetPid = [int]($listener | Select-Object -First 1 -ExpandProperty OwningProcess)
Write-Output "Proceso escuchando en el puerto $Port`: PID $targetPid"

$target = Get-ProcInfo $targetPid
if (-not $target) {
    Write-Output "Aviso: no se pudo leer el proceso PID $targetPid (se detiene igual)."
    Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
} else {
    # (rol, info) en el orden en que se detienen.
    $chain = [System.Collections.ArrayList]@()
    [void]$chain.Add(@{ Role = "proceso real"; Info = $target })

    $parent = Get-RealParent $target
    if ($parent -and (Test-VenvLauncher $parent)) {
        [void]$chain.Add(@{ Role = "launcher del venv (pythonw.exe)"; Info = $parent })
        $grandparent = Get-RealParent $parent
        if ($grandparent -and (Test-StartBotCmd $grandparent)) {
            [void]$chain.Add(@{ Role = "cmd.exe de start_bot.ps1"; Info = $grandparent })
        } elseif ($grandparent) {
            Write-Output ("El padre del launcher (PID $($grandparent.ProcessId), $($grandparent.Name)) " +
                "no es el cmd.exe de start_bot.ps1 para el puerto $Port -- no se toca.")
        }
    } elseif ($parent -and (Test-StartBotCmd $parent)) {
        [void]$chain.Add(@{ Role = "cmd.exe de start_bot.ps1"; Info = $parent })
    } elseif ($parent) {
        Write-Output ("El proceso padre (PID $($parent.ProcessId), $($parent.Name), " +
            "$($parent.ExecutablePath)) no es el launcher del .venv de este repo ni el cmd.exe de " +
            "start_bot.ps1 -- no se toca.")
    } else {
        Write-Output ("Aviso: el padre que reporta Windows ya no existe o es un PID reciclado -- " +
            "no hay lanzador que detener.")
    }

    $summary = ($chain | ForEach-Object { "$($_.Role)=PID $($_.Info.ProcessId)" }) -join " | "
    Write-Output "Cadena reconocida: $summary"

    foreach ($entry in $chain) {
        Write-Output "Deteniendo $($entry.Role): PID $($entry.Info.ProcessId)"
        Stop-Process -Id $entry.Info.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Seconds 2

    $survivors = @($chain | Where-Object { Test-StillAlive $_.Info })
    if ($survivors.Count -gt 0) {
        foreach ($entry in $survivors) {
            Write-Output "ERROR: sigue vivo $($entry.Role): PID $($entry.Info.ProcessId)"
        }
        exit 1
    }
    Write-Output "Confirmado: no queda vivo ningun proceso de la cadena ($($chain.Count) detenido(s))."
}

$stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stillListening) {
    $stuckPid = ($stillListening | Select-Object -First 1 -ExpandProperty OwningProcess)
    Write-Output "ERROR: el puerto $Port sigue ocupado (PID $stuckPid) despues de intentar detenerlo."
    exit 1
}
Write-Output "OK: el puerto $Port quedo libre."
