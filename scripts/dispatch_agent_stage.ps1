param(
    [Parameter(Mandatory = $true)][string]$StagePath,
    [Parameter(Mandatory = $true)][string]$SourceCommit,
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = 'Stop'
$resolvedStage = (Resolve-Path -LiteralPath $StagePath).Path
$required = @('td-agent.tox', 'manifest.json', 'verification.json')
foreach ($name in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedStage $name) -PathType Leaf)) {
        throw "Missing staged file: $name"
    }
}

$archive = Join-Path ([IO.Path]::GetTempPath()) ("td-agent-stage-" + [guid]::NewGuid() + '.zip')
try {
    Compress-Archive -LiteralPath ($required | ForEach-Object { Join-Path $resolvedStage $_ }) -DestinationPath $archive
    $digest = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $payload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($archive))
    @{
        source_commit = $SourceCommit
        version = $Version
        stage_archive_base64 = $payload
        stage_archive_sha256 = $digest
    } | ConvertTo-Json -Compress | gh workflow run stage-agent-component.yml --ref main --json
    if ($LASTEXITCODE -ne 0) { throw 'Failed to dispatch Stage Agent Component' }
    Write-Output "STAGE_DISPATCHED source_commit=$SourceCommit sha256=$digest"
}
finally {
    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
}
