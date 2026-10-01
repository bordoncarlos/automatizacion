<#
============================================================
 ejecutar_pipeline.ps1  -  cadena diaria del pipeline, EN SERIE
============================================================
PARA QUE SIRVE
  Corre los pasos 01 -> 02 -> 03 uno detras del otro, reintenta los que
  fallan y deja un log por dia y una linea de estado. Es lo que dispara el
  Programador de tareas todas las mananas (ver programar_tarea.ps1).

REGLAS
  - EN SERIE, nunca en paralelo: 01 y 02 escriben el mismo parquet.
  - Cada paso se reintenta hasta lograrlo, con tope de intentos y de
    minutos: una caida de la base de 10 minutos no debe costar el dia, pero
    un error permanente tampoco debe trabar la cadena para siempre.
  - Si 01 falla, se SALTA 02: conciliar un parquet al que le faltan las
    ventas de ayer daria diferencias falsas y las "corregiria".
  - 03 corre SIEMPRE: la foto de stock de hoy no se puede sacar manana.
  - Si la cadena ya termino hoy, no vuelve a correr (la tarea tiene dos
    disparadores: a las 07:30 y al iniciar sesion, por si la PC estaba
    apagada a esa hora).

SALIDAS
  logs\AAAA-MM\pipeline_AAAAMMDD.log   detalle de cada paso
  logs\pipeline_estado.txt             una linea: en que paso va / como termino
  Codigo de salida = cantidad de pasos que fallaron (0 = todo OK).

USO
  powershell -ExecutionPolicy Bypass -File orquestacion\ejecutar_pipeline.ps1
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
$Log       = Join-Path $LogMes ("pipeline_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$EstadoTxt = Join-Path $LogDir "pipeline_estado.txt"

function Escribir([string]$msg) {
    "$(Get-Date -Format 'HH:mm:ss') $msg" | Out-File $Log -Append -Encoding utf8
}

# Una sola linea que se pisa en cada paso: se mira de un vistazo (o por
# escritorio remoto) sin abrir el log.
function Estado([string]$txt) {
    "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')] $txt" | Out-File $EstadoTxt -Encoding utf8
}

# Corre un script con reintentos. Devuelve $true si termino con codigo 0.
# Espera 1 min antes del 2do intento (cortes breves) y 5 min entre los demas.
function Correr([string]$nombre, [string[]]$argumentos = @(),
                [int]$maxIntentos = $MaxIntentos, [int]$maxMinutos = $MaxMinutos) {
    $ruta   = Join-Path $Raiz $nombre
    $inicio = Get-Date
    for ($i = 1; $i -le $maxIntentos; $i++) {
        Estado "CORRIENDO $nombre (intento $i, desde las $($inicio.ToString('HH:mm')))"
        Escribir ">>> $nombre $($argumentos -join ' ') (intento $i de $maxIntentos)"
        & $Python $ruta @argumentos *>&1 | Out-File $Log -Append -Encoding utf8
        if ($LASTEXITCODE -eq 0) { Escribir "OK  $nombre"; return $true }
        Escribir "FALLO $nombre (codigo $LASTEXITCODE)"

        $minutos = ((Get-Date) - $inicio).TotalMinutes
        if ($i -ge $maxIntentos -or $minutos -ge $maxMinutos) { break }
        $espera = if ($i -eq 1) { 60 } else { 300 }
        Escribir "    reintento en $espera s"
        Start-Sleep -Seconds $espera
    }
    Escribir "FALLO DEFINITIVO $nombre"
    return $false
}

# --- Guarda anti-duplicado -------------------------------------------------
if ((Test-Path $Log) -and (Select-String -Path $Log -Pattern '=== fin' -Quiet)) {
    Estado "SIN ACCION: la cadena de hoy ya corrio (ver $(Split-Path $Log -Leaf))"
    exit 0
}

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') :: pipeline diario ===" | Out-File $Log -Encoding utf8
$fallos = 0

# --- 01 -> 02 -> 03 ----------------------------------------------------------
$okExtraccion = Correr "01_extraer_ventas.py"
if (-not $okExtraccion) { $fallos++ }

if ($okExtraccion) {
    # pocos intentos: si la conciliacion falla no rompe nada aguas abajo
    if (-not (Correr "02_conciliar_ventas.py" @("--corregir") 2 10)) { $fallos++ }
} else {
    Escribir "SALTEADO 02_conciliar_ventas.py (la extraccion fallo)"
}

if (-not (Correr "03_foto_stock.py")) { $fallos++ }

# --- Limpieza: logs de mas de 60 dias --------------------------------------
Get-ChildItem $LogDir -Recurse -Filter "pipeline_*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-60) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Escribir "=== fin: $fallos fallo(s) ==="
if ($fallos -eq 0) { Estado "TERMINADO OK" } else { Estado "TERMINADO CON $fallos FALLO(S): revisar $(Split-Path $Log -Leaf)" }
exit $fallos
