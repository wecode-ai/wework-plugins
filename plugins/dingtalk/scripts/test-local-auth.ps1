$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$authScript = Join-Path $scriptDirectory 'local-auth.ps1'
$readyScript = Join-Path $scriptDirectory 'ensure-dws-ready.ps1'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    ('wegent-dingtalk-auth-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$preexistingAuthTempFiles = @(Get-ChildItem -LiteralPath `
    ([System.IO.Path]::GetTempPath()) -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -like 'wegent-dws-status-*' -or
        $_.Name -like 'wegent-dws-login-*'
    } |
    Select-Object -ExpandProperty FullName)

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

function Invoke-AuthTest(
    [string]$Action,
    [string]$StateDirectory,
    [int]$WaitAttempts = 10,
    [int]$LoginWaitAttempts = 100,
    [int]$WaitDelayMilliseconds = 10,
    [int]$StatusTimeoutMilliseconds = 200
) {
    $env:DWS_MOCK_STATE_DIR = $StateDirectory
    $env:WEGENT_LOCAL_AUTH_TOOL = $mockDws
    $powershell = Join-Path $PSHOME 'powershell.exe'
    $output = (& $powershell -NoProfile -ExecutionPolicy Bypass `
        -File $authScript $Action `
        -WaitAttempts $WaitAttempts `
        -LoginWaitAttempts $LoginWaitAttempts `
        -WaitDelayMilliseconds $WaitDelayMilliseconds `
        -StatusTimeoutMilliseconds $StatusTimeoutMilliseconds) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "local-auth.ps1 exited with code $LASTEXITCODE."
    }
    return $output
}

try {
    $mockDws = Join-Path $testRoot 'dws.cmd'
@'
@echo off
chcp 65001 >nul
if "%~1"=="version" goto version
if "%~1"=="auth" if "%~2"=="status" goto status
if "%~1"=="auth" if "%~2"=="login" goto login
if "%~1"=="auth" if "%~2"=="logout" goto logout
if "%~1"=="contact" if "%~2"=="user" goto contact
exit /b 4

:version
echo dws version 1.0.58
exit /b 0

:status
if "%DWS_MOCK_STATUS_HANG%"=="1" goto hang
if not "%DWS_MOCK_UTF8_STATUS_EMITTER%"=="" (
  "%DWS_MOCK_UTF8_STATUS_EMITTER%"
  exit /b 0
)
if "%DWS_MOCK_STATUS_ALWAYS_INVALID%"=="1" (
  echo authorization-pending-secret-value
  exit /b 0
)
if exist "%DWS_MOCK_STATE_DIR%\authenticated" if "%DWS_MOCK_STATUS_INVALID_AFTER_LOGIN%"=="1" if not exist "%DWS_MOCK_STATE_DIR%\invalid-json-seen" (
  type nul >"%DWS_MOCK_STATE_DIR%\invalid-json-seen"
  echo authorization-pending
  exit /b 0
)
if exist "%DWS_MOCK_STATE_DIR%\authenticated" if "%DWS_MOCK_STATUS_MISSING_FIELD_AFTER_LOGIN%"=="1" if not exist "%DWS_MOCK_STATE_DIR%\missing-field-seen" (
  type nul >"%DWS_MOCK_STATE_DIR%\missing-field-seen"
  echo {"success":true}
  exit /b 0
)
if "%DWS_MOCK_STATUS_REASON%"=="1" (
  echo {"authenticated":false,"reason":"token_refresh_failed","message":"Token refresh failed"}
  exit /b 0
)
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
if "%DWS_MOCK_LOGIN_NO_PERSIST%"=="1" exit /b 0
type nul >"%DWS_MOCK_STATE_DIR%\authenticated"
if "%DWS_MOCK_LOGIN_EXIT_AFTER_AUTH%"=="1" exit /b 4
if "%DWS_MOCK_LOGIN_HANG_AFTER_AUTH%"=="1" (
  echo {"success":true,"token_valid":true,"refresh_token_valid":true}
  goto hang
)
exit /b 0

:hang
goto hang

:logout
echo Removing local login... 1>&2
del /q "%DWS_MOCK_STATE_DIR%\authenticated" 2>nul
exit /b 0

:contact
if exist "%DWS_MOCK_STATE_DIR%\authenticated" exit /b 0
echo not_authenticated 1>&2
exit /b 4
'@ | Set-Content -LiteralPath $mockDws -Encoding Ascii

    $utf8StatusEmitter = Join-Path $testRoot 'utf8-status.exe'
    Add-Type -Language CSharp -OutputType ConsoleApplication `
        -OutputAssembly $utf8StatusEmitter -TypeDefinition @'
using System;
using System.Text;

public static class Utf8Status
{
    public static void Main()
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.Write("{\"authenticated\":true,\"user_name\":\"\u9648\u4fca\u9f99\"}");
    }
}
'@

    $utf8State = Join-Path $testRoot 'utf8-status'
    New-Item -ItemType Directory -Path $utf8State | Out-Null
    $env:DWS_MOCK_UTF8_STATUS_EMITTER = $utf8StatusEmitter
    $utf8Output = Invoke-AuthTest 'health' $utf8State `
        -StatusTimeoutMilliseconds 5000
    Assert-Contains $utf8Output '"status":"ok"'
    Remove-Item Env:DWS_MOCK_UTF8_STATUS_EMITTER

    $freshState = Join-Path $testRoot 'fresh'
    New-Item -ItemType Directory -Path $freshState | Out-Null
    $freshOutput = Invoke-AuthTest 'login' $freshState
    Assert-Contains $freshOutput '"status":"ok"'
    $freshCalls = Get-Content -LiteralPath (Join-Path $freshState 'calls') -Raw
    Assert-Contains $freshCalls 'auth login auth login --format json'
    Assert-NotContains $freshCalls '--recommend'

    $nonzeroAuthState = Join-Path $testRoot 'nonzero-auth'
    New-Item -ItemType Directory -Path $nonzeroAuthState | Out-Null
    $env:DWS_MOCK_LOGIN_EXIT_AFTER_AUTH = '1'
    $nonzeroAuthOutput = Invoke-AuthTest 'login' $nonzeroAuthState
    Assert-Contains $nonzeroAuthOutput '"status":"ok"'
    if (-not (Test-Path -LiteralPath (Join-Path $nonzeroAuthState 'authenticated'))) {
        throw 'A persisted OAuth login must win over the native process exit code.'
    }
    Remove-Item Env:DWS_MOCK_LOGIN_EXIT_AFTER_AUTH

    $hangingAuthState = Join-Path $testRoot 'hanging-after-auth'
    New-Item -ItemType Directory -Path $hangingAuthState | Out-Null
    $env:DWS_MOCK_LOGIN_HANG_AFTER_AUTH = '1'
    $hangingStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $hangingAuthOutput = Invoke-AuthTest 'login' $hangingAuthState
    $hangingStopwatch.Stop()
    Assert-Contains $hangingAuthOutput '"status":"ok"'
    if ($hangingStopwatch.Elapsed.TotalSeconds -ge 5) {
        throw 'Persisted OAuth must not wait for the login process to exit.'
    }
    Remove-Item Env:DWS_MOCK_LOGIN_HANG_AFTER_AUTH

    $hangingStatusState = Join-Path $testRoot 'hanging-status'
    New-Item -ItemType Directory -Path $hangingStatusState | Out-Null
    $env:DWS_MOCK_STATUS_HANG = '1'
    $statusStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $hangingStatusOutput = Invoke-AuthTest 'health' $hangingStatusState `
        -StatusTimeoutMilliseconds 100
    $statusStopwatch.Stop()
    Assert-Contains $hangingStatusOutput '"status":"need_login"'
    if ($statusStopwatch.Elapsed.TotalSeconds -ge 3) {
        throw 'A blocked DWS status command must be terminated promptly.'
    }
    Remove-Item Env:DWS_MOCK_STATUS_HANG

    $invalidJsonState = Join-Path $testRoot 'transient-invalid-json'
    New-Item -ItemType Directory -Path $invalidJsonState | Out-Null
    $env:DWS_MOCK_STATUS_INVALID_AFTER_LOGIN = '1'
    $invalidJsonOutput = Invoke-AuthTest 'login' $invalidJsonState
    Assert-Contains $invalidJsonOutput '"status":"ok"'
    if (-not (Test-Path -LiteralPath (Join-Path $invalidJsonState 'invalid-json-seen'))) {
        throw 'The test must exercise transient invalid DWS status output.'
    }
    Remove-Item Env:DWS_MOCK_STATUS_INVALID_AFTER_LOGIN

    $missingFieldState = Join-Path $testRoot 'transient-missing-field'
    New-Item -ItemType Directory -Path $missingFieldState | Out-Null
    $env:DWS_MOCK_STATUS_MISSING_FIELD_AFTER_LOGIN = '1'
    $missingFieldOutput = Invoke-AuthTest 'login' $missingFieldState
    Assert-Contains $missingFieldOutput '"status":"ok"'
    if (-not (Test-Path -LiteralPath (Join-Path $missingFieldState 'missing-field-seen'))) {
        throw 'The test must exercise status JSON without authenticated.'
    }
    Remove-Item Env:DWS_MOCK_STATUS_MISSING_FIELD_AFTER_LOGIN

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
    Assert-Contains $failedOutput 'DWS auth status reported authenticated=false'
    if (Test-Path -LiteralPath (Join-Path $failedState 'authenticated')) {
        throw 'A failed OAuth login must not be reported as authenticated.'
    }
    Remove-Item Env:DWS_MOCK_LOGIN_FAIL

    $diagnosticState = Join-Path $testRoot 'diagnostic'
    New-Item -ItemType Directory -Path $diagnosticState | Out-Null
    $env:DWS_MOCK_LOGIN_NO_PERSIST = '1'
    $env:DWS_MOCK_STATUS_REASON = '1'
    $diagnosticOutput = Invoke-AuthTest 'login' $diagnosticState
    Assert-Contains $diagnosticOutput '"status":"error"'
    Assert-Contains $diagnosticOutput 'reason=token_refresh_failed'
    Assert-NotContains $diagnosticOutput 'Token refresh failed'
    Remove-Item Env:DWS_MOCK_LOGIN_NO_PERSIST
    Remove-Item Env:DWS_MOCK_STATUS_REASON

    $invalidDiagnosticState = Join-Path $testRoot 'invalid-json-diagnostic'
    New-Item -ItemType Directory -Path $invalidDiagnosticState | Out-Null
    $env:DWS_MOCK_LOGIN_NO_PERSIST = '1'
    $env:DWS_MOCK_STATUS_ALWAYS_INVALID = '1'
    $invalidDiagnosticOutput = Invoke-AuthTest 'login' $invalidDiagnosticState
    Assert-Contains $invalidDiagnosticOutput 'returned invalid JSON (length='
    Assert-NotContains $invalidDiagnosticOutput 'secret-value'
    Remove-Item Env:DWS_MOCK_LOGIN_NO_PERSIST
    Remove-Item Env:DWS_MOCK_STATUS_ALWAYS_INVALID

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

    $nonzeroReadyState = Join-Path $testRoot 'nonzero-ready'
    New-Item -ItemType Directory -Path $nonzeroReadyState | Out-Null
    $env:DWS_MOCK_STATE_DIR = $nonzeroReadyState
    $env:DWS_MOCK_LOGIN_EXIT_AFTER_AUTH = '1'
    $nonzeroReadyOutput = (& $powershell -NoProfile -ExecutionPolicy Bypass `
        -File $readyScript) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "ensure-dws-ready.ps1 exited with code $LASTEXITCODE after persisting login."
    }
    Assert-Contains $nonzeroReadyOutput 'DWS is installed and authenticated.'
    Remove-Item Env:DWS_MOCK_LOGIN_EXIT_AFTER_AUTH

    $newAuthTempFiles = @(Get-ChildItem -LiteralPath `
        ([System.IO.Path]::GetTempPath()) -File -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -like 'wegent-dws-status-*' -or
                $_.Name -like 'wegent-dws-login-*') -and
            $_.FullName -notin $preexistingAuthTempFiles
        })
    if ($newAuthTempFiles.Count -ne 0) {
        throw 'Local authorization tests left temporary DWS output files.'
    }

    Write-Host 'DingTalk Windows local authorization tests passed.'
} finally {
    Remove-Item Env:DWS_MOCK_STATE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_LOGIN_FAIL -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_LOGIN_EXIT_AFTER_AUTH -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_LOGIN_HANG_AFTER_AUTH -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_LOGIN_NO_PERSIST -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_STATUS_REASON -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_STATUS_HANG -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_STATUS_ALWAYS_INVALID -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_STATUS_INVALID_AFTER_LOGIN -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_STATUS_MISSING_FIELD_AFTER_LOGIN -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_MOCK_UTF8_STATUS_EMITTER -ErrorAction SilentlyContinue
    Remove-Item Env:WEGENT_LOCAL_AUTH_TOOL -ErrorAction SilentlyContinue
    Remove-Item Env:DWS_BINARY_PATH -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testRoot -Recurse -Force `
        -ErrorAction SilentlyContinue
}
