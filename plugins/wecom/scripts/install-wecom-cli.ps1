param(
    [switch]$PrintPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$wecomVersion = '0.1.9'

function Test-WeComCli {
    param([string]$Executable)
    if ([string]::IsNullOrWhiteSpace($Executable)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    & $Executable --version *> $null
    return $LASTEXITCODE -eq 0
}

function Resolve-WeComCli {
    $command = Get-Command wecom-cli -ErrorAction SilentlyContinue
    if ($null -ne $command -and (Test-WeComCli $command.Source)) {
        return $command.Source
    }
    $cached = Join-Path $env:LOCALAPPDATA `
        "Wegent\tools\wecom-cli\$wecomVersion\win32-x64\wecom-cli.exe"
    if (Test-WeComCli $cached) {
        return $cached
    }
    return $null
}

$existing = Resolve-WeComCli
if (Test-WeComCli $existing) {
    Write-Output $existing
    exit 0
}

if ($env:PROCESSOR_ARCHITECTURE.ToUpperInvariant() -ne 'AMD64') {
    throw "The official WeCom CLI 0.1.9 manifest supports Windows x64, not $env:PROCESSOR_ARCHITECTURE."
}

$primaryUrl = 'https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/wecom-cli/0.1.9/win32-x64-1783946896354509143.zip'
$fallbackUrl = 'https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/wecom-cli/0.1.9/win32-x64-1784086407740594051.zip'
$expectedHash = '28e30dbff4d29634ccf3b1efbfbf98fa43ecc3f23bf147eee1458d8e5e2d9416'
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("wegent-wecom-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null

try {
    $archivePath = Join-Path $temporaryDirectory 'wecom-cli.zip'
    $downloaded = $false
    foreach ($uri in @($primaryUrl, $fallbackUrl)) {
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Invoke-WebRequest -Uri $uri -OutFile $archivePath -UseBasicParsing
                $downloaded = $true
                break
            } catch {
                if ($attempt -lt 3) {
                    Start-Sleep -Seconds ([Math]::Min($attempt * 2, 6))
                }
            }
        }
        if ($downloaded) {
            break
        }
    }
    if (-not $downloaded) {
        throw 'Both official WeCom CLI download mirrors failed.'
    }

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw 'The downloaded WeCom CLI archive failed SHA-256 verification.'
    }

    $expandedPath = Join-Path $temporaryDirectory 'expanded'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $expandedPath
    $source = Get-ChildItem -LiteralPath $expandedPath -Filter wecom-cli.exe -Recurse |
        Select-Object -First 1
    if ($null -eq $source) {
        throw 'The verified archive did not contain wecom-cli.exe.'
    }

    $installDirectory = Join-Path $env:LOCALAPPDATA `
        "Wegent\tools\wecom-cli\$wecomVersion\win32-x64"
    New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
    $installTarget = Join-Path $installDirectory 'wecom-cli.exe'
    Copy-Item -LiteralPath $source.FullName -Destination $installTarget -Force
    if (-not (Test-WeComCli $installTarget)) {
        throw "The verified WeCom CLI binary is not runnable at $installTarget."
    }
    Write-Output $installTarget
} finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}

