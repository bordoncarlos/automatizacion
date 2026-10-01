<#
============================================================
 programar_publicacion.ps1  -  registra actualizar_y_publicar.ps1 cada 10 minutos
============================================================
PARA QUE SIRVE
  Deja programada la publicacion del informe. Correrlo UNA vez como el
  usuario de la sesion, despues de guardar_credencial.ps1.

DETALLES QUE IMPORTAN
  - LogonType Interactive: Power BI Desktop es una aplicacion con interfaz y
    necesita la sesion del usuario abierta.
  - MultipleInstances IgnoreNew: si un ciclo sigue corriendo cuando toca el
    siguiente, el nuevo se descarta en vez de encimarse.
  - ExecutionTimeLimit 15 min: es solo un respaldo. Quien acota el ciclo es
    el tope de refresh del propio script, porque cortar desde el Programador
    de tareas deja el refresh vivo en el motor.

EJEMPLO
  ... programar_publicacion.ps1 -Pbix "C:\ruta\Informe.pbix" -Workspace "Ventas"

PARA DESINSTALAR
  Unregister-ScheduledTask -TaskName "Publicar Power BI" -Confirm:$false
============================================================
#>
param(
    [Parameter(Mandatory = $true)][string]$Pbix,
    [Parameter(Mandatory = $true)][string]$Workspace,
    [string]$Desde = "08:00",
    [int]$CadaMinutos = 10,
    [int]$Horas = 11,
    [string]$NombreTarea = "Publicar Power BI"
)

$ErrorActionPreference = 'Stop'
$Script = Join-Path $PSScriptRoot "actualizar_y_publicar.ps1"

$Accion = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $PSScriptRoot `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Pbix "{1}" -Workspace "{2}"' -f $Script, $Pbix, $Workspace)

$Disparador = New-ScheduledTaskTrigger -Daily -At $Desde
$Disparador.Repetition = (New-ScheduledTaskTrigger -Once -At $Desde `
    -RepetitionInterval (New-TimeSpan -Minutes $CadaMinutos) `
    -RepetitionDuration (New-TimeSpan -Hours $Horas)).Repetition

$Config = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

$Usuario = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $NombreTarea -Action $Accion -Trigger $Disparador -Settings $Config `
    -Principal $Usuario -Description "Refresca el pbix en Power BI Desktop, lo guarda y lo publica" -Force | Out-Null

Write-Host "Tarea '$NombreTarea' registrada: cada $CadaMinutos min desde las $Desde durante $Horas h." -ForegroundColor Green
