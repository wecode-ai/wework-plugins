$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDirectory 'invoke-dws.ps1')
$dws = & (Join-Path $scriptDirectory 'install-dws.ps1') -PrintPath |
    Select-Object -Last 1

function Test-AuthenticatedStatus {
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('auth', 'status', '--format', 'json') `
        -OutputMode stdout
    $statusText = $result.Output
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
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('contact', 'user', 'get-self', '--format', 'json') `
        -OutputMode all
    if ($result.ExitCode -eq 0) {
        return 'authenticated'
    }
    $probeText = $result.Output
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
    # Complete local OAuth login without blocking on operation-specific PAT
    # permissions, which DWS handles when a command requires them.
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('auth', 'login', '--format', 'json')
    if ($result.ExitCode -ne 0) {
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
