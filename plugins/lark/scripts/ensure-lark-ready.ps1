param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'invoke-native-command.ps1')
$result = -1
Invoke-NativeCommand -Command {
    & (Join-Path $PSScriptRoot 'run-python.ps1') (Join-Path $PSScriptRoot 'lark_cli.py') '--ready'
} -ExitCode ([ref]$result)
exit $result
