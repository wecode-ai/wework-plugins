param(
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArguments
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'invoke-native-command.ps1')
$python = Get-Command python, py, python3 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $python) { throw 'Python 3.9 or newer is required for this plugin.' }
$arguments = @()
if ($python.Name -match '^py(\.exe)?$') { $arguments += '-3' }
$arguments += $ScriptPath
$arguments += $ScriptArguments
$result = -1
Invoke-NativeCommand -Command { & $python.Source @arguments } -ExitCode ([ref]$result)
exit $result
