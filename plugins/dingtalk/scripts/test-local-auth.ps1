$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$authScript = Join-Path $scriptDirectory 'local-auth.ps1'
$readyScript = Join-Path $scriptDirectory 'ensure-dws-ready.ps1'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    ('wegent-dingtalk-auth-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null

function Assert-Contains([string]$Value, [string]$Expected) {
    if (-not $Value.Contains($Expected)) {
        throw "Expected output to contain '$Expected', got: $Value"
    }
}

function Assert-NotContains([string]$Value, [string]$Unexpected) {
    if ($Value.Contains($Unexpected)) {
        throw "Expected output not to contain '$Unexpected', got: $Value"
    }
}

function Invoke-AuthTest([string]$Action, [string]$StateDirectory) {
    $env:DWS_MOCK_STATE_DIR = $StateDirectory
    $env:WEGENT_LOCAL_AUTH_TOOL = $mockDws
    $powershell = Join-Path $PSHOME 'powershell.exe'
    $output = (& $powershell -NoProfile -ExecutionPolicy Bypass `
        -File $authScript $Action) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "local-auth.ps1 exited with code $LASTEXITCODE."
    }
    return $output
}

try {
    $mockDws = Join-Path $testRoot 'dws.cmd'
    @'
@echo off
if "%~1"=="version" goto version
if "%~1"=="auth" if "%~2"=="status" goto status
if "%~1"=="auth" if "%~2"=="login" goto login
if "%~1"=="auth" if "%~2"=="logout" goto logout
if "%~1"=="contact" if "%~2"=="user" goto contact
exit /b 4

:version
echo dws version 1.0.54
exit /b 0

:status
if exist "%DWS_MOCK_STATE_DIR%\authenticated" (
  echo {"authenticated":true}
) else (
  echo {"authenticated":false}
)
exit /b 0

:login
echo auth login %*>>"%DWS_MOCK_STATE_DIR%\calls"
echo Opening DingTalk browser authorization... 1>&2
if "%DWS_MOCK_LOGIN_FAIL%"=="1" exit /b 4
echo %*| findstr /c:"--format json" >nul || exit /b 4
echo %*| findstr /c:"--recommend" >nul && exit /b 4
type nul >"%DWS_MOCK_STATE_DIR%\authenticated"
exit /b 0

:logout
echo Removing local login... 1>&2
del /q "%DWS_MOCK_STATE_DIR%\authenticated" 2>nul
exit /b 0

:contact
if exist "%DWS_MOCK_STATE_DIR%\authenticated" exit /b 0
echo not_authenticated 1>&2
exit /b 4
'@ | Set-Content -LiteralPath $mockDws -Encoding Ascii

    $freshState = Join-Path $testRoot 'fresh'
    New-Item -ItemType Directory -Path $freshState | Out-Null
    $freshOutput = Invoke-AuthTest 'login' $freshState
    Assert-Contains $freshOutput '"status":"ok"'
    $freshCalls = Get-Content -LiteralPath (Join-Path $freshState 'calls') -Raw
    Assert-Contains $freshCalls 'auth login auth login --format json'
    Assert-NotContains $freshCalls '--recommend'

    $retryState = Join-Path $testRoot 'retry'
    New-Item -ItemType Directory -Path $retryState | Out-Null
    New-Item -ItemType File -Path (Join-Path $retryState 'authenticated') | Out-Null
    $retryOutput = Invoke-AuthTest 'login' $retryState
    Assert-Contains $retryOutput '"status":"ok"'
    if (Test-Path -LiteralPath (Join-Path $retryState 'calls')) {
        throw 'An existing login must not start operation-specific permission authorization.'
    }

    $failedState = Join-Path $testRoot 'failed'
    New-Item -ItemType Directory -Path $failedState | Out-Null
    $env:DWS_MOCK_LOGIN_FAIL = '1'
    $failedOutput = Invoke-AuthTest 'login' $failedState
    Assert-Contains $failedOutput '"status":"error"'
    Assert-Contains $failedOutput 'browser authorization did not complete'
    if (Test-Path -LiteralPath (Join-Path $failedState 'authenticated')) {
        throw 'A failed OAuth login must not be reported as authenticated.'
    }
    Remove-Item Env:DWS_MOCK_LOGIN_FAIL

    $readyState = Join-Path $testRoot 'ready'
    New-Item -ItemType Directory -Path $readyState | Out-Null
    $env:DWS_MOCK_STATE_DIR = $readyState
    $env:DWS_BINARY_PATH = $mockDws
    $powershell = Join-Path $PSHOME 'powershell.exe'
    $readyOutput = (& $powershell -NoProfile -ExecutionPolicy Bypass `
        -File $readyScript) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "ensure-dws-ready.ps1 exited with code $LASTEXITCODE."
    }
    Assert-Contains $readyOutput 'DWS is installed and authenticated.'
    $readyCalls = Get-Content -LiteralPath (Join-Path $readyState 'calls') -Raw
    Assert-Contains $readyCalls 'auth login auth login --format json'
    Assert-NotContains $readyCalls '--recommend'

    Write-Host 'DingTalk Windows local authorization tests passed.'
} finally {
    Remove-Item Env:DWS_MOCK_STATE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_LOGIN_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:WEGENT_LOCAL_AUTH_TOOL -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_BINARY_PATH -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testRoot -Recurse -Force `
        -ErrorAction SilentlyContinue
}
