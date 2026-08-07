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

function Grant-RecommendedPermissions {
    & $dws pat chmod --recommend --yes --format json *> $null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-LoginWithRecommendedPermissions {
    # JSON mode returns PAT_BATCH_AUTH_PENDING to the host immediately. Table
    # mode keeps the DWS-owned browser flow running until authorization ends.
    & $dws auth login --recommend --format table *> $null
    return ($LASTEXITCODE -eq 0)
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
        & $dws auth logout --yes --format json *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Status 'ok' 'DingTalk login was removed.'
        } else {
            Write-Status 'error' 'Unable to remove the DingTalk login.'
        }
    }
}
