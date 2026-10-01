<#
============================================================
 guardar_credencial.ps1  -  UNA sola vez, en una ventana normal de PowerShell
============================================================
PARA QUE SIRVE
  Guarda la cuenta del Power BI Service para que actualizar_y_publicar.ps1
  pueda publicar sin pedir login. Antes de guardarla prueba que el login
  funcione y que el workspace sea accesible.

  La guarda cifrada con DPAPI (Export-Clixml): solo la puede leer ESTE
  usuario de Windows en ESTA computadora. Igual, NO se sube al repositorio
  (esta en .gitignore).

  Si el login falla por MFA, una contrasena guardada no sirve para una tarea
  desatendida: hace falta un "service principal" (una aplicacion de Entra ID
  habilitada para las API de Power BI, con permiso en el workspace).

USO
  powershell -ExecutionPolicy Bypass -File guardar_credencial.ps1 -Workspace "Ventas"
============================================================
#>
param(
    [string]$Workspace = "Mi Workspace",
    [string]$RutaCredencial = (Join-Path $PSScriptRoot ".credencial_pbi.xml")
)
$ErrorActionPreference = 'Stop'
Import-Module MicrosoftPowerBIMgmt.Profile, MicrosoftPowerBIMgmt.Workspaces

$cred = Get-Credential -Message "Cuenta de app.powerbi.com"
try {
    Connect-PowerBIServiceAccount -Credential $cred | Out-Null
} catch {
    Write-Host "Fallo el login: $($_.Exception.Message)" -ForegroundColor Red
    if ("$($_.Exception.Message)" -match 'AADSTS50076|multi.?factor|MFA') {
        Write-Host "La cuenta exige MFA: hace falta un service principal (ver el encabezado)." -ForegroundColor Yellow
    }
    exit 1
}
$ws = Get-PowerBIWorkspace -Name $Workspace
if (-not $ws) {
    Write-Host "Login OK, pero no se encontro el workspace '$Workspace'. Visibles:" -ForegroundColor Red
    Get-PowerBIWorkspace | ForEach-Object { Write-Host "  - $($_.Name)" }
    exit 1
}
Disconnect-PowerBIServiceAccount
$cred | Export-Clixml -Path $RutaCredencial
Write-Host "Credencial guardada (cifrada) en $RutaCredencial" -ForegroundColor Green
