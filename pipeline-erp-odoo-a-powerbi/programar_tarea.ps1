<#
============================================================
 programar_tarea.ps1  -  registra actualizar_reporte.py en el Programador de tareas
============================================================
PARA QUE SIRVE
  Deja la actualizacion programada sin entrar a la interfaz del Programador
  de tareas. Correrlo UNA vez como el usuario de la sesion (no hace falta
  ser administrador); volver a correrlo actualiza la tarea.

DETALLES QUE IMPORTAN
  - pythonw.exe (no python.exe): sin una ventana de consola apareciendo cada
    minuto en la pantalla. La salida va a logs\AAAA-MM\ (lib/consola.py).
  - MultipleInstances IgnoreNew: si una corrida sigue viva cuando toca la
    siguiente, la nueva se descarta (nunca dos a la vez).
  - ExecutionTimeLimit: un tope, como respaldo.
  - En PowerShell 5.1, "-Daily" no acepta repeticion: se copia la de un
    disparador "-Once". Es el patron que Windows registra bien.

EJEMPLO
  ... programar_tarea.ps1 -Desde "08:00" -CadaMinutos 1 -Horas 11   # cada minuto, 08:00 a 19:00

PARA DESINSTALAR
  Unregister-ScheduledTask -TaskName "Actualizar reporte Odoo" -Confirm:$false
============================================================
#>
param(
    [string]$Desde = "08:00",
    [int]$CadaMinutos = 1,
    [int]$Horas = 11,
    [string]$NombreTarea = "Actualizar reporte Odoo",
    [string]$Pythonw = ""        # vacio = el pythonw.exe junto al python del PATH
)

$ErrorActionPreference = 'Stop'
if (-not $Pythonw) {
    $Pythonw = Join-Path (Split-Path (Get-Command python -ErrorAction Stop).Source) "pythonw.exe"
}
if (-not (Test-Path $Pythonw)) { throw "No se encontro pythonw.exe en $Pythonw (pasarlo con -Pythonw)" }

$Accion = New-ScheduledTaskAction -Execute $Pythonw -WorkingDirectory $PSScriptRoot `
    -Argument ('"{0}"' -f (Join-Path $PSScriptRoot "actualizar_reporte.py"))

$Disparador = New-ScheduledTaskTrigger -Daily -At $Desde
$Disparador.Repetition = (New-ScheduledTaskTrigger -Once -At $Desde `
    -RepetitionInterval (New-TimeSpan -Minutes $CadaMinutos) `
    -RepetitionDuration (New-TimeSpan -Hours $Horas)).Repetition

$Config = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

# Interactive: corre en la sesion del usuario (no hay que guardar su contrasena).
$Usuario = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $NombreTarea -Action $Accion -Trigger $Disparador -Settings $Config `
    -Principal $Usuario -Description "Actualiza el reporte desde Odoo" -Force | Out-Null

Write-Host "Tarea '$NombreTarea' registrada: cada $CadaMinutos min desde las $Desde durante $Horas h." -ForegroundColor Green
Write-Host "Resultado:  Get-ScheduledTaskInfo -TaskName '$NombreTarea'"
Write-Host "Codigo 3 = el Excel estaba abierto; lo escribe la corrida siguiente (no es un error)."
