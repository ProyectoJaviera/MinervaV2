<#
.SYNOPSIS
    Detiene SOLO el proceso que escucha en -Port y su lanzador (el cmd.exe
    que start_bot.ps1 crea para fusionar stdout+stderr) -- nunca otros
    procesos python/pythonw del sistema.

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

    Identifica el PID que escucha en -Port (`Get-NetTCPConnection`) y, por
    separado, SU proceso padre (`Get-CimInstance Win32_Process`) -- solo lo
    toca si ademas se llama literalmente "cmd" (el lanzador que
    start_bot.ps1 crea); un padre de otro tipo (p.ej. explorer.exe si el
    bot se arranco a mano de otra forma), o que ya no exista, se deja
    intacto, con un aviso. Verificado en esta maquina: con una corrida
    larga (minutos), el `ParentProcessId` que Windows reporta para el
    proceso que escucha el puerto puede quedar apuntando a un PID que para
    entonces ya no es el `cmd.exe` lanzador (puede haber terminado solo, o
    ese numero de PID puede haber sido reciclado por otro proceso
    completamente distinto) -- en ese caso no queda ningun lanzador vivo
    que detener, y es exactamente por eso que este script NUNCA asume que
    un PID encontrado ahi es seguro de tocar sin antes confirmar su nombre.

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

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Write-Output "Nada escuchando en el puerto $Port -- no hay nada que detener."
    exit 0
}

$targetPid = ($listener | Select-Object -First 1 -ExpandProperty OwningProcess)
Write-Output "Proceso escuchando en el puerto $Port`: PID $targetPid"

$parentPid = $null
try {
    $parentPid = (Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction Stop).ParentProcessId
} catch {
    Write-Output "Aviso: no se pudo determinar el proceso lanzador (se detiene igual PID $targetPid)."
}

Write-Output "Deteniendo PID $targetPid..."
Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue

if ($parentPid) {
    $parentProc = Get-Process -Id $parentPid -ErrorAction SilentlyContinue
    if ($parentProc -and $parentProc.ProcessName -eq "cmd") {
        Write-Output "Deteniendo su proceso lanzador: PID $parentPid ($($parentProc.ProcessName))"
        Stop-Process -Id $parentPid -Force -ErrorAction SilentlyContinue
    } elseif ($parentProc) {
        Write-Output ("El proceso padre (PID $parentPid, $($parentProc.ProcessName)) no parece ser " +
            "el lanzador de start_bot.ps1 -- no se toca.")
    }
}

Start-Sleep -Seconds 2
$stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stillListening) {
    $stuckPid = ($stillListening | Select-Object -First 1 -ExpandProperty OwningProcess)
    Write-Output "ERROR: el puerto $Port sigue ocupado (PID $stuckPid) despues de intentar detenerlo."
    exit 1
}
Write-Output "OK: el puerto $Port quedo libre."
