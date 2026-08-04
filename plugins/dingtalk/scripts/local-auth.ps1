param(
    [ValidateSet('health', 'login', 'logout')]
    [string]$Action = 'health'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$dws = $env:WEGENT_LOCAL_AUTH_TOOL

function Write-Status([string]$Status, [string]$Hint) {
    @{ status = $Status; hint = $Hint } | ConvertTo-Json -Compress
}

function Test-Authenticated {
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
        & $dws auth login --recommend --format json *> $null
        if ($LASTEXITCODE -eq 0 -and (Test-Authenticated)) {
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
        & $dws auth logout --yes --format json *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Status 'ok' 'DingTalk login was removed.'
        } else {
            Write-Status 'error' 'Unable to remove the DingTalk login.'
        }
    }
}
