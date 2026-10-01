<#
============================================================
 ejecutar_con_reintentos.ps1  -  corre actualizar_reporte.py hasta lograrlo
============================================================
PARA QUE SIRVE
  Es lo que dispara el Programador de tareas. Corre el script, guarda su
  salida en un log por dia, y si falla (la base no responde, un corte de red)
  lo reintenta con tope de intentos y de minutos: una caida de 10 minutos
  no debe costar el dia, pero un error permanente tampoco debe quedar
  reintentando para siempre.

SALIDAS
  logs\AAAA-MM\reporte_AAAAMMDD.log   detalle de cada corrida
  logs\estado.txt                     una linea: corriendo / como termino
  Codigo de salida: 0 OK, distinto de 0 si fallo despues de todos los intentos.

USO
  powershell -ExecutionPolicy Bypass -File orquestacion\ejecutar_con_reintentos.ps1
============================================================
#>
param(
    [string]$Python = "",        # vacio = el python del PATH
    [int]$MaxIntentos = 10,
    [int]$MaxMinutos = 60
)

$Raiz = Split-Path $PSScriptRoot -Parent
if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }

# Python escribe en UTF-8 y PowerShell lee esa salida en UTF-8: sin estas dos
# lineas los acentos llegan al log convertidos en simbolos raros.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$LogDir = Join-Path $Raiz "logs"
$LogMes = Join-Path $LogDir (Get-Date -Format "yyyy-MM")
New-Item -ItemType Directory -Path $LogMes -Force | Out-Null
$Log       = Join-Path $LogMes ("reporte_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$EstadoTxt = Join-Path $LogDir "estado.txt"

function Escribir([string]$msg) { "$(Get-Date -Format 'HH:mm:ss') $msg" | Out-File $Log -Append -Encoding utf8 }

# Una sola linea que se pisa en cada paso: se mira de un vistazo sin abrir el log.
function Estado([string]$txt) { "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')] $txt" | Out-File $EstadoTxt -Encoding utf8 }

$script = Join-Path $Raiz "actualizar_reporte.py"
$inicio = Get-Date
for ($i = 1; $i -le $MaxIntentos; $i++) {
    Estado "CORRIENDO (intento $i, desde las $($inicio.ToString('HH:mm')))"
    Escribir ">>> actualizar_reporte.py (intento $i de $MaxIntentos)"
    & $Python $script *>&1 | Out-File $Log -Append -Encoding utf8
    $codigo = $LASTEXITCODE
    if ($codigo -eq 0) {
        Escribir "OK"
        Estado "OK"
        break
    }
    Escribir "FALLO (codigo $codigo)"
    $minutos = ((Get-Date) - $inicio).TotalMinutes
    if ($i -ge $MaxIntentos -or $minutos -ge $MaxMinutos) {
        Estado "FALLO despues de $i intento(s): revisar $(Split-Path $Log -Leaf)"
        break
    }
    # 1 minuto antes del 2do intento (cortes breves), 5 entre los demas
    $espera = if ($i -eq 1) { 60 } else { 300 }
    Escribir "    reintento en $espera s"
    Start-Sleep -Seconds $espera
}

# Limpieza: logs de mas de 60 dias
Get-ChildItem $LogDir -Recurse -Filter "reporte_*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-60) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $codigo
