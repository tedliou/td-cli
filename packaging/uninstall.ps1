param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\touchdesigner-cli",
    [switch]$PurgeData,
    [switch]$NonInteractive,
    [switch]$NoPathUpdate
)
$ErrorActionPreference = 'Stop'
$current = Join-Path $InstallRoot 'current'
$daemon = Join-Path $current 'td-daemon.exe'
if (Test-Path $daemon) { & $daemon stop 2>$null | Out-Null }
if (-not $NoPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = $userPath -split ';' | Where-Object { $_ -and $_ -ne $current }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}
if (Test-Path $InstallRoot) { Remove-Item $InstallRoot -Recurse -Force }
if ($PurgeData) {
    if ($NonInteractive) { throw 'Data purge is never allowed noninteractively' }
    $answer = Read-Host 'Delete daemon data, token, and logs? Type PURGE'
    if ($answer -eq 'PURGE') { Remove-Item "$env:LOCALAPPDATA\touchdesigner-cli" -Recurse -Force -ErrorAction SilentlyContinue }
}
Write-Output 'Uninstalled td-cli; daemon data and TouchDesigner projects were preserved'
