param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$DwsArguments
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$dws = & (Join-Path $scriptDirectory 'install-dws.ps1') -PrintPath |
    Select-Object -Last 1
& $dws @DwsArguments
exit $LASTEXITCODE

