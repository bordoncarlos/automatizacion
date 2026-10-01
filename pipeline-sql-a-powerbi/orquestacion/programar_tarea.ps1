<#
============================================================
 programar_tarea.ps1  -  registra la actualizacion en el Programador de tareas
============================================================
PARA QUE SIRVE
  Deja programado ejecutar_con_reintentos.ps1 sin entrar a la interfaz del
  Programador de tareas. Correrlo UNA vez (como el usuario de la sesion, no
  hace falta ser administrador). Volver a correrlo actualiza la tarea.

QUE CONFIGURA
  - Todos los dias a la hora indicada; con -CadaMinutos se repite durante
    el dia (por ejemplo, cada 60 minutos durante 12 horas).
  - StartWhenAvailable: si a esa hora la PC estaba apagada o dormida, corre
    apenas se pueda.
  - MultipleInstances IgnoreNew: nunca dos corridas a la vez.
  - ExecutionTimeLimit: respaldo por si algo queda colgado.

EJEMPLOS
  ... programar_tarea.ps1                              # una vez por dia, 07:30
  ... programar_tarea.ps1 -CadaMinutos 60 -Horas 12    # cada hora, 07:30 a 19:30

PARA DESINSTALAR
  Unregister-ScheduledTask -TaskName "Actualizar reporte SQL" -Confirm:$false
============================================================
#>
param(
    [string]$Hora = "07:30",
    [int]$CadaMinutos = 0,
    [int]$Horas = 12,
    [string]$NombreTarea = "Actualizar reporte SQL"
)

$ErrorActionPreference = 'Stop'
$Script = Join-Path $PSScriptRoot "ejecutar_con_reintentos.ps1"

$Accion = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $Script) `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)

# En PowerShell 5.1, "-Daily" no acepta repeticion: se copia la de un "-Once".
$Disparador = New-ScheduledTaskTrigger -Daily -At $Hora
if ($CadaMinutos -gt 0) {
    $Disparador.Repetition = (New-ScheduledTaskTrigger -Once -At $Hora `
        -RepetitionInterval (New-TimeSpan -Minutes $CadaMinutos) `
        -RepetitionDuration (New-TimeSpan -Hours $Horas)).Repetition
}

$Config = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

# Interactive: corre en la sesion del usuario (no necesita guardar su contrasena).
$Usuario = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $NombreTarea -Action $Accion -Trigger $Disparador `
    -Settings $Config -Principal $Usuario -Force `
    -Description "Actualiza el reporte desde la base SQL, con reintentos" | Out-Null

Write-Host "Tarea '$NombreTarea' registrada." -ForegroundColor Green
Write-Host "Probarla ya:  Start-ScheduledTask -TaskName '$NombreTarea'"
Write-Host "Resultado:    Get-ScheduledTaskInfo -TaskName '$NombreTarea'"
