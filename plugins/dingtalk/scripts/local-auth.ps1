param(
    [ValidateSet('health', 'login', 'logout')]
    [string]$Action = 'health'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDirectory 'invoke-dws.ps1')

$dws = $env:WEGENT_LOCAL_AUTH_TOOL

function Write-Status([string]$Status, [string]$Hint) {
    @{ status = $Status; hint = $Hint } | ConvertTo-Json -Compress
}

function Limit-StatusReason([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ''
    }
    $safe = ($Value -replace '[^A-Za-z0-9_.-]', '').Trim()
    if ($safe.Length -gt 80) {
        return $safe.Substring(0, 80)
    }
    return $safe
}

function Get-AuthenticationState {
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('auth', 'status', '--format', 'json') `
        -OutputMode stdout
    if ($result.ExitCode -ne 0) {
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = "DWS auth status exited with code $($result.ExitCode)."
        }
    }
    $statusText = $result.Output
    if ([string]::IsNullOrWhiteSpace($statusText)) {
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = 'DWS auth status returned no output.'
        }
    }
    try {
        $status = $statusText | ConvertFrom-Json
        if ($status.authenticated -eq $true) {
            return [PSCustomObject]@{ Authenticated = $true; Hint = '' }
        }
        $details = @()
        if ($null -ne $status.PSObject.Properties['reason']) {
            $reason = Limit-StatusReason ([string]$status.reason)
            if (-not [string]::IsNullOrWhiteSpace($reason)) {
                $details += "reason=$reason"
            }
        }
        if ($null -ne $status.PSObject.Properties['token_valid']) {
            $details += "token_valid=$($status.token_valid -eq $true)"
        }
        if ($null -ne $status.PSObject.Properties['refresh_token_valid']) {
            $details += "refresh_token_valid=$($status.refresh_token_valid -eq $true)"
        }
        $detail = $details -join '; '
        if ([string]::IsNullOrWhiteSpace($detail)) {
            $detail = 'authenticated=false'
        }
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = "DWS auth status reported $detail."
        }
    } catch {
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = 'DWS auth status returned invalid JSON.'
        }
    }
}

function Test-Authenticated {
    return (Get-AuthenticationState).Authenticated
}

function Invoke-Login {
    # Installation authentication ends with the OAuth callback. Recommended
    # PAT permissions are operation-specific and must not keep this dialog open.
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('auth', 'login', '--format', 'json')
    return $result.ExitCode
}

function Wait-Authenticated {
    param(
        [int]$Attempts = 10,
        [int]$DelayMilliseconds = 500
    )

    $state = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $state = Get-AuthenticationState
        if ($state.Authenticated) {
            return $state
        }
        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
    return $state
}

if ([string]::IsNullOrWhiteSpace($dws) -or -not (Test-Path -LiteralPath $dws -PathType Leaf)) {
    Write-Status 'error' 'The bundled DWS CLI is unavailable.'
    exit 0
}

switch ($Action) {
    'health' {
        if (Test-Authenticated) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
        } else {
            Write-Status 'need_login' 'DingTalk authorization is required.'
        }
    }
    'login' {
        if (Test-Authenticated) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
            exit 0
        }
        $loginExitCode = Invoke-Login
        # The browser can finish OAuth and persist the credential even when the
        # native command reports a non-zero exit on Windows. The stored state is
        # authoritative, and a short wait covers credential-store visibility.
        $authState = Wait-Authenticated
        if ($authState.Authenticated) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
        } elseif ($loginExitCode -ne 0) {
            Write-Status 'error' "DingTalk OAuth command failed (exit code $loginExitCode); $($authState.Hint)"
        } else {
            Write-Status 'error' "DingTalk OAuth callback completed, but $($authState.Hint)"
        }
    }
    'logout' {
        if (-not (Test-Authenticated)) {
            Write-Status 'ok' 'DingTalk is already logged out.'
            exit 0
        }
        $result = Invoke-DwsCommand -Executable $dws `
            -Arguments @('auth', 'logout', '--yes', '--format', 'json')
        if ($result.ExitCode -eq 0) {
            Write-Status 'ok' 'DingTalk login was removed.'
        } else {
            Write-Status 'error' 'Unable to remove the DingTalk login.'
        }
    }
}
