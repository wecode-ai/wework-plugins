param(
    [ValidateSet('health', 'login', 'logout')]
    [string]$Action = 'health',

    [ValidateRange(1, 600)]
    [int]$WaitAttempts = 60,

    [ValidateRange(1, 1200)]
    [int]$LoginWaitAttempts = 480,

    [ValidateRange(0, 5000)]
    [int]$WaitDelayMilliseconds = 500,

    [ValidateRange(100, 30000)]
    [int]$StatusTimeoutMilliseconds = 5000
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

function Remove-AuthTemporaryFiles([string[]]$Paths) {
    foreach ($path in $Paths) {
        for ($attempt = 1; $attempt -le 10; $attempt++) {
            try {
                [System.IO.File]::Delete($path)
                break
            } catch {
                if ($attempt -lt 10) {
                    Start-Sleep -Milliseconds 50
                }
            }
        }
    }
}

function Invoke-StatusCommand {
    $outputRoot = [System.IO.Path]::GetTempPath()
    $outputId = [Guid]::NewGuid().ToString('N')
    $stdoutPath = Join-Path $outputRoot "wegent-dws-status-$outputId.stdout"
    $stderrPath = Join-Path $outputRoot "wegent-dws-status-$outputId.stderr"
    $process = $null
    try {
        $process = Start-Process -FilePath $dws `
            -ArgumentList @('auth', 'status', '--format', 'json') `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden `
            -PassThru
        if (-not $process.WaitForExit($StatusTimeoutMilliseconds)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $null = $process.WaitForExit(2000)
            return [PSCustomObject]@{
                ExitCode = $null
                Output = ''
                TimedOut = $true
                StartFailed = $false
            }
        }
        $process.WaitForExit()
        # DWS always emits UTF-8 JSON. Windows PowerShell 5.1 otherwise reads
        # redirected files with the active ANSI code page, which can consume a
        # JSON quote when a multibyte Chinese value ends immediately before it.
        $output = Get-Content -LiteralPath $stdoutPath -Raw -Encoding UTF8 `
            -ErrorAction SilentlyContinue
        return [PSCustomObject]@{
            ExitCode = $process.ExitCode
            Output = $output
            TimedOut = $false
            StartFailed = $false
        }
    } catch {
        return [PSCustomObject]@{
            ExitCode = $null
            Output = ''
            TimedOut = $false
            StartFailed = $true
        }
    } finally {
        if ($null -ne $process) {
            try {
                $process.Dispose()
            } catch {
                # The executor may have already terminated the child process.
            }
        }
        Remove-AuthTemporaryFiles @($stdoutPath, $stderrPath)
    }
}

function Get-AuthenticationState {
    $result = Invoke-StatusCommand
    if ($result.TimedOut) {
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = 'DWS auth status timed out.'
        }
    }
    if ($result.StartFailed) {
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = 'Unable to start DWS auth status.'
        }
    }
    # Start-Process can omit ExitCode for command-script wrappers on Windows.
    # A missing code is not a failure when stdout is still available to parse.
    if ($null -ne $result.ExitCode -and $result.ExitCode -ne 0) {
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
    } catch {
        $statusLength = $statusText.Length
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = "DWS auth status returned invalid JSON (length=$statusLength)."
        }
    }
    if ($null -eq $status -or
        $null -eq $status.PSObject.Properties['authenticated']) {
        return [PSCustomObject]@{
            Authenticated = $false
            Hint = 'DWS auth status JSON did not include authenticated.'
        }
    }
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
}

function Test-Authenticated {
    return (Get-AuthenticationState).Authenticated
}

function Start-Login {
    # Installation authentication ends with the OAuth callback. Recommended
    # PAT permissions are operation-specific and must not keep this dialog open.
    $outputRoot = [System.IO.Path]::GetTempPath()
    $outputId = [Guid]::NewGuid().ToString('N')
    $stdoutPath = Join-Path $outputRoot "wegent-dws-login-$outputId.stdout"
    $stderrPath = Join-Path $outputRoot "wegent-dws-login-$outputId.stderr"
    try {
        $process = Start-Process -FilePath $dws `
            -ArgumentList @('auth', 'login', '--format', 'json') `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden `
            -PassThru
        return [PSCustomObject]@{
            Started = $true
            Process = $process
            StdoutPath = $stdoutPath
            StderrPath = $stderrPath
            Hint = ''
        }
    } catch {
        Remove-AuthTemporaryFiles @($stdoutPath, $stderrPath)
        return [PSCustomObject]@{
            Started = $false
            Process = $null
            StdoutPath = ''
            StderrPath = ''
            Hint = 'Unable to start the DingTalk OAuth command.'
        }
    }
}

function Complete-Login([PSCustomObject]$Login) {
    $wasRunning = $false
    $exitCode = $null
    try {
        $Login.Process.Refresh()
        if ($Login.Process.HasExited) {
            $exitCode = $Login.Process.ExitCode
        } else {
            $wasRunning = $true
            Stop-Process -Id $Login.Process.Id -Force -ErrorAction SilentlyContinue
            $null = $Login.Process.WaitForExit(2000)
        }
    } catch {
        $wasRunning = $true
    } finally {
        try {
            $Login.Process.Dispose()
        } catch {
            # The executor may have already terminated the child process.
        }
        Remove-AuthTemporaryFiles @($Login.StdoutPath, $Login.StderrPath)
    }
    return [PSCustomObject]@{
        WasRunning = $wasRunning
        ExitCode = $exitCode
    }
}

function Get-LoginSignal([PSCustomObject]$Login) {
    try {
        if (Test-Path -LiteralPath $Login.StdoutPath -PathType Leaf) {
            $loginText = Get-Content -LiteralPath $Login.StdoutPath -Raw `
                -Encoding UTF8 `
                -ErrorAction SilentlyContinue
            if (-not [string]::IsNullOrWhiteSpace($loginText)) {
                try {
                    $loginStatus = $loginText | ConvertFrom-Json
                    if ($null -ne $loginStatus -and
                        $null -ne $loginStatus.PSObject.Properties['success'] -and
                        $loginStatus.success -eq $true) {
                        return 'succeeded'
                    }
                } catch {
                    # DWS may still be writing the JSON document.
                }
            }
        }
        $Login.Process.Refresh()
        if ($Login.Process.HasExited) {
            return 'exited'
        }
    } catch {
        return 'exited'
    }
    return 'waiting'
}

function Wait-LoginSignal {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Login,

        [Parameter(Mandatory = $true)]
        [int]$Attempts,

        [Parameter(Mandatory = $true)]
        [int]$DelayMilliseconds
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $signal = Get-LoginSignal $Login
        if ($signal -ne 'waiting') {
            return $signal
        }
        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
    return 'timeout'
}

function Wait-Authenticated {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Attempts,

        [Parameter(Mandatory = $true)]
        [int]$DelayMilliseconds
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
        $authState = Get-AuthenticationState
        if ($authState.Authenticated) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
        } else {
            Write-Status 'need_login' $authState.Hint
        }
    }
    'login' {
        if (Test-Authenticated) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
            exit 0
        }
        $login = Start-Login
        if (-not $login.Started) {
            Write-Status 'error' $login.Hint
            exit 0
        }
        # DWS may persist credentials and print success while its process still
        # holds the credential-store lock. Observe stdout first, stop the login
        # process, then verify stored state after the lock has been released.
        $loginSignal = Wait-LoginSignal `
            -Login $login `
            -Attempts $LoginWaitAttempts `
            -DelayMilliseconds $WaitDelayMilliseconds
        $loginResult = Complete-Login $login
        $authState = Wait-Authenticated `
            -Attempts $WaitAttempts `
            -DelayMilliseconds $WaitDelayMilliseconds
        if ($authState.Authenticated) {
            Write-Status 'ok' 'DingTalk authorization is ready.'
        } elseif ($null -ne $loginResult.ExitCode -and $loginResult.ExitCode -ne 0) {
            Write-Status 'error' "DingTalk OAuth command failed (exit code $($loginResult.ExitCode)); $($authState.Hint)"
        } elseif ($loginSignal -eq 'timeout' -or $loginResult.WasRunning) {
            Write-Status 'error' "DingTalk OAuth command timed out; $($authState.Hint)"
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
