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

function Test-Authenticated {
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

function Grant-RecommendedPermissions {
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('pat', 'chmod', '--recommend', '--yes', '--format', 'json')
    return ($result.ExitCode -eq 0)
}

function Invoke-LoginWithRecommendedPermissions {
    # JSON mode returns PAT_BATCH_AUTH_PENDING to the host immediately. Table
    # mode keeps the DWS-owned browser flow running until authorization ends.
    $result = Invoke-DwsCommand -Executable $dws `
        -Arguments @('auth', 'login', '--recommend', '--format', 'table')
    return ($result.ExitCode -eq 0)
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
            if (Grant-RecommendedPermissions) {
                Write-Status 'ok' 'DingTalk authorization is ready.'
            } else {
                Write-Status 'error' 'DingTalk recommended permission authorization did not complete.'
            }
            exit 0
        }
        if ((Invoke-LoginWithRecommendedPermissions) -and (Test-Authenticated)) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
        } else {
            Write-Status 'error' 'DingTalk browser authorization did not complete.'
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
