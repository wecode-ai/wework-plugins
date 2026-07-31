param(
    [switch]$PrintPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$dwsVersion = '1.0.46'

function Test-Dws {
    param([string]$Executable)
    if ([string]::IsNullOrWhiteSpace($Executable)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    & $Executable version *> $null
    return $LASTEXITCODE -eq 0
}

function Resolve-Dws {
    $command = Get-Command dws -ErrorAction SilentlyContinue
    if ($null -ne $command -and (Test-Dws $command.Source)) {
        return $command.Source
    }
    $cached = Join-Path $env:LOCALAPPDATA `
        "Wegent\tools\dws\$dwsVersion\win32-x64\dws.exe"
    if (Test-Dws $cached) {
        return $cached
    }
    return $null
}

$existing = Resolve-Dws
if (Test-Dws $existing) {
    Write-Output $existing
    exit 0
}

if ($env:PROCESSOR_ARCHITECTURE.ToUpperInvariant() -ne 'AMD64') {
    throw "The verified DWS 1.0.46 manifest supports Windows x64, not $env:PROCESSOR_ARCHITECTURE."
}

$archiveUrl = 'https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/dws-cli/1.0.46/win32-x64-1783747458807783552.zip'
$expectedHash = '65ce216a994d575cb686615463715f67d9bfb2f709f93a71196d16627e0d3b48'
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("wegent-dws-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null

try {
    $archivePath = Join-Path $temporaryDirectory 'dws.zip'
    $lastError = $null
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath -UseBasicParsing
            $lastError = $null
            break
        } catch {
            $lastError = $_
            if ($attempt -lt 4) {
                Start-Sleep -Seconds ([Math]::Min($attempt * 2, 6))
            }
        }
    }
    if ($null -ne $lastError) {
        throw $lastError
    }

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw 'The downloaded DWS archive failed SHA-256 verification.'
    }

    $expandedPath = Join-Path $temporaryDirectory 'expanded'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $expandedPath
    $source = Get-ChildItem -LiteralPath $expandedPath -Filter dws.exe -Recurse |
        Select-Object -First 1
    if ($null -eq $source) {
        throw 'The verified DWS archive did not contain dws.exe.'
    }

    $installDirectory = Join-Path $env:LOCALAPPDATA `
        "Wegent\tools\dws\$dwsVersion\win32-x64"
    New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
    $installTarget = Join-Path $installDirectory 'dws.exe'
    Copy-Item -LiteralPath $source.FullName -Destination $installTarget -Force
    if (-not (Test-Dws $installTarget)) {
        throw "The verified DWS binary is not runnable at $installTarget."
    }
    Write-Output $installTarget
} finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
