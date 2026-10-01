<#
============================================================
 programar_tarea.ps1  -  registra la cadena diaria en el Programador de tareas
============================================================
PARA QUE SIRVE
  Deja programado ejecutar_pipeline.ps1 sin tener que entrar a la interfaz
  del Programador de tareas. Correrlo UNA vez (como el usuario de la sesion,
  no hace falta ser administrador). Volver a correrlo actualiza la tarea.

QUE CONFIGURA
  - Dos disparadores: todos los dias a las 07:30 y al iniciar sesion. El
    segundo recupera los dias en que la PC estaba apagada a las 07:30; la
    guarda anti-duplicado del script evita que corra dos veces el mismo dia.
  - StartWhenAvailable: si a la hora programada la PC dormia, corre al
    despertar.
  - MultipleInstances IgnoreNew: nunca dos cadenas a la vez.
  - ExecutionTimeLimit 6 h: respaldo por si algo queda colgado.

PARA DESINSTALAR
  Unregister-ScheduledTask -TaskName "Pipeline SQL diario" -Confirm:$false
============================================================
#>
param([string]$Hora = "07:30", [string]$NombreTarea = "Pipeline SQL diario")

$ErrorActionPreference = 'Stop'
$Script = Join-Path $PSScriptRoot "ejecutar_pipeline.ps1"

$Accion = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $Script) `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)

$Disparadores = @(
    (New-ScheduledTaskTrigger -Daily -At $Hora),
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)
)

$Config = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew

# Interactive: corre en la sesion del usuario (no necesita guardar su contrasena).
$Usuario = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $NombreTarea -Action $Accion -Trigger $Disparadores `
    -Settings $Config -Principal $Usuario -Force `
    -Description "Extraccion SQL -> parquet, conciliacion y foto de stock (en serie, con reintentos)" | Out-Null

Write-Host "Tarea '$NombreTarea' registrada: diaria $Hora + al iniciar sesion." -ForegroundColor Green
Write-Host "Probarla ya:  Start-ScheduledTask -TaskName '$NombreTarea'"
Write-Host "Resultado:    Get-ScheduledTaskInfo -TaskName '$NombreTarea'"
