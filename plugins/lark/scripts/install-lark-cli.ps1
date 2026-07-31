$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$larkVersion = '1.0.68'

function Test-LarkCli {
    param([string]$Executable)
    if ([string]::IsNullOrWhiteSpace($Executable)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    $versionOutput = (& $Executable --version 2>$null | Out-String)
    return $LASTEXITCODE -eq 0 -and $versionOutput.Contains($larkVersion)
}

if ($env:PROCESSOR_ARCHITECTURE.ToUpperInvariant() -ne 'AMD64') {
    throw "The Lark CLI 1.0.68 manifest supports Windows x64, not $env:PROCESSOR_ARCHITECTURE."
}

$installDirectory = Join-Path $env:LOCALAPPDATA `
    "Wegent\tools\lark-cli\$larkVersion\win32-x64"
$installTarget = Join-Path $installDirectory 'lark-cli.exe'
if (Test-LarkCli $installTarget) {
    Write-Output $installTarget
    exit 0
}

$pathCommand = Get-Command lark-cli -ErrorAction SilentlyContinue
if ($null -ne $pathCommand -and (Test-LarkCli $pathCommand.Source)) {
    Write-Output $pathCommand.Source
    exit 0
}

$primaryUrl = 'https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/lark-cli/1.0.68/windows-amd64.zip'
$fallbackUrl = 'https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/lark-cli/1.0.69/windows-x64-1784086368245859794.zip'
$expectedHash = 'd593f658151a0de1ab26b89ee8ff1d93a216777b8120db0b048013468ff63a1d'
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("wegent-lark-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null

try {
    $archivePath = Join-Path $temporaryDirectory 'lark-cli.zip'
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
        throw 'Both official Lark CLI download mirrors failed.'
    }

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw 'The downloaded Lark CLI archive failed SHA-256 verification.'
    }

    $expandedPath = Join-Path $temporaryDirectory 'expanded'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $expandedPath
    $source = Get-ChildItem -LiteralPath $expandedPath -Filter lark-cli.exe -Recurse |
        Select-Object -First 1
    if ($null -eq $source) {
        throw 'The verified archive did not contain lark-cli.exe.'
    }

    New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
    Copy-Item -LiteralPath $source.FullName -Destination $installTarget -Force
    if (-not (Test-LarkCli $installTarget)) {
        throw "The verified Lark CLI binary is not version $larkVersion."
    }
    Write-Output $installTarget
} finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
