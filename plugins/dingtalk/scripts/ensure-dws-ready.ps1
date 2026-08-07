$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$dws = & (Join-Path $scriptDirectory 'install-dws.ps1') -PrintPath |
    Select-Object -Last 1

function Test-AuthenticatedStatus {
    $statusText = (& $dws auth status --format json 2>$null) -join "`n"
    if ([string]::IsNullOrWhiteSpace($statusText)) {
        return $false
    }
    try {
        $status = $statusText | ConvertFrom-Json
        return $status.authenticated -eq $true
    } catch {
        return $false
    }
}

function Get-AuthProbeResult {
    $probeText = (& $dws contact user get-self --format json 2>&1) -join "`n"
    if ($LASTEXITCODE -eq 0) {
        return 'authenticated'
    }
    if ($probeText -match 'not_authenticated|AUTH_TOKEN_EXPIRED|USER_TOKEN_ILLEGAL|未登录|Token验证失败') {
        return 'unauthenticated'
    }
    return 'unavailable'
}

$loginRequired = -not (Test-AuthenticatedStatus)
if (-not $loginRequired) {
    $probe = Get-AuthProbeResult
    if ($probe -eq 'unauthenticated') {
        $loginRequired = $true
    } elseif ($probe -eq 'unavailable') {
        Write-Warning 'DWS reports a local login; the read-only auth probe was unavailable for a non-authentication reason.'
    }
}

if ($loginRequired) {
    Write-Host 'DWS is not authenticated. Opening DingTalk browser authorization...'
    & $dws auth login --recommend --yes --format json
    if ($LASTEXITCODE -ne 0) {
        throw 'DWS browser authorization failed.'
    }
}

if (-not (Test-AuthenticatedStatus)) {
    throw 'DWS authorization did not produce a usable local login.'
}
$finalProbe = Get-AuthProbeResult
if ($finalProbe -eq 'unauthenticated') {
    throw 'DWS authorization completed, but the local token is still rejected.'
}
Write-Host 'DWS is installed and authenticated.'
