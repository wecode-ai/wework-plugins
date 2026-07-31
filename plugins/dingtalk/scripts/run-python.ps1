param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ScriptPath,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArguments
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$dws = & (Join-Path $scriptDirectory 'install-dws.ps1') -PrintPath |
    Select-Object -Last 1
$env:PATH = "$(Split-Path -Parent $dws);$env:PATH"

$python = Get-Command python3 -ErrorAction SilentlyContinue
if ($null -eq $python) {
    $python = Get-Command python -ErrorAction SilentlyContinue
}
if ($null -eq $python) {
    throw 'Python 3 is required to run this DWS helper script.'
}
& $python.Source $ScriptPath @ScriptArguments
exit $LASTEXITCODE

