param(
    [string]$Version = '__VERSION__',
    [string]$Repository = 'tedliou/td-cli',
    [string]$AssetBaseUri = '',
    [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\touchdesigner-cli",
    [switch]$NonInteractive,
    [switch]$NoPathUpdate
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $AssetBaseUri) { $AssetBaseUri = "https://github.com/$Repository/releases/download/v$Version" }
$names = @(
    "td-v$Version-windows-x86_64.zip",
    "td-daemon-v$Version-windows-x86_64.zip",
    "td-agent-cli-v$Version-windows-x86_64.zip",
    "td-agent-component-v$Version-td2025.32050.zip"
)
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("td-cli-install-" + [guid]::NewGuid())
$versionRoot = Join-Path $InstallRoot "versions\$Version"
$current = Join-Path $InstallRoot 'current'
$next = Join-Path $InstallRoot 'current.next'
$previous = Join-Path $InstallRoot 'current.previous'

function Compare-SemVer([string]$Left, [string]$Right) {
    $pattern = '^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$'
    if ($Left -notmatch $pattern) { throw "Invalid installed SemVer: $Left" }
    $leftParts = @([int]$Matches[1], [int]$Matches[2], [int]$Matches[3]); $leftPre = $Matches[4]
    if ($Right -notmatch $pattern) { throw "Invalid requested SemVer: $Right" }
    $rightParts = @([int]$Matches[1], [int]$Matches[2], [int]$Matches[3]); $rightPre = $Matches[4]
    for ($index = 0; $index -lt 3; $index++) {
        if ($leftParts[$index] -lt $rightParts[$index]) { return -1 }
        if ($leftParts[$index] -gt $rightParts[$index]) { return 1 }
    }
    if (-not $leftPre -and $rightPre) { return 1 }
    if ($leftPre -and -not $rightPre) { return -1 }
    $leftIdentifiers = @($leftPre -split '\.'); $rightIdentifiers = @($rightPre -split '\.')
    $count = [Math]::Min($leftIdentifiers.Count, $rightIdentifiers.Count)
    for ($index = 0; $index -lt $count; $index++) {
        $leftNumber = 0; $rightNumber = 0
        $leftNumeric = [int]::TryParse($leftIdentifiers[$index], [ref]$leftNumber)
        $rightNumeric = [int]::TryParse($rightIdentifiers[$index], [ref]$rightNumber)
        if ($leftNumeric -and $rightNumeric) {
            if ($leftNumber -lt $rightNumber) { return -1 }; if ($leftNumber -gt $rightNumber) { return 1 }
        } elseif ($leftNumeric) { return -1
        } elseif ($rightNumeric) { return 1
        } else {
            $comparison = [string]::CompareOrdinal($leftIdentifiers[$index], $rightIdentifiers[$index])
            if ($comparison -ne 0) { return $comparison }
        }
    }
    return $leftIdentifiers.Count.CompareTo($rightIdentifiers.Count)
}

function Test-InstalledFiles([string]$Root, [string[]]$Archives, [string]$Scratch) {
    $verify = Join-Path $Scratch 'verify'
    New-Item -ItemType Directory -Force -Path $verify | Out-Null
    foreach ($archive in $Archives) { Expand-Archive (Join-Path $Scratch $archive) $verify -Force }
    foreach ($file in Get-ChildItem $verify -File) {
        $installed = Join-Path $Root $file.Name
        if (-not (Test-Path $installed) -or (Get-FileHash $installed).Hash -ne (Get-FileHash $file.FullName).Hash) { return $false }
    }
    return $true
}

try {
    New-Item -ItemType Directory -Force -Path $temporary | Out-Null
    Invoke-WebRequest "$AssetBaseUri/SHA256SUMS" -OutFile (Join-Path $temporary 'SHA256SUMS')
    $expected = @{}
    foreach ($line in Get-Content (Join-Path $temporary 'SHA256SUMS')) {
        if ($line -notmatch '^([0-9a-f]{64})  (.+\.zip)$') { throw 'Invalid SHA256SUMS format' }
        $expected[$Matches[2]] = $Matches[1]
    }
    foreach ($name in $names) {
        if (-not $expected.ContainsKey($name)) { throw "Checksum missing for $name" }
        $path = Join-Path $temporary $name
        Invoke-WebRequest "$AssetBaseUri/$name" -OutFile $path
        if ((Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected[$name]) {
            throw "Checksum mismatch for $name"
        }
    }
    $currentVersion = $null
    if (Test-Path $current) {
        $target = (Get-Item $current).Target | Select-Object -First 1
        if ($target) { $currentVersion = Split-Path $target -Leaf }
    }
    if ($currentVersion -eq $Version) {
        if (-not (Test-InstalledFiles $versionRoot $names $temporary)) { throw 'Current installation failed verification' }
        Write-Output "td-cli $Version is already installed and verified"
        return
    }
    if ($currentVersion -and (Compare-SemVer $Version $currentVersion) -lt 0) {
        if ($NonInteractive) { throw "Noninteractive downgrade from $currentVersion to $Version is rejected" }
        $latest = (Invoke-RestMethod "https://api.github.com/repos/$Repository/releases/latest").tag_name.TrimStart('v')
        if ($Version -eq $latest) { throw "Implicit downgrade from $currentVersion to $Version is rejected" }
        if ((Read-Host "Downgrade td-cli from $currentVersion to $Version? Type DOWNGRADE") -ne 'DOWNGRADE') { throw 'Downgrade cancelled' }
    }
    if (Test-Path $versionRoot) {
        if (-not (Test-InstalledFiles $versionRoot $names $temporary)) { throw 'Existing version directory failed verification' }
    } else {
        New-Item -ItemType Directory -Force -Path $versionRoot | Out-Null
        foreach ($name in $names) { Expand-Archive (Join-Path $temporary $name) $versionRoot -Force }
    }
    $oldDaemon = Join-Path $current 'td-daemon.exe'
    if (Test-Path $oldDaemon) { & $oldDaemon stop 2>$null | Out-Null }
    Remove-Item $next -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Junction -Path $next -Target $versionRoot | Out-Null
    Remove-Item $previous -Force -ErrorAction SilentlyContinue
    if (Test-Path $current) { Move-Item $current $previous }
    Move-Item $next $current
    Remove-Item $previous -Force -ErrorAction SilentlyContinue
    if (-not $NoPathUpdate) {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $parts = @($userPath -split ';' | Where-Object { $_ -and $_ -ne $current }) + $current
        [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
    }
    Write-Output "Installed td-cli $Version to $versionRoot"
} catch {
    if (-not (Test-Path $current) -and (Test-Path $previous)) { Move-Item $previous $current }
    throw
} finally {
    Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $next -Force -ErrorAction SilentlyContinue
}
