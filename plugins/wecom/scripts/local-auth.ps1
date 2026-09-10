param([ValidateSet('health', 'login', 'logout')][string]$Action = 'health')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'invoke-native-command.ps1')
$result = -1
Invoke-NativeCommand -Command {
    & (Join-Path $PSScriptRoot 'run-python.ps1') (Join-Path $PSScriptRoot 'local_auth.py') $Action
} -ExitCode ([ref]$result)
exit $result
