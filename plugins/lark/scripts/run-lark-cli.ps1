param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LarkArguments
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$lark = & (Join-Path $scriptDirectory 'install-lark-cli.ps1') |
    Select-Object -Last 1
foreach ($name in @(
    'LARKSUITE_CLI_USER_ACCESS_TOKEN',
    'LARKSUITE_CLI_BRAND',
    'LARKSUITE_CLI_APP_ID'
)) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$env:LARKSUITE_CLI_NO_UPDATE_NOTIFIER = '1'
$env:LARKSUITE_CLI_NO_SKILLS_NOTIFIER = '1'
& $lark @LarkArguments
exit $LASTEXITCODE
