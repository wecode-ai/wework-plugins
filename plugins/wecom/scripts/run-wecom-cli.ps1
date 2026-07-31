param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$WeComArguments
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$wecom = & (Join-Path $scriptDirectory 'install-wecom-cli.ps1') -PrintPath |
    Select-Object -Last 1
foreach ($name in @('WECOM_ACCESS_TOKEN', 'WECOM_BOT_ID', 'WECOM_SECRET')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
& $wecom @WeComArguments
exit $LASTEXITCODE

