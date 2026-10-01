<#
============================================================
 programar_tareas.ps1  -  registra las 5 tareas en el Programador de tareas
============================================================
PARA QUE SIRVE
  Deja todo el pipeline programado de una vez, sin entrar a la interfaz del
  Programador de tareas. Correrlo UNA vez como el usuario de la sesion (no
  hace falta ser administrador); volver a correrlo actualiza las tareas.

  Tarea                 Script                          Cuando
  --------------------  ------------------------------  -------------------------------
  ERP Ventas            actualizar_ventas.py            cada 1 min, 08:00-19:00
  ERP Cobranza          actualizar_cobranza.py          cada 1 min, 08:00:30-19:00
  ERP Stock             actualizar_stock.py             07:30 y 13:00
  ERP Clientes          actualizar_clientes.py          cada 1 hora, 07:45-19:45
  Publicar Power BI     powerbi\actualizar_y_publicar   cada 10 min, 08:00-19:00

DETALLES QUE IMPORTAN
  - pythonw.exe (no python.exe): sin ventana de consola apareciendo cada
    minuto en la pantalla. La salida va a logs\AAAA-MM\ (lib/consola.py).
  - La cobranza arranca 30 s despues que las ventas: usa el archivo que la
    de ventas acaba de escribir y asi no se pisan.
  - MultipleInstances IgnoreNew: si una corrida sigue viva cuando toca la
    siguiente, la nueva se descarta (nunca dos a la vez).
  - ExecutionTimeLimit: un tope por tarea, como respaldo.
  - En PowerShell 5.1, "-Daily" no acepta repeticion: se copia la de un
    disparador "-Once". Es el patron que Windows registra bien.

PARA DESINSTALAR
  "ERP Ventas","ERP Cobranza","ERP Stock","ERP Clientes","Publicar Power BI" |
      % { Unregister-ScheduledTask -TaskName $_ -Confirm:$false }
============================================================
#>
param(
    [string]$Pythonw = "",                  # vacio = el pythonw.exe junto al python del PATH
    [string]$Pbix = (Join-Path $PSScriptRoot "Informe.pbix"),
    [string]$Workspace = "Mi Workspace"
)

$ErrorActionPreference = 'Stop'
$Raiz = $PSScriptRoot
if (-not $Pythonw) {
    $Pythonw = Join-Path (Split-Path (Get-Command python -ErrorAction Stop).Source) "pythonw.exe"
}
if (-not (Test-Path $Pythonw)) { throw "No se encontro pythonw.exe en $Pythonw (pasarlo con -Pythonw)" }

function Disparador-Diario([string]$hora, [int]$cadaMinutos = 0, [int]$duranteHoras = 0) {
    $t = New-ScheduledTaskTrigger -Daily -At $hora
    if ($cadaMinutos -gt 0) {
        $t.Repetition = (New-ScheduledTaskTrigger -Once -At $hora `
            -RepetitionInterval (New-TimeSpan -Minutes $cadaMinutos) `
            -RepetitionDuration (New-TimeSpan -Hours $duranteHoras)).Repetition
    }
    return $t
}

function Registrar([string]$nombre, $accion, $disparadores, [int]$topeMinutos, [string]$descripcion) {
    $config = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $topeMinutos) -MultipleInstances IgnoreNew
    # Interactive: corre en la sesion del usuario. Power BI Desktop necesita
    # escritorio, y asi no hay que guardar la contrasena de Windows en la tarea.
    $usuario = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger $disparadores -Settings $config `
        -Principal $usuario -Description $descripcion -Force | Out-Null
    Write-Host ("  {0,-18} registrada" -f $nombre) -ForegroundColor Green
}

function Accion-Python([string]$script) {
    New-ScheduledTaskAction -Execute $Pythonw -Argument ('"{0}"' -f (Join-Path $Raiz $script)) -WorkingDirectory $Raiz
}

Write-Host "Registrando tareas (python: $Pythonw)"

Registrar "ERP Ventas" (Accion-Python "actualizar_ventas.py") `
    (Disparador-Diario "08:00" 1 11) 3 "Ventas de Odoo -> Excel de Power BI, cada minuto"

Registrar "ERP Cobranza" (Accion-Python "actualizar_cobranza.py") `
    (Disparador-Diario "08:00:30" 1 11) 5 "Cobranza contado + credito -> parquet, cada minuto"

Registrar "ERP Stock" (Accion-Python "actualizar_stock.py") `
    @((Disparador-Diario "07:30"), (Disparador-Diario "13:00")) 10 "Existencias por deposito -> Excel"

Registrar "ERP Clientes" (Accion-Python "actualizar_clientes.py") `
    (Disparador-Diario "07:45" 60 12) 10 "Clientes nuevos de Odoo -> base de clientes"

$scriptPbi = Join-Path $Raiz "powerbi\actualizar_y_publicar.ps1"
$accionPbi = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Raiz `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Pbix "{1}" -Workspace "{2}"' -f $scriptPbi, $Pbix, $Workspace)
Registrar "Publicar Power BI" $accionPbi (Disparador-Diario "08:00" 10 11) 15 `
    "Refresca el pbix en Power BI Desktop, lo guarda y lo publica al servicio"

Write-Host ""
Write-Host "Ver el resultado de una tarea:  Get-ScheduledTaskInfo -TaskName 'ERP Ventas'"
Write-Host "Codigo 3 = el archivo estaba abierto; lo escribe la corrida siguiente (no es un error)."
