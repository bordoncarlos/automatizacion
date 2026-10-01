<#
============================================================
 actualizar_y_publicar.ps1
 Refresca un .pbix en Power BI Desktop, lo guarda y lo publica al Service.
 Cada 10 minutos, con licencia Pro y SIN gateway.
============================================================
LA IDEA
  Con Pro, el Service permite 8 actualizaciones programadas por dia, y para
  leer archivos de la PC necesita un gateway. Pero Power BI Desktop trae
  adentro su propio motor (Analysis Services): el refresh se hace AHI, se
  guarda el .pbix y se vuelve a publicar. Publicar no es una actualizacion
  programada (no cuenta contra el tope) y los datos viajan dentro del
  archivo (no hace falta gateway).

LOS PASOS
  1. Encontrar Desktop con el informe abierto y el puerto de su motor.
  2. Refresh completo por XMLA, con tope de tiempo.
  3. Marcar el modelo como modificado (si no, "Guardar" no guarda nada).
  4. Guardar por UI Automation (sin robar el foco) y VERIFICAR que se guardo.
  5. Publicar al workspace.

CODIGOS DE SALIDA
  0 OK | 1 faltan requisitos | 2 Desktop o su motor no aparecen
  3 error o tiempo agotado en el refresh | 4 no se guardo (no publica) | 5 error al publicar

USO
  powershell -ExecutionPolicy Bypass -File actualizar_y_publicar.ps1 -Pbix "C:\ruta\Informe.pbix" -Workspace "Ventas"
  ... -SoloRefrescar      # refresca y guarda, sin publicar (para probar)

REQUISITOS
  Install-Module SqlServer -Scope CurrentUser               (Invoke-ASCmd y TOM)
  Install-Module MicrosoftPowerBIMgmt -Scope CurrentUser    (publicar)
  guardar_credencial.ps1, una sola vez.
============================================================
#>
param(
    [string]$Pbix = (Join-Path (Split-Path $PSScriptRoot -Parent) "Informe.pbix"),
    [string]$Workspace = "Mi Workspace",
    [string]$RutaCredencial = (Join-Path $PSScriptRoot ".credencial_pbi.xml"),
    [int]$RefreshTimeoutSeg = 420,      # el refresh mas lento medido rondaba 5 min: 7 dan margen
    [switch]$SoloRefrescar
)

$ErrorActionPreference = 'Stop'
$NombrePbix = [System.IO.Path]::GetFileNameWithoutExtension($Pbix)   # asi empieza el titulo de la ventana
$LogDir     = Join-Path (Split-Path $PSScriptRoot -Parent) "logs"
$LogMes     = Join-Path $LogDir (Get-Date -Format "yyyy-MM")
New-Item -ItemType Directory -Path $LogMes -Force | Out-Null
$Log        = Join-Path $LogMes ("publicar_pbi_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$EstadoTxt  = Join-Path $LogDir "publicar_pbi_estado.txt"
$FallosTxt  = Join-Path $LogDir "publicar_pbi_fallos.txt"

function Escribir([string]$msg) { "$(Get-Date -Format 'HH:mm:ss') $msg" | Out-File $Log -Append -Encoding utf8 }
function Estado([string]$txt)   { "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')] $txt" | Out-File $EstadoTxt -Encoding utf8 }
function Salir([int]$codigo, [string]$msg) { Escribir $msg; Estado $msg; exit $codigo }

Escribir "=== inicio ciclo (SoloRefrescar=$SoloRefrescar) ==="

# --- 0. Requisitos ---------------------------------------------------------------
if (-not (Test-Path $Pbix)) { Salir 1 "ERROR: no existe el pbix: $Pbix" }
if (-not (Get-Module -ListAvailable SqlServer)) { Salir 1 "ERROR: falta el modulo SqlServer" }
if (-not $SoloRefrescar) {
    if (-not (Get-Module -ListAvailable MicrosoftPowerBIMgmt.Profile)) { Salir 1 "ERROR: falta el modulo MicrosoftPowerBIMgmt" }
    if (-not (Test-Path $RutaCredencial)) { Salir 1 "ERROR: falta la credencial: correr guardar_credencial.ps1" }
}
Import-Module SqlServer -DisableNameChecking

# --- 1. Desktop, su motor y el modelo ----------------------------------------------
# Deteccion en DOS capas: por el titulo de la ventana y, si un dialogo lo tapa,
# por la linea de comando del proceso (lleva la ruta del .pbix). Nunca se abre
# una segunda instancia del mismo archivo: dos ventanas del mismo informe
# terminan guardando una encima de la otra.
function Buscar-Desktop {
    $p = Get-Process PBIDesktop -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -like "$NombrePbix*" } | Select-Object -First 1
    if ($p) { return $p }
    $patron = [regex]::Escape((Split-Path $Pbix -Leaf))
    $cim = Get-CimInstance Win32_Process -Filter "Name='PBIDesktop.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match $patron } | Select-Object -First 1
    if ($cim) { return (Get-Process -Id $cim.ProcessId -ErrorAction SilentlyContinue) }
    return $null
}

$desktop = Buscar-Desktop
if (-not $desktop -and (Get-Process PBIDesktop -ErrorAction SilentlyContinue)) {
    # Hay algun Power BI abierto que no coincide: puede ser el nuestro tapado
    # por un dialogo pasajero. Mejor un ciclo tarde que una ventana duplicada.
    Escribir "Hay Power BI abierto sin coincidir con '$NombrePbix': reintentando 2 min"
    $tope = (Get-Date).AddMinutes(2)
    while (-not $desktop -and (Get-Date) -lt $tope) { Start-Sleep -Seconds 20; $desktop = Buscar-Desktop }
}
if (-not $desktop) {
    Estado "ABRIENDO Desktop (el primer ciclo del dia tarda mas)"
    Start-Process $Pbix
    $tope = (Get-Date).AddMinutes(8)
    while (-not $desktop -and (Get-Date) -lt $tope) { Start-Sleep -Seconds 15; $desktop = Buscar-Desktop }
    if (-not $desktop) { Salir 2 "ERROR: Desktop no abrio '$NombrePbix' en 8 min" }
}

# El motor es msmdsrv.exe, proceso HIJO de PBIDesktop.exe. Su puerto cambia
# cada vez que se abre Desktop, asi que se busca en cada ciclo.
function Buscar-Puerto([int]$pidDesktop) {
    $motor = Get-CimInstance Win32_Process -Filter "Name='msmdsrv.exe'" |
        Where-Object { $_.ParentProcessId -eq $pidDesktop } | Select-Object -First 1
    if (-not $motor) { return $null }
    $c = Get-NetTCPConnection -OwningProcess $motor.ProcessId -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($c) { return $c.LocalPort } else { return $null }
}
$puerto = $null
$tope = (Get-Date).AddMinutes(8)
while (-not $puerto -and (Get-Date) -lt $tope) {
    $puerto = Buscar-Puerto $desktop.Id
    if (-not $puerto) { Start-Sleep -Seconds 15 }
}
if (-not $puerto) { Salir 2 "ERROR: no aparecio el puerto del motor de Desktop" }
$servidor = "localhost:$puerto"

# El nombre de la base del modelo es un GUID; sale de una DMV.
$catalogo = $null
$tope = (Get-Date).AddMinutes(8)
while (-not $catalogo -and (Get-Date) -lt $tope) {
    try {
        $xml = Invoke-ASCmd -Server $servidor -Query 'SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS'
        if ($xml -match '<CATALOG_NAME>([^<]+)</CATALOG_NAME>') { $catalogo = $Matches[1] }
    } catch { }
    if (-not $catalogo) { Start-Sleep -Seconds 15 }
}
if (-not $catalogo) { Salir 2 "ERROR: el modelo no termino de cargar en $servidor" }
Escribir "Desktop PID $($desktop.Id), motor en $servidor, modelo $catalogo"

# --- Defensa contra un refresh colgado -------------------------------------------
# Matar al cliente NO cancela el refresh: el motor sigue con los jobs abiertos,
# el modelo queda bloqueado y cada refresh nuevo se encola detras y se cuelga
# igual. El <Cancel> de XMLA responde OK y se ignora. Lo unico que destraba es
# cerrar Desktop; el ciclo siguiente lo reabre limpio.
function Cerrar-Desktop([string]$motivo) {
    Escribir "$motivo -> cerrando Desktop (PID $($desktop.Id)); el proximo ciclo lo reabre"
    Stop-Process -Id $desktop.Id -Force -ErrorAction SilentlyContinue
}

# Minutos que lleva cada job 'Process' vivo en el motor. Un refresh sano no
# deja ninguno. La columna del tiempo es JOB_TOTAL_TIME_MS.
function Jobs-Process([string]$srv) {
    $minutos = @()
    try { $xml = Invoke-ASCmd -Server $srv -Query 'SELECT * FROM $SYSTEM.DISCOVER_JOBS' -QueryTimeout 60 }
    catch { return $minutos }
    foreach ($m in [regex]::Matches($xml, '(?s)<row\b.*?</row>')) {
        if ($m.Value -match '<JOB_DESCRIPTION>\s*Process\s*</JOB_DESCRIPTION>' -and
            $m.Value -match '<JOB_TOTAL_TIME_MS>(\d+)</JOB_TOTAL_TIME_MS>') {
            $minutos += [double]$Matches[1] / 60000
        }
    }
    return $minutos
}

# Al entrar: un job de 12 min o mas no es un refresh lento, es uno colgado.
$colgados = @(Jobs-Process $servidor)
if ($colgados.Count -gt 0 -and ($colgados | Measure-Object -Maximum).Maximum -ge 12) {
    Cerrar-Desktop "Quedo un refresh colgado de un ciclo anterior"
    Salir 3 "Refresh SALTEADO: el modelo estaba bloqueado"
}

# --- 2. Refresh completo por XMLA (TMSL) ----------------------------------------------
Estado "REFRESCANDO (inicio $(Get-Date -Format 'HH:mm'))"
$tmsl = '{"refresh":{"type":"full","objects":[{"database":"' + $catalogo + '"}]}}'
$t0 = Get-Date
try {
    $resp = Invoke-ASCmd -Server $servidor -Query $tmsl -QueryTimeout $RefreshTimeoutSeg
} catch {
    $min = ((Get-Date) - $t0).TotalMinutes
    # Solo se cierra Desktop si los jobs que quedaron son NUESTROS (llevan lo
    # mismo que nuestro refresh). Si llevan otro tiempo son de alguien que
    # esta refrescando a mano: cerrarle Desktop le borraria lo que no guardo.
    $nuestros = @(Jobs-Process $servidor | Where-Object { [math]::Abs($_ - $min) -le 1.5 })
    if ($nuestros.Count -gt 0) { Cerrar-Desktop "Nuestro refresh dejo jobs vivos tras $([math]::Round($min,1)) min" }
    Salir 3 ("ERROR en el refresh tras {0:N1} min: {1}" -f $min, $_.Exception.Message)
}
# Solo un <Error> corta el ciclo. Los warnings (por ejemplo, una medida que
# apunta a una tabla que ya no existe) se registran y se sigue.
if ($resp -match '<Error\b') { Escribir $resp; Salir 3 "ERROR: el refresh devolvio errores (ver log)" }
$warnings = [regex]::Matches($resp, 'Warning[^>]*Description="([^"]+)"') | ForEach-Object { $_.Groups[1].Value }
if ($warnings) { Escribir ("Refresh con {0} warning(s): {1}" -f @($warnings).Count, ($warnings -join ' | ')) }
Escribir ("Refresh OK en {0:N1} min" -f ((Get-Date) - $t0).TotalMinutes)

# --- 3. Marcar el modelo como modificado ---------------------------------------------
# El paso menos obvio: un refresh de DATOS por XMLA no le avisa a Desktop que
# hay cambios, asi que "Guardar" no hace nada y el archivo queda con los datos
# viejos, sin ningun error. Un cambio de METADATOS por TOM si lo detecta: se
# escribe una annotation con la hora del ciclo.
try {
    $srv = New-Object Microsoft.AnalysisServices.Tabular.Server
    $srv.Connect($servidor)
    $modelo = $srv.Databases.FindByName($catalogo).Model
    $ahora = (Get-Date).ToString('s')
    if ($modelo.Annotations.ContainsName('auto_publicacion')) {
        $modelo.Annotations['auto_publicacion'].Value = $ahora
    } else {
        $a = New-Object Microsoft.AnalysisServices.Tabular.Annotation
        $a.Name = 'auto_publicacion'; $a.Value = $ahora
        $modelo.Annotations.Add($a)
    }
    $modelo.SaveChanges() | Out-Null
    $srv.Disconnect()
} catch {
    Escribir "AVISO: no se pudo marcar el modelo ($($_.Exception.Message)); se intenta guardar igual"
}

# --- 4. Guardar y verificar ----------------------------------------------------------
Estado "GUARDANDO"
try { Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes } catch { }
Add-Type -Name U32 -Namespace Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int max);
[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
'@

# La ventana correcta: Process.MainWindowHandle puede apuntar a una ventana
# oculta del navegador embebido de Power BI. Se recorren las ventanas del
# proceso y se toma la de clase WindowsForms10.Window cuyo titulo empieza
# con el nombre del informe.
function Buscar-Ventana([int]$pidDesktop) {
    $script:hallada = [IntPtr]::Zero
    $cb = [Win.U32+EnumWindowsProc]{
        param($h, $l)
        $p = 0
        [Win.U32]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null
        if ($p -eq $pidDesktop -and [Win.U32]::IsWindowVisible($h)) {
            $clase = New-Object System.Text.StringBuilder 256
            $titulo = New-Object System.Text.StringBuilder 256
            [Win.U32]::GetClassName($h, $clase, 256) | Out-Null
            [Win.U32]::GetWindowText($h, $titulo, 256) | Out-Null
            if ($clase.ToString() -like 'WindowsForms10.Window*' -and $titulo.ToString().StartsWith($NombrePbix)) {
                $script:hallada = $h
                return $false                       # encontrada: cortar la busqueda
            }
        }
        return $true
    }
    [Win.U32]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    if ($script:hallada -eq [IntPtr]::Zero) { return (Get-Process -Id $pidDesktop).MainWindowHandle }
    return $script:hallada
}

# El boton Guardar de la barra de acceso rapido tiene AutomationId 'save' (su
# nombre cambia con el idioma, el id no). El patron Invoke de UI Automation NO
# necesita que la ventana este al frente: se puede seguir usando la PC.
function Invocar-Guardar([IntPtr]$hwnd) {
    try {
        $ventana = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
        $cond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'save')
        $boton = $ventana.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
        if (-not $boton) { return "no aparece el boton Guardar" }
        if (-not $boton.Current.IsEnabled) { return "el boton Guardar esta deshabilitado" }
        $boton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        return $null
    } catch { return $_.Exception.Message }
}

# La prueba de que se guardo: la fecha de modificacion del archivo avanzo.
$mtimeAntes = (Get-Item $Pbix).LastWriteTime
$guardado = $false
for ($i = 1; $i -le 3 -and -not $guardado; $i++) {
    $motivo = Invocar-Guardar (Buscar-Ventana $desktop.Id)
    if ($motivo) { Escribir "Guardar intento ${i}: $motivo"; Start-Sleep -Seconds 10; continue }
    $tope = (Get-Date).AddSeconds(45)
    while (-not $guardado -and (Get-Date) -lt $tope) {
        Start-Sleep -Seconds 3
        $guardado = (Get-Item $Pbix).LastWriteTime -gt $mtimeAntes
    }
}
if (-not $guardado) {
    # Si NO se guardo, NO se publica: nunca subir datos viejos. Tras dos fallos
    # seguidos (tipico: un dialogo modal trabado) se cierra Desktop para que el
    # ciclo siguiente lo reabra limpio.
    $fallos = 0
    if (Test-Path $FallosTxt) { [int]::TryParse((Get-Content $FallosTxt -Raw).Trim(), [ref]$fallos) | Out-Null }
    $fallos++
    if ($fallos -ge 2) { Cerrar-Desktop "Segundo guardado fallido seguido"; $fallos = 0 }
    $fallos | Out-File $FallosTxt -Encoding utf8
    Salir 4 "ERROR: el pbix NO se guardo; publicacion salteada para no subir datos viejos"
}
"0" | Out-File $FallosTxt -Encoding utf8
Escribir "Guardado OK ($((Get-Item $Pbix).LastWriteTime.ToString('HH:mm:ss')))"
if ($SoloRefrescar) { Salir 0 "OK (refrescado y guardado, sin publicar)" }

# --- 5. Publicar ---------------------------------------------------------------------
# CreateOrOverwrite reemplaza informe y modelo CONSERVANDO el mismo id: los
# enlaces y los paneles que apuntan al informe no se rompen.
Estado "PUBLICANDO en $Workspace"
Import-Module MicrosoftPowerBIMgmt.Profile, MicrosoftPowerBIMgmt.Workspaces, MicrosoftPowerBIMgmt.Reports
try {
    Connect-PowerBIServiceAccount -Credential (Import-Clixml -Path $RutaCredencial) | Out-Null
    $ws = Get-PowerBIWorkspace -Name $Workspace
    if (-not $ws) { throw "workspace '$Workspace' no encontrado para esta cuenta" }
    for ($intento = 1; $true; $intento++) {
        $t0 = Get-Date
        try { $rep = New-PowerBIReport -Path $Pbix -WorkspaceId $ws.Id -ConflictAction CreateOrOverwrite; break }
        catch {
            if ($intento -ge 2) { throw }
            Escribir "Publicar intento $intento fallo: $($_.Exception.Message); se reintenta"
            Start-Sleep -Seconds 10
        }
    }
    Escribir ("Publicado OK en {0:N1} min (informe {1})" -f ((Get-Date) - $t0).TotalMinutes, $rep.Id)
    Disconnect-PowerBIServiceAccount
} catch {
    Salir 5 "ERROR al publicar: $($_.Exception.Message)"
}
Salir 0 "OK: refrescado, guardado y publicado en $Workspace $(Get-Date -Format 'HH:mm')"
